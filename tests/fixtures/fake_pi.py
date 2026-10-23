#!/usr/bin/env python3
"""Deterministic RPC peer for protocol/lifecycle tests; no model/network calls."""
import json
import os
import sys
import time
from pathlib import Path


def emit(record):
    print(json.dumps(record, ensure_ascii=False), flush=True)


def dialog(request):
    emit(request)
    for line in sys.stdin.buffer:
        answer = json.loads(line)
        if answer.get('type') == 'abort':
            raise SystemExit(0)
        if answer.get('type') == 'extension_ui_response' and answer.get('id') == request['id']:
            return answer


def finish(text, error=None, report=True):
    if report and not error and os.environ.get('WORKBENCH_CHILD') != '1':
        emit({'type': 'extension_ui_request', 'method': 'notify', 'message': '__workbench_finish__' + json.dumps({'summary': text})})
    emit({'type': 'message_start', 'message': {'role': 'assistant'}})
    emit({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': text}})
    emit({'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': text}], 'stopReason': 'error' if error else 'stop', 'errorMessage': error, 'usage': {'input': 2, 'output': 1, 'totalTokens': 3}}})
    emit({'type': 'agent_end', 'willRetry': False})
    emit({'type': 'agent_settled', 'aborted': False})


for line in sys.stdin.buffer:
    command = json.loads(line)
    kind = command['type']
    data = {}
    if kind == 'get_commands':
        data = {'commands': [{'name': 'workbench-status'}]}
    elif kind == 'get_state':
        data = {'sessionId': sys.argv[sys.argv.index('--session-id')+1]}
    elif kind == 'prompt':
        emit({'type': 'response', 'id': command['id'], 'command': kind, 'success': True, 'data': {'disposition': 'started'}})
        emit({'type': 'agent_start'})
        prompt = command['message']
        if os.environ.get('WORKBENCH_MODE')=='chat':
            if '[create-task]' in prompt:
                dialog({'type':'extension_ui_request','method':'input','id':'create-task','title':'__workbench_create_task__','placeholder':json.dumps({'prompt':'基于前面对话制定求职计划'})})
            if '[create-agent]' in prompt:
                dialog({'type':'extension_ui_request','method':'input','id':'create-agent','title':'__workbench_create_agent__','placeholder':json.dumps({'name':'interview-mentor','description':'面试辅导','prompt':'模拟面试并提供反馈','tools':['read']})})
            finish('# 阅读笔记\n\n**贡献**与 `指标`\n\n| 实验 | 样本量 |\n| --- | --- |\n| A | 120 |\n\n```python\nprint(120)\n```\n\n[ \\kappa = \\frac{1}{R} ]' if '[markdown]' in prompt else '这是一段普通回答')
            continue
        if '[exit]' in prompt:
            raise SystemExit(0)
        if '[block]' in prompt:
            while True:
                time.sleep(.1)
        if '[parallel]' in prompt:
            child_task = '[ask]' if '[askchildren]' in prompt else '[write]' if '[approvals]' in prompt else '[fail]' if '[childfail]' in prompt else '调查代码'
            tasks = [{'agent': 'interview-coach' if '[custom]' in prompt else 'task-worker', 'task': child_task}, {'agent': 'task-reviewer', 'task': child_task}]
            answer = dialog({'type': 'extension_ui_request', 'method': 'input', 'id': 'delegate-1', 'title': '__workbench_delegate__', 'placeholder': json.dumps({'tasks': tasks})})
            result = json.loads(answer['value'])
            emit({'type': 'tool_execution_end', 'toolCallId': 'delegate-tool', 'toolName': 'workbench_delegate', 'result': {'content': [{'type': 'text', 'text': json.dumps(result)}]}})
        if '[ask]' in prompt:
            answer = dialog({'type': 'extension_ui_request', 'method': 'input', 'id': 'ask-1', 'title': '__workbench_ask__', 'placeholder': json.dumps({'question': '请提供简历或工作经历', 'options': ['应届毕业生', '已有工作经验']})})
            if not answer.get('value'): raise SystemExit(1)
        if '[write]' in prompt:
            emit({'type': 'tool_execution_start', 'toolCallId': 'write-1', 'toolName': 'write', 'args': {'path': 'result.txt', 'content': 'done'}})
            answer = dialog({'type': 'extension_ui_request', 'method': 'confirm', 'id': 'approval-1', 'title': '工作台工具审批', 'message': json.dumps({'name': 'write', 'arguments': {'path': 'result.txt', 'content': 'done'}})})
            if answer.get('confirmed'):
                Path('result.txt').write_text('done')
            emit({'type': 'tool_execution_end', 'toolCallId': 'write-1', 'toolName': 'write', 'isError': not answer.get('confirmed'), 'result': {'details': {'diff': '+done'}}})
        if '[retry]' in prompt:
            emit({'type': 'agent_end', 'willRetry': True})
            gate = Path(os.environ['FAKE_PI_GATE'])
            while not gate.exists():
                time.sleep(.01)
        finish('已完成\u2028可观察结果', '子任务失败' if '[fail]' in prompt else None, report='[unreported]' not in prompt)
        continue
    elif kind == 'abort':
        break
    emit({'type': 'response', 'id': command.get('id'), 'command': kind, 'success': True, 'data': data})
