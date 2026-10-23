#!/usr/bin/env python3
"""Deterministic vendor CLI peer; no network or model calls."""
import json
import sys
import time
if '--help' in sys.argv:
    print('--json --sandbox --ephemeral --ignore-user-config --output-schema --safe-mode --no-session-persistence --json-schema --permission-mode --tools --allowedTools --strict-mcp-config --mcp-config --output-format');sys.exit()
body=sys.stdin.read();vendor='codex' if 'exec' in sys.argv else 'claude'
def emit(x):print(json.dumps(x),flush=True)
if 'HANG_TEST' in body:time.sleep(30)
if 'MALFORMED_TEST' in body:print('not json');sys.exit()
if 'EXIT_TEST' in body:sys.exit(2)
result={'status':'blocked' if 'BLOCK_TEST' in body else 'completed','summary':'隔离 CLI 已读取任务上下文并完成检查','checks':['读取输入成功']}
if vendor=='codex':
    emit({'type':'thread.started','thread_id':'fixture-session'})
    emit({'type':'item.started','item':{'id':'tool-1','type':'command_execution','command':'read fixture'}})
    emit({'type':'item.completed','item':{'id':'tool-1','type':'command_execution','status':'completed'}})
    emit({'type':'item.completed','item':{'id':'answer','type':'agent_message','text':json.dumps(result)}})
    if 'NO_FINISH_TEST' not in body:emit({'type':'turn.completed'})
else:
    emit({'type':'system','subtype':'init','session_id':'fixture-session'})
    emit({'type':'assistant','message':{'content':[{'type':'tool_use','id':'tool-1','name':'Read','input':{'file_path':'fixture.md'}}]}})
    emit({'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'tool-1','content':'fixture'}]}})
    emit({'type':'result','subtype':'success','is_error':False,'structured_output':result})
