"""Bind services to an HTTP handler; execution stays in Runtime."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ..external_tasks import ExternalTasks
from .queries import QueryRoutes
from .mutations import MutationRoutes
from .streams import EventStreams

class WorkbenchServer(ThreadingHTTPServer):
    request_queue_size = 64

def make_handler(store, runtime):

    class Handler(QueryRoutes, MutationRoutes, EventStreams, BaseHTTPRequestHandler):

        def reply(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            host = self.headers.get('Host', '').split(':')[0]
            return host in ('localhost', '127.0.0.1')
    Handler.store = store
    Handler.runtime = runtime
    Handler.external = ExternalTasks(store)
    return Handler
