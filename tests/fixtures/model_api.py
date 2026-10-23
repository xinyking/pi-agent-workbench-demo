"""Local OpenAI-compatible deterministic SSE fixture. Never contacts a model."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class ModelAPI:
 def __init__(self):
  self.calls=[];self.keys=[];self.catalog_calls=[];self.catalog_mode='normal';owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    owner.catalog_calls.append(self.path)
    if owner.catalog_mode=='slow':time.sleep(.5)
    if owner.catalog_mode=='error' or '/missing/' in self.path:
     self.send_response(404);self.end_headers();self.wfile.write(b'{"error":"upstream detail withheld"}');return
    data={'data':[{'id':'main','name':'Main model'},{'id':'fast','name':'Fast model'},{'id':'review','name':'Review model'},{'id':'media','name':'Image-only example'}]}
    body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
   def do_POST(self):
    body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));owner.calls.append(body['model']);owner.keys.append(self.headers.get('Authorization'))
    tools={t['function']['name'] for t in body.get('tools',[])};count=sum(m.get('role')=='tool' for m in body['messages'])
    call=None
    if 'workbench_delegate' in tools and count==0:call={'id':'fixture-delegate','type':'function','function':{'name':'workbench_delegate','arguments':json.dumps({'tasks':[{'agent':'task-scout','task':'用一句话给出检查结论'},{'agent':'task-reviewer','task':'独立复核并用一句话总结'}]})}}
    elif 'workbench_finish' in tools and not any('fixture-finish'==m.get('tool_call_id') for m in body['messages']):call={'id':'fixture-finish','type':'function','function':{'name':'workbench_finish','arguments':json.dumps({'summary':'本地模型路由与交付验证完成'})}}
    delta={'role':'assistant','tool_calls':[{'index':0,**call}]} if call else {'role':'assistant','content':'模型 '+body['model']+' 已完成本地核验'}
    chunks=[{'id':'local-fixture','object':'chat.completion.chunk','created':1,'model':body['model'],'choices':[{'index':0,'delta':delta,'finish_reason':None}]},{'id':'local-fixture','object':'chat.completion.chunk','created':1,'model':body['model'],'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if call else 'stop'}],'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}]
    output=(''.join('data: '+json.dumps(c,ensure_ascii=False)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(output)));self.end_headers();self.wfile.write(output)
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
 @property
 def url(self):return f'http://127.0.0.1:{self.server.server_port}/v1'
 def start(self):self.thread.start();return self
 def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
