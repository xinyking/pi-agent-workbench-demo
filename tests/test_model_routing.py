"""Real installed Pi -> isolated HTTP fixture -> per-agent model routing."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent/'fixtures'))
from model_api import ModelAPI
from workbench.store import Store
from workbench.runtime import Runtime

@unittest.skipUnless(os.environ.get('WORKBENCH_TEST_REAL_PI')=='1' and shutil.which('pi'),'requires installed Pi, local requests only')
class ModelRoutingTests(unittest.TestCase):
 def test_actual_provider_extension_and_different_agent_models(self):
  with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'PI_CODING_AGENT_DIR':str(Path(temp)/'isolated-pi')}):
   root=Path(temp);workspace=root/'project';workspace.mkdir();store=Store(root/'data/state.sqlite3');runtime=Runtime(store);runtime.config.add_workspace(str(workspace));api=ModelAPI().start()
   try:
    key='!literal-$not-executable'
    runtime.models.save({'action':'upsert','revision':0,'id':'routing','name':'Routing fixture','base_url':api.url,'api':'openai-completions','api_key':key,'models':['main','fast','review'],'context_window':32768,'max_tokens':2048})
    runtime.models.save({'action':'default','revision':1,'model':'wbapi-routing/main'})
    runtime.agents.set_model('task-scout','wbapi-routing/fast');runtime.agents.set_model('task-reviewer','wbapi-routing/review')
    run=runtime.create('请委派两个专家核验并报告完成',str(workspace));runtime.start(run);deadline=time.monotonic()+25
    while run in runtime.active and time.monotonic()<deadline:time.sleep(.05)
    self.assertNotIn(run,runtime.active);self.assertEqual(store.run(run)['status'],'completed',store.run(run).get('reason'))
    self.assertEqual(set(api.calls),{'main','fast','review'});self.assertTrue(all(x=='Bearer '+key for x in api.keys));self.assertNotIn(key,str(store.events(run)));self.assertNotIn(key,str(runtime.models.public()))
   finally:runtime.shutdown();api.close();store.db.close()
