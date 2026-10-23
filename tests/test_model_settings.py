import json
import tempfile
import threading
import unittest
from pathlib import Path
from workbench.runtime import Runtime
from workbench.store import Store

class ModelSettingsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'data/state.sqlite3');self.runtime=Runtime(self.store);self.runtime.config.add_workspace(str(self.root))
 def tearDown(self):self.runtime.shutdown();self.store.db.close();self.temp.cleanup()
 def request(self,**changes):return {'action':'upsert','revision':self.runtime.models.public()['revision'],'id':'local-test','name':'Local test','base_url':'http://127.0.0.1:8000/v1','api':'openai-completions','api_key':'!literal-key-$NOT_EXECUTED','models':['main','review'],'context_window':32768,'max_tokens':4096,**changes}
 def test_key_is_private_atomic_and_redacted(self):
  payload=self.request();data=self.runtime.models.save(payload);self.assertNotIn(payload['api_key'],json.dumps(data));self.assertTrue(data['providers'][0]['has_key']);self.assertEqual(self.runtime.models.path.stat().st_mode&0o777,0o600);self.assertNotIn(payload['api_key'],str(self.store.db.execute('SELECT * FROM settings').fetchall()));self.assertEqual(self.runtime.config.redact(payload['api_key']),'[REDACTED]')
  self.runtime.models.save(self.request(api_key=''))
  with self.assertRaises(ValueError):self.runtime.models.save(self.request(api_key='',base_url='http://127.0.0.1:9000/v1'))
  self.assertEqual(self.runtime.models.read()['providers'][0]['api_key'],payload['api_key'])
 def test_default_and_builtin_agent_overrides(self):
  self.runtime.models.save(self.request());self.runtime.models.save({'action':'default','revision':1,'model':'wbapi-local-test/main'});run=self.runtime.create('模型测试',str(self.root));self.assertEqual(self.store.run(run)['model'],'wbapi-local-test/main');chat=self.runtime.conversations.create('chat');self.assertEqual(chat['model'],'wbapi-local-test/main')
  self.runtime.agents.set_model('task-reviewer','wbapi-local-test/review');agent=next(a for a in self.runtime.agents.list() if a['name']=='task-reviewer');self.assertEqual(agent['source'],'builtin');self.assertEqual(agent['model'],'wbapi-local-test/review');self.assertIn('wbapi-local-test/review',self.runtime.agents.snapshot(agent).read_text())
 def test_revision_active_and_references(self):
  self.runtime.models.save(self.request())
  with self.assertRaises(ValueError):self.runtime.models.save(self.request(revision=0))
  stop=threading.Event();self.runtime.active['test']=stop
  with self.assertRaises(ValueError):self.runtime.models.save(self.request())
  self.runtime.active.clear();self.runtime.agents.set_model('task-reviewer','wbapi-local-test/review')
  with self.assertRaises(ValueError):self.runtime.models.save(self.request(models=['main']))
  self.runtime.agents.set_model('task-reviewer','');self.runtime.models.save({'action':'delete','revision':1,'id':'local-test'});self.assertEqual(self.runtime.models.public()['providers'],[])
 def test_invalid_values_never_write(self):
  for changes in ({'base_url':'file:///etc/passwd'},{'base_url':'http://127.0.0.1:wrong/v1'},{'base_url':'https://user:pass@example.test'},{'api':'shell'},{'models':[]},{'max_tokens':40000},{'api_key':'key\nheader'},{'context_window':True},{'reasoning':'true'}):
   with self.assertRaises(ValueError):self.runtime.models.save(self.request(**changes))
  self.assertFalse(self.runtime.models.path.exists())
  with self.assertRaises(ValueError):self.runtime.models.validate_model('wbapi-unknown/model')
  with self.assertRaises(ValueError):self.runtime.models.validate_model(' wbapi-unknown/model ')
