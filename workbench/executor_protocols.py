"""Translate documented CLI JSON streams; never expose reasoning blocks."""
import json

SCHEMA = {'type':'object','properties':{'status':{'type':'string','enum':['completed','blocked']},'summary':{'type':'string'},'checks':{'type':'array','items':{'type':'string'}}},'required':['status','summary','checks'],'additionalProperties':False}

class Protocol:
    def __init__(self, vendor):
        self.vendor=vendor;self.finished=False;self.failed=False;self.result=None

    def feed(self, event):
        out=[];kind=event.get('type','')
        if self.vendor=='codex':
            if kind=='thread.started':out.append(('executor.session',{'session_id':event.get('thread_id','')}))
            if kind=='turn.completed':self.finished=True
            if kind in ('turn.failed','error'):self.failed=True
            item=event.get('item') or {};item_type=item.get('type')
            if item_type=='agent_message' and kind=='item.completed':self.result=item.get('text')
            if item_type in ('command_execution','mcp_tool_call','file_change','web_search') and kind in ('item.started','item.completed'):
                out.append(('tool.started' if kind=='item.started' else 'tool.completed',{'id':str(item.get('id','tool')),'name':item_type,'input':{'command':str(item.get('command',''))[:2000]},'is_error':item.get('status')=='failed','result':str(item.get('status',''))}))
        else:
            if kind=='system' and event.get('subtype')=='init':out.append(('executor.session',{'session_id':event.get('session_id','')}))
            if kind in ('assistant','user'):
                for block in (event.get('message') or {}).get('content',[]):
                    if not isinstance(block,dict):continue
                    if block.get('type')=='tool_use':out.append(('tool.started',{'id':block.get('id'),'name':block.get('name'),'input':block.get('input',{})}))
                    if block.get('type')=='tool_result':out.append(('tool.completed',{'id':block.get('tool_use_id'),'name':'tool_result','is_error':bool(block.get('is_error')),'result':'工具执行失败' if block.get('is_error') else '工具已返回'}))
            if kind=='result':
                self.finished=True;self.failed=bool(event.get('is_error')) or event.get('subtype')!='success'
                self.result=event.get('structured_output') or event.get('result')
        return out

    def final(self):
        if not self.finished or self.failed:raise ValueError('外部工具未正常提交完成事件；请检查 CLI 登录、模型与运行环境')
        try:result=json.loads(self.result) if isinstance(self.result,str) else self.result
        except (ValueError,TypeError):raise ValueError('外部工具未返回约定的结构化交付结果') from None
        if not isinstance(result,dict) or result.get('status') not in ('completed','blocked') or not isinstance(result.get('summary'),str) or not result['summary'].strip() or not isinstance(result.get('checks'),list) or any(not isinstance(x,str) for x in result['checks']):raise ValueError('外部工具交付字段无效')
        return result
