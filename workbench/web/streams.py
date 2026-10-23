import json
import re
import time
from urllib.parse import urlparse, parse_qs, quote
from .common import ID, STATIC, STREAM_TERMINAL

class EventStreams:

    def stream_headers(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

    def stream_data(self, data, cursor):
        self.wfile.write(f'id: {cursor}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode())
        self.wfile.flush()

    def board_stream(self):
        self.stream_headers()
        cursor = -1
        heartbeat = 0
        try:
            while not getattr(self.runtime, 'closed', False):
                current = self.store.cursor()
                if current != cursor:
                    self.stream_data({'changed': True}, current)
                    cursor = current
                elif time.monotonic() - heartbeat > 10:
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    heartbeat = time.monotonic()
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def run_stream(self, run_id, after):
        self.stream_headers()
        try:
            while not getattr(self.runtime, 'closed', False):
                for event in self.store.events(run_id, after):
                    self.stream_data(event, event['id'])
                    after = event['id']
                status = self.store.run(run_id)['status']
                if status in STREAM_TERMINAL and (not self.store.events(run_id, after)):
                    self.wfile.write(b'event: caught-up\ndata: {}\n\n')
                    self.wfile.flush()
                    break
                self.wfile.write(b': heartbeat\n\n')
                self.wfile.flush()
                time.sleep(0.15)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def conversation_stream(self, conversation_id, after):
        self.stream_headers()
        try:
            while not getattr(self.runtime, 'closed', False):
                for event in self.runtime.conversations.events(conversation_id, after):
                    self.stream_data(event, event['id'])
                    after = event['id']
                chat = self.runtime.conversations.get(conversation_id)
                still_executing = chat['mode'] == 'planning' and chat['task_id'] in self.runtime.active
                if chat['status'] != 'running' and (not still_executing) and (not self.runtime.conversations.events(conversation_id, after)):
                    self.wfile.write(b'event: caught-up\ndata: {}\n\n')
                    self.wfile.flush()
                    break
                self.wfile.write(b': heartbeat\n\n')
                self.wfile.flush()
                time.sleep(0.15)
        except (BrokenPipeError, ConnectionResetError):
            pass
