"""Application startup. Public server imports retained for existing integrations."""
import argparse
import errno
from http.server import BaseHTTPRequestHandler
from .store import Store
from .runtime import Runtime
from .web.server import WorkbenchServer, make_handler


def main():
    parser = argparse.ArgumentParser(description='WSL/Linux 本地 Python Agent 工作台')
    parser.add_argument('--port', type=int, default=4310)
    parser.add_argument('--data', default='.workbench/state.sqlite3')
    args = parser.parse_args()
    # Bind before touching persisted task state. A duplicate launch must not
    # mark the already-running daemon's tasks as interrupted.
    try:
        server = WorkbenchServer(('127.0.0.1', args.port), BaseHTTPRequestHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            parser.exit(1, f'启动失败：端口 {args.port} 已被占用。请检查旧服务，或使用 --port {args.port + 1}。\n')
        if exc.errno in (errno.EACCES, errno.EPERM):
            parser.exit(1, '启动失败：当前执行环境禁止监听网络端口。请在普通 WSL 终端中运行 python3 -m workbench。\n')
        parser.exit(1, f'启动失败：{exc}\n')
    runtime = None
    try:
        store = Store(args.data)
        runtime = Runtime(store)
        server.RequestHandlerClass = make_handler(store, runtime)
        print(f'Agent 工作台已启动：http://127.0.0.1:{server.server_port}', flush=True)
        print(f'Windows 浏览器：http://localhost:{server.server_port}（请保留此 WSL 终端）', flush=True)
        print(f'健康检查：http://127.0.0.1:{server.server_port}/api/health', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if runtime is not None:
            runtime.shutdown()
        server.server_close()

if __name__ == '__main__':
    main()
