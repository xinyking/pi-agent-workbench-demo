import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from workbench.runtime import Runtime
from workbench.store import Store

class SourcePacketTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir();self.store=Store(self.root/'data/state.sqlite3');self.runtime=Runtime(self.store);self.runtime.config.add_workspace(str(self.workspace));self.chat=self.runtime.conversations.create('chat')
  with self.store.lock,self.store.db:
   self.message=self.store.db.execute('INSERT INTO conversation_messages(conversation_id,role,text,status,created) VALUES(?,?,?,?,?)',(self.chat['id'],'user','选中消息：中文交付','completed',time.time())).lastrowid
   self.hidden=self.store.db.execute('INSERT INTO conversation_messages(conversation_id,role,text,status,created) VALUES(?,?,?,?,?)',(self.chat['id'],'user','不要读取的私有消息','completed',time.time())).lastrowid
  self.doc=self.runtime.documents.upload(self.chat['id'],'sources.md',base64.b64encode(b'# One\nselected source').decode());self.runtime.documents._save_pages(self.doc['id'],['选中页原文A','不应读取的第二页','选中页原文C'])
 def tearDown(self):self.runtime.shutdown();self.store.db.close();self.temp.cleanup()
 def selection(self,**changes):return {'conversation_id':self.chat['id'],'message_ids':[self.message],'documents':[{'id':self.doc['id'],'pages':[1,3]}],'constraints':'必须使用中文并注明来源','context':'手工资料','budget':16000,**changes}
 def create(self,selection=None,vendor='pi'):
  selected=selection or self.selection();preview=self.runtime.sources.preview('核查资料',selected,vendor)
  return self.runtime.create('核查资料',str(self.workspace),vendor,source_selection=selected,preview_digest=preview['digest'])
 def test_preview_and_actual_initial_input_same_scoped_sources(self):
  selected=self.selection();preview=self.runtime.sources.preview('核查资料',selected);id=self.create(selected);data=self.runtime.sources.get(id)
  self.assertEqual(preview['text'],self.store.messages(id)[0]['content']);self.assertEqual(preview['digest'],data['digest']);self.assertIn('选中页原文A',data['text']);self.assertNotIn('不要读取的私有消息',data['text']);self.assertNotIn('不应读取的第二页',data['text']);self.assertIn('必须使用中文',self.runtime.task_context.overview(id)['goal']);self.assertEqual(self.store.run(id)['prompt'],'核查资料')
 def test_snapshot_survives_source_edits_and_archive(self):
  id=self.create();self.runtime.documents._save_pages(self.doc['id'],['changed']);self.runtime.documents.archive(self.chat['id'],self.doc['id']);result=self.runtime.sources.tool(id,{'action':'read','document_id':self.doc['id'],'page':3});self.assertEqual(result['text'],'选中页原文C');self.assertIn('#page=3',result['citation'])
 def test_tools_reject_unselected_pages_documents_and_context(self):
  id=self.create()
  for payload in ({'action':'read','document_id':self.doc['id'],'page':2},{'action':'read','document_id':'other','page':1},{'action':'read','document_id':self.doc['id'],'page':True}):
   with self.assertRaises(ValueError):self.runtime.sources.tool(id,payload)
  with self.assertRaises(ValueError):self.runtime.sources.context(id,'other')
  self.assertEqual(self.runtime.sources.tool(id,{'action':'list'})['documents'][0]['selected_pages'],[1,3])
  self.assertEqual([p['page'] for p in self.runtime.sources.tool(id,{'action':'outline','document_id':self.doc['id']})['entries']],[1,3]);self.assertEqual(self.runtime.sources.tool(id,{'action':'search','document_id':self.doc['id'],'query':'第二页'})['matches'],[])
 def test_over_budget_never_creates_or_truncates_and_changed_source_requires_preview(self):
  selection=self.selection(context='x'*5000,budget=1024);preview=self.runtime.sources.preview('核查资料',selection);self.assertFalse(preview['budget']['fits'])
  with self.assertRaises(ValueError):self.runtime.create('核查资料',str(self.workspace),source_selection=selection,preview_digest=preview['digest'])
  selection=self.selection();preview=self.runtime.sources.preview('核查资料',selection);self.runtime.documents._save_pages(self.doc['id'],['修改后的第一页','第二页','第三页'])
  with self.assertRaises(ValueError):self.runtime.create('核查资料',str(self.workspace),source_selection=selection,preview_digest=preview['digest'])
  self.assertFalse(self.store.runs())
 def test_foreign_ids_invalid_pages_and_loading_documents_rejected(self):
  for changes in ({'message_ids':[99999]},{'documents':[{'id':'other','pages':[1]}]},{'documents':[{'id':self.doc['id'],'pages':[4]}]},{'message_ids':[True]},{'budget':True}):
   with self.assertRaises(ValueError):self.runtime.sources.preview('核查资料',self.selection(**changes))
  self.runtime.documents._status(self.doc['id'],'processing')
  with self.assertRaises(ValueError):self.runtime.sources.preview('核查资料',self.selection())
 def test_managed_model_window_caps_budget_without_guessing_native_model(self):
  self.runtime.models.save({'action':'upsert','revision':0,'id':'small-model','name':'small','base_url':'http://localhost:1/v1','api':'openai-completions','api_key':'fixture','models':['small'],'context_window':4096,'max_tokens':1024})
  result=self.runtime.sources.preview('核查资料',self.selection(),'pi','wbapi-small-model/small');self.assertEqual(result['budget']['context_window'],4096);self.assertFalse(result['budget']['fits']);self.assertIsNone(self.runtime.sources.preview('核查资料',self.selection(),'pi','native/model')['budget']['context_window'])
 def test_external_executor_contains_the_same_selected_document_snapshot(self):
  self.runtime.executors.binaries={'codex':str(Path(__file__).parent/'fixtures/fake_executor.py')};id=self.create(vendor='codex');overview=self.runtime.executors.overview(id);self.assertEqual(overview['packet']['selected_sources'],overview['source_packet']['packet']);self.assertNotIn('不应读取的第二页',json.dumps(overview,ensure_ascii=False));self.runtime.start(id)
  deadline=time.monotonic()+5
  while id in self.runtime.active and time.monotonic()<deadline:time.sleep(.02)
  self.assertEqual(self.store.run(id)['status'],'completed')
