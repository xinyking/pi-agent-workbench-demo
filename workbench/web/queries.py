import json
import re
import time
from urllib.parse import urlparse, parse_qs, quote
from .common import ID, STATIC, STREAM_TERMINAL

class QueryRoutes:

    def do_GET(self):
        if not self.valid_host():
            return self.reply({'error': '只允许本机访问'}, 403)
        url = urlparse(self.path)
        if url.path == '/api/health':
            return self.reply({'status': 'ok', 'service': 'python-agent-workbench'})
        if url.path == '/api/memory':
            return self.reply(self.runtime.memory.catalog())
        if url.path == '/api/settings/models':
            return self.reply(self.runtime.models.public())
        if url.path == '/api/executors':
            return self.reply(self.runtime.executors.capabilities())
        if url.path == '/api/config':
            return self.reply(self.runtime.config.public())
        if url.path == '/api/board':
            return self.reply(self.runtime.board())
        if url.path == '/api/conversations':
            return self.reply({'conversations': self.runtime.conversations.list()})
        chat_parts = url.path.strip('/').split('/')
        if len(chat_parts) in (3, 4, 5, 6) and chat_parts[:2] == ['api', 'conversations'] and ID.fullmatch(chat_parts[2]):
            try:
                chat = self.runtime.conversations.get(chat_parts[2])
            except ValueError as exc:
                return self.reply({'error': str(exc)}, 404)
            if len(chat_parts) == 3:
                return self.reply(chat)
            if chat_parts[3] == 'documents':
                try:
                    if len(chat_parts) == 4:
                        return self.reply({'documents': self.runtime.documents.list(chat['id'])})
                    document_id = chat_parts[4]
                    if len(chat_parts) == 6 and chat_parts[5] == 'download':
                        source, item = self.runtime.documents.source(chat['id'], document_id)
                        body = source.read_bytes()
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Disposition', 'attachment; filename=source' + item['kind'] + "; filename*=UTF-8''" + quote(item['name']))
                        self.send_header('Content-Length', str(len(body)))
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if len(chat_parts) == 6 and chat_parts[5] == 'outline':
                        return self.reply(self.runtime.documents.outline(chat['id'], document_id))
                    if len(chat_parts) == 6 and chat_parts[5] == 'search':
                        return self.reply(self.runtime.documents.search(chat['id'], document_id, parse_qs(url.query).get('q', [''])[0]))
                    if len(chat_parts) == 5:
                        return self.reply(self.runtime.documents.read(chat['id'], document_id, int(parse_qs(url.query).get('page', ['1'])[0]), int(parse_qs(url.query).get('offset', ['0'])[0])))
                except (ValueError, OSError) as exc:
                    return self.reply({'error': str(exc)}, 400)
            if chat_parts[3] == 'events':
                try:
                    after = int(self.headers.get('Last-Event-ID') or parse_qs(url.query).get('after', ['0'])[0])
                except ValueError:
                    return self.reply({'error': '无效事件编号'}, 400)
                if after < 0:
                    return self.reply({'error': '无效事件编号'}, 400)
                return self.conversation_stream(chat['id'], after)
        if url.path == '/api/agents':
            return self.reply({'agents': self.runtime.agents.list()})
        if url.path == '/api/usage':
            return self.reply(self.store.usage())
        if url.path.startswith('/api/external/tasks/'):
            try:
                return self.reply(self.external.get(url.path.rsplit('/', 1)[-1]))
            except ValueError as exc:
                return self.reply({'error': str(exc)}, 404)
        if url.path == '/api/runs':
            return self.reply({'runs': self.store.runs(), 'mode': self.runtime.mode})
        if url.path == '/api/board/events':
            return self.board_stream()
        parts = url.path.strip('/').split('/')
        if len(parts)==6 and parts[:2]==['api','runs'] and ID.fullmatch(parts[2]) and parts[3:5]==['context','artifacts']:
            try:
                return self.reply(self.runtime.task_context.artifact(parts[2],parts[5],int(parse_qs(url.query).get('offset',['0'])[0])))
            except ValueError as exc: return self.reply({'error':str(exc)},400)
        if len(parts) in (5, 6) and parts[:2] == ['api', 'runs'] and ID.fullmatch(parts[2]) and (parts[3] == 'documents'):
            try:
                self.store.run(parts[2])
                if self.runtime.sources.get(parts[2]):
                    payload={'document_id':parts[4],'action':parts[5] if len(parts)==6 else 'read','page':int(parse_qs(url.query).get('page',['1'])[0]),'offset':int(parse_qs(url.query).get('offset',['0'])[0]),'query':parse_qs(url.query).get('q',[''])[0]}
                    return self.reply(self.runtime.sources.tool(parts[2],payload))
                conversation_id = self.runtime.documents.for_task(parts[2])
                if len(parts) == 6:
                    if parts[5] == 'outline':
                        return self.reply(self.runtime.documents.outline(conversation_id, parts[4]))
                    if parts[5] == 'search':
                        return self.reply(self.runtime.documents.search(conversation_id, parts[4], parse_qs(url.query).get('q', [''])[0]))
                    return self.reply({'error': '不存在'}, 404)
                return self.reply(self.runtime.documents.read(conversation_id, parts[4], int(parse_qs(url.query).get('page', ['1'])[0]), int(parse_qs(url.query).get('offset', ['0'])[0])))
            except ValueError as exc:
                return self.reply({'error': str(exc)}, 400)
        if len(parts) in (3, 4) and parts[:2] == ['api', 'runs'] and ID.fullmatch(parts[2]):
            try:
                run = self.store.run(parts[2])
            except ValueError as exc:
                return self.reply({'error': str(exc)}, 404)
            if len(parts) == 3:
                return self.reply({'run': run, 'approvals': self.store.approvals(run['id']), 'agents': self.store.agents(run['id']), 'inputs': self.store.inputs(run['id'])})
            if parts[3] == 'context':
                try: return self.reply(self.runtime.executors.overview(run['id']) if run['provider_id'] in ('codex','claude') else {**self.runtime.task_context.overview(run['id']),'source_packet':self.runtime.sources.get(run['id'])})
                except ValueError as exc: return self.reply({'error': str(exc)}, 400)
            if parts[3] == 'outputs':
                cursor, outputs = (0, [])
                while not getattr(self.runtime, 'closed', False):
                    page = self.store.events(run['id'], cursor)
                    if not page:
                        break
                    for event in page:
                        cursor = event['id']
                        if event['kind'] == 'output.structured':
                            outputs.append(event)
                return self.reply({'outputs': outputs})
            if parts[3] == 'archive':
                from ..archives import read_archive
                try:
                    return self.reply(read_archive(self.store, run, self.runtime.config.workspace(run['workspace'])))
                except (OSError, ValueError) as exc:
                    return self.reply({'error': str(exc)}, 400)
            if parts[3] == 'events':
                try:
                    after = int(self.headers.get('Last-Event-ID') or parse_qs(url.query).get('after', ['0'])[0])
                    if after < 0:
                        raise ValueError()
                except ValueError:
                    return self.reply({'error': '无效事件编号'}, 400)
                return self.run_stream(run['id'], after)
        names = {'/source-form.mjs':'source-form.mjs','/source-packet-view.mjs':'source-packet-view.mjs','/model-discovery.mjs':'model-discovery.mjs','/model-settings.mjs':'model-settings.mjs','/model-choices.mjs':'model-choices.mjs','/executor-form.mjs':'executor-form.mjs','/memory-settings.mjs':'memory-settings.mjs','/task-context.mjs':'task-context.mjs','/board-controller.mjs':'board-controller.mjs','/task-detail-controller.mjs':'task-detail-controller.mjs','/chat-controller.mjs':'chat-controller.mjs','/view-session.mjs':'view-session.mjs','/task-timeline.mjs': 'task-timeline.mjs', '/api.mjs': 'api.mjs', '/settings.mjs': 'settings.mjs', '/preferences.mjs': 'preferences.mjs', '/': 'index.html', '/app.js': 'app.js', '/flow.mjs': 'flow.mjs', '/timeline.mjs': 'timeline.mjs', '/sidebar.mjs': 'sidebar.mjs', '/trace.mjs': 'trace.mjs', '/markdown.mjs': 'markdown.mjs', '/progress.mjs': 'progress.mjs', '/vendor/temml.mjs': 'vendor/temml.mjs', '/vendor/temml.css': 'vendor/temml.css', '/workspace-picker.mjs': 'workspace-picker.mjs', '/vendor/marked.mjs': 'vendor/marked.mjs', '/style.css': 'style.css', '/chat.css': 'chat.css'}
        if url.path not in names:
            return self.reply({'error': '不存在'}, 404)
        file = STATIC / names[url.path]
        body = file.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', {'html': 'text/html', 'js': 'text/javascript', 'mjs': 'text/javascript', 'css': 'text/css'}[file.suffix[1:]] + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
        self.end_headers()
        self.wfile.write(body)
