#!/usr/bin/env python3
"""Import and report local workbench tasks, or wrap an authorized command."""
import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit


def request(base, route, payload=None):
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.path not in ('', '/') or parsed.username or parsed.password:
        raise ValueError('地址必须为本机工作台 http://localhost:端口')
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(base.rstrip('/') + route, data=body, headers={'Content-Type': 'application/json'})
    # Do not forward local task reports through configured HTTP proxies.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:4311')
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('import')
    create.add_argument('--source', required=True); create.add_argument('--external-id', required=True); create.add_argument('--prompt', required=True)
    inspect = sub.add_parser('inspect'); inspect.add_argument('id')
    report = sub.add_parser('report'); report.add_argument('id'); report.add_argument('--sequence', required=True, type=int)
    report.add_argument('--status', required=True, choices=['running', 'completed', 'failed', 'cancelled', 'blocked'])
    report.add_argument('--step', required=True); report.add_argument('--result', default=''); report.add_argument('--tool')
    run = sub.add_parser('run')
    run.add_argument('--source', required=True); run.add_argument('--external-id', required=True); run.add_argument('--prompt', required=True)
    run.add_argument('--timeout', type=int, default=600); run.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'inspect': result = request(args.url, '/api/external/tasks/' + args.id)
    elif args.action == 'report':
        payload = {key: getattr(args, key) for key in ['sequence', 'status', 'step', 'result']}
        if args.tool: payload['tool'] = args.tool
        result = request(args.url, '/api/external/tasks/' + args.id + '/reports', payload)
    else:
        command = args.command if args.action == 'run' else None
        if command and command[0] == '--': command = command[1:]
        if args.action == 'run' and (not command or args.timeout < 1): parser.error('run 需要正数超时和 -- 后的命令参数')
        result = request(args.url, '/api/external/tasks', {key: getattr(args, key) for key in ['source', 'external_id', 'prompt']})
        if args.action == 'run':
            if result['sequence'] or result['run']['status'] != 'queued': raise ValueError('该任务已经开始；不会自动重复执行命令，请 inspect 后核对')
            route = '/api/external/tasks/' + result['id'] + '/reports'
            claim = request(args.url, route, {'sequence': 1, 'status': 'running', 'step': '正在执行 ' + command[0]})
            if claim.get('duplicate'): raise ValueError('任务已被其他执行器认领；不会重复执行')
            status = 'failed'
            with tempfile.TemporaryFile() as output:
                try:
                    process = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=args.timeout)
                    status = 'completed' if process.returncode == 0 else 'failed'
                    reason = '退出码：' + str(process.returncode)
                except (OSError, subprocess.TimeoutExpired) as exc: reason = str(exc)
                size = output.tell();output.seek(max(0, size - 12000))
                text = output.read().decode('utf-8', errors='replace')
            result = request(args.url, route, {'sequence': 2, 'status': status, 'step': '外部命令执行结束', 'tool': command[0][:100], 'result': reason + '\n' + ('（仅末尾输出）\n' if size > 12000 else '') + text})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.action == 'run' and result['run']['status'] == 'failed' else 0


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr);sys.exit(1)
