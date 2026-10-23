import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from workbench.model_discovery import fetch_models
from workbench.runtime import Runtime
from workbench.store import Store

class DiscoveryTests(unittest.TestCase):
 def setUp(self):
  self.mode='normal';self.calls=[];owner=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    owner.calls.append((self.path,dict(self.headers)));mode=owner.mode
    if mode=='slow':time.sleep(.2)
    if mode.startswith('http'):
     self.send_response(int(mode[4:]));self.send_header('Location','/leaked');self.end_headers();self.wfile.write(b'upstream echoed secret-never-display');return
    payload={'data':[{'id':'model-a','name':'Model A'},{'id':'model-a'},{'id':'model-b'},{'id':'bad id'},None]}
    if mode=='anthropic':payload={'data':[{'id':'claude-b' if 'after_id=claude-a' in self.path else 'claude-a','display_name':'Claude fixture'}],'has_more':'after_id=' not in self.path,'last_id':'claude-a'}
    if mode=='loop':payload={'data':[{'id':'a'}],'has_more':True,'last_id':'a'}
    if mode=='empty':payload={'data':[]}
    if mode=='echo':payload={'data':[{'id':'secret-never-display'},{'id':'valid','name':'secret-never-display'}]}
    if mode=='wrong':payload={'models':['model-a']}
    if mode=='large':payload={'data':[{'id':'x'*5000}]}
    if mode=='many':payload={'data':[{'id':f'model-{i}'} for i in range(4)]}
    output=b'<html>secret-never-display</html>' if mode=='bad' else json.dumps(payload).encode()
    self.send_response(200);self.send_header('Content-Length',str(len(output)));self.send_header('Content-Type','application/json');self.end_headers()
    try:self.wfile.write(output)
    except (BrokenPipeError,ConnectionResetError):pass
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.server.server_port}/v1'
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
 def fetch(self,api='openai-completions'):return fetch_models(self.base,api,'secret-never-display')
 def test_openai_protocols_deduplicate_and_skip_invalid(self):
  for api in ('openai-completions','openai-responses'):
   result=self.fetch(api);self.assertEqual([x['id'] for x in result['models']],['model-a','model-b']);self.assertFalse(result['truncated']);self.assertTrue(result['warnings']);self.assertEqual(self.calls[-1][0],'/v1/models');self.assertEqual(self.calls[-1][1]['Authorization'],'Bearer secret-never-display')
 def test_anthropic_paging_and_auth(self):
  self.mode='anthropic';result=self.fetch('anthropic-messages');self.assertEqual(len(result['models']),2);self.assertEqual(len(self.calls),2);self.assertIn('after_id=claude-a',self.calls[-1][0]);self.assertEqual(self.calls[0][1]['X-Api-Key'],'secret-never-display');self.assertNotIn('Authorization',self.calls[0][1])
 def test_errors_hide_upstream_body_and_never_follow_redirect(self):
  for code in (401,403,404,429,500,302):
   self.mode='http'+str(code);before=len(self.calls)
   with self.assertRaises(ValueError) as context:self.fetch()
   self.assertNotIn('secret-never-display',str(context.exception));self.assertEqual(len(self.calls),before+1)
 def test_format_empty_size_and_count_limits(self):
  for mode in ('wrong','bad'):
   self.mode=mode
   with self.assertRaises(ValueError):self.fetch()
  self.mode='empty';self.assertEqual(self.fetch()['models'],[])
  self.mode='echo';self.assertNotIn('secret-never-display',json.dumps(self.fetch()))
  self.mode='large'
  with patch('workbench.model_discovery.MAX_BYTES',128),self.assertRaises(ValueError):self.fetch()
  self.mode='many'
  with patch('workbench.model_discovery.MAX_MODELS',2):result=self.fetch()
  self.assertEqual(len(result['models']),2);self.assertTrue(result['truncated'])
 def test_timeout_and_repeated_cursor_are_bounded(self):
  self.mode='slow'
  with patch('workbench.model_discovery.TIMEOUT',.05),self.assertRaises(ValueError):self.fetch()
  self.mode='loop'
  with self.assertRaises(ValueError):self.fetch('anthropic-messages')
 def test_saved_key_scope_revision_and_no_persistence(self):
  with tempfile.TemporaryDirectory() as temp:
   store=Store(Path(temp)/'data/state.sqlite3');runtime=Runtime(store)
   try:
    runtime.models.save({'action':'upsert','revision':0,'id':'test-catalog','name':'Catalog','base_url':self.base,'api':'openai-completions','api_key':'saved-secret','models':['existing'],'context_window':32768,'max_tokens':4096})
    before=runtime.models.path.read_bytes();request={'id':'test-catalog','revision':1,'base_url':self.base,'api':'openai-completions','api_key':''}
    result=runtime.models.discover(request);self.assertEqual(len(result['models']),2);self.assertEqual(self.calls[-1][1]['Authorization'],'Bearer saved-secret');self.assertEqual(runtime.models.path.read_bytes(),before)
    for changes in ({'base_url':self.base+'/other'},{'api':'anthropic-messages'},{'revision':0}):
     count=len(self.calls)
     with self.assertRaises(ValueError):runtime.models.discover({**request,**changes})
     self.assertEqual(len(self.calls),count)
    runtime.models.discover({**request,'base_url':self.base+'/new','api_key':'explicit-new-secret'});self.assertEqual(self.calls[-1][1]['Authorization'],'Bearer explicit-new-secret');self.assertEqual(runtime.models.path.read_bytes(),before)
   finally:runtime.shutdown();store.db.close()
 def test_invalid_request_cannot_send_headers(self):
  for base,api,key in [('file:///etc/passwd','openai-completions','key'),(self.base,'unknown','key'),(self.base,'openai-completions','key\nheader')]:
   with self.assertRaises(ValueError):fetch_models(base,api,key)
  self.assertFalse(self.calls)
