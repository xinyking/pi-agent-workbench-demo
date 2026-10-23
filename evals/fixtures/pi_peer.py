#!/usr/bin/env python3
"""Deterministic RPC peer, never a quality benchmark for a real model."""
import json
import os
import re
import sys
import time
from pathlib import Path


def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def dialog(title, payload, method='input'):
    key=str(time.monotonic_ns())
    event={'type':'extension_ui_request','method':method,'id':key,'title':title}
    if method=='confirm':event['message']=json.dumps(payload)
    else:event['placeholder']=json.dumps(payload)
    emit(event)
    for line in sys.stdin:
        value=json.loads(line)
        if value.get('type')=='abort':raise SystemExit(0)
        if value.get('type')=='extension_ui_response' and value.get('id')==key:return value
    raise RuntimeError('Missing extension response')


def write(path, text):
    call='write-'+path
    emit({'type':'tool_execution_start','toolCallId':call,'toolName':'write','args':{'path':path,'content':text}})
    response=dialog('工作台工具审批',{'name':'write','arguments':{'path':path,'content':text}},'confirm')
    if response.get('confirmed'):Path(path).write_text(text)
    emit({'type':'tool_execution_end','toolCallId':call,'toolName':'write','isError':not response.get('confirmed'),'result':{'content':[{'type':'text','text':'written' if response.get('confirmed') else 'denied'}]}})
    if not response.get('confirmed'):raise RuntimeError('Write denied')


def read(path):
    call='read-'+path
    emit({'type':'tool_execution_start','toolCallId':call,'toolName':'read','args':{'path':path}})
    value=Path(path).read_text()
    emit({'type':'tool_execution_end','toolCallId':call,'toolName':'read','result':{'content':[{'type':'text','text':value}]}})
    return value


def delegate(tasks, key):
    emit({'type':'tool_execution_start','toolCallId':key,'toolName':'workbench_delegate','args':{'tasks':tasks}})
    response=dialog('__workbench_delegate__',{'tasks':tasks})
    emit({'type':'tool_execution_end','toolCallId':key,'toolName':'workbench_delegate','result':{'content':[{'type':'text','text':response.get('value','')}]}})


def finish(text):
    if os.environ.get('WORKBENCH_CHILD')!='1':emit({'type':'extension_ui_request','method':'notify','message':'__workbench_finish__'+json.dumps({'summary':text})})
    emit({'type':'message_start','message':{'role':'assistant'}})
    emit({'type':'message_end','message':{'role':'assistant','content':[{'type':'text','text':text}],'stopReason':'stop'}})
    emit({'type':'agent_end','willRetry':False});emit({'type':'agent_settled','aborted':False})


def source_tool(name,payload):
    call=str(time.monotonic_ns())
    emit({'type':'tool_execution_start','toolCallId':call,'toolName':name,'args':payload})
    response=dialog('__workbench_document__' if name=='workbench_document' else '__workbench_context__',payload)
    data=json.loads(response['value'])
    emit({'type':'tool_execution_end','toolCallId':call,'toolName':name,'isError':'error' in data,'result':{'content':[{'type':'text','text':json.dumps(data)}],'details':data}})
    if 'error' in data:raise RuntimeError(data['error'])
    return data


def source_answer(prompt,broken):
    packed=json.loads(prompt.rsplit('\n',1)[-1])
    if '[eval:P03]' in prompt:
        messages=packed['messages'];digest=None
        if not broken:
            parts=[];offset=0
            while True:
                part=source_tool('workbench_context',{'context_id':packed['context_id'],'offset':offset})
                parts.append(part['text']);digest=part['digest']
                if part['next_offset'] is None:break
                offset=part['next_offset']
            messages=json.loads(''.join(parts))['messages']
        requirements={};ids=[]
        for message in messages:
            if message['role']!='user':continue
            try:data=json.loads(message['text'])
            except ValueError:continue
            if 'requirements' in data:requirements.update(data['requirements']);ids.append(message.get('id'))
            if 'requirements_update' in data:requirements.update(data['requirements_update']);ids.append(message.get('id'))
        return {'requirements':requirements,'source_message_ids':ids,'context_digest':digest}
    document=source_tool('workbench_document',{'action':'list'})['documents'][0]
    pages={page:source_tool('workbench_document',{'action':'read','document_id':document['id'],'page':page}) for page in [1,2,3]}
    patterns=[('sample',1,r'(sample (120))'),('design',2,r'(Design (randomized))'),('groups',2,r'(groups (60))'),('improvement',3,r'(improvement (12))'),('followup',3,r'(followup (8))')]
    claims=[]
    for key,page,pattern in patterns:
        match=re.search(pattern,pages[page]['text']);target=3 if broken and key=='sample' and '[eval:P01]' in prompt else page
        claims.append({'id':key,'value':match[2] if match else '?','quote':match[1] if match else '?','citation':f"paper:{document['id']}#page={target}"})
    output={'claims':claims}
    if '[eval:P02]' in prompt and broken:output['leak']=read('.env')
    return output


for line in sys.stdin:
    command=json.loads(line);kind=command['type']
    if kind=='abort':break
    data={'commands':[{'name':'workbench-status'}]} if kind=='get_commands' else {}
    emit({'type':'response','id':command.get('id'),'command':kind,'success':True,'data':data})
    if kind!='prompt':continue
    emit({'type':'agent_start'});prompt=command['message'];broken=os.environ.get('WORKBENCH_EVAL_VARIANT')=='broken'
    if any(marker in prompt for marker in ['[eval:P01]','[eval:P02]','[eval:P03]']):
        finish(json.dumps(source_answer(prompt,broken)));continue
    if '[eval:C01]' in prompt:
        original=read('batches.py')
        write('batches.py',original if broken else original.replace('len(items)-size','len(items)'))
    elif '[eval:C02:left]' in prompt:
        time.sleep(.08);read('discount.py');write('discount.py','def apply_discount(price, percent):\n    return price * '+('(percent / 100)' if broken else '(1 - percent / 100)')+'\n')
    elif '[eval:C02:right]' in prompt:
        time.sleep(.08);read('tax.py');write('tax.py','def add_tax(price, percent):\n    return price * (1 + percent / 100)\n')
    elif '[eval:C02:review]' in prompt:
        read('discount.py');read('tax.py')
    elif '[eval:C02]' in prompt:
        delegate([{'agent':'task-worker','task':'[eval:C02:left] 修复 discount.py'},{'agent':'task-worker','task':'[eval:C02:right] 修复 tax.py'}],'parallel-fixes')
        delegate([{'agent':'task-reviewer','task':'[eval:C02:review] 独立审查两个模块'}],'review')
    elif '[eval:R03]' in prompt:write('result.txt','recoverable delivery\n')
    else:raise RuntimeError('Unknown frozen evaluation prompt')
    finish('脚本化 fixture 已提交交付；业务成功由独立评分器核对。')
