"""External executors never run through Pi or overwrite internal tasks."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from workbench.__main__ import WorkbenchServer, make_handler
from workbench.external_tasks import ExternalTasks
from workbench.runtime import Runtime
from workbench.store import Store

ROOT = Path(__file__).resolve().parents[1]


class ExternalTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'state.sqlite3')
        self.external = ExternalTasks(self.store)

    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()

    def create(self):
        return self.external.create({'source': 'test-editor', 'external_id': '001', 'prompt': '核查测试结果'})['id']

    def test_import_is_idempotent_and_preserves_identity(self):
        run_id = self.create()
        self.assertEqual(self.create(), run_id)
        self.assertEqual(len(self.store.runs()), 1)
        with self.assertRaises(ValueError): self.external.create({'source':'test-editor','external_id':'001','prompt':'different'})
        self.assertEqual(self.store.run(run_id)['provider_id'], 'external')

    def test_ordered_reports_retries_and_tool_results(self):
        run_id = self.create()
        first = {'sequence':1,'status':'running','step':'读取外部工具','tool':'reader','result':'实际输出'}
        self.external.report(run_id,first)
        count = len(self.store.events(run_id))
        self.external.report(run_id,dict(first))
        self.assertEqual(len(self.store.events(run_id)), count)
        with self.assertRaises(ValueError): self.external.report(run_id,{**first,'result':'changed'})
        with self.assertRaises(ValueError): self.external.report(run_id,{**first,'sequence':3})
        self.external.report(run_id,{'sequence':2,'status':'completed','step':'已交付','result':'实际交付摘要'})
        self.assertEqual(self.store.run(run_id)['summary'],'实际交付摘要')
        self.assertEqual(self.store.run(run_id)['status'],'completed')
        tools=[e for e in self.store.events(run_id) if e['kind'].startswith('tool.')]
        self.assertEqual(tools[0]['data']['id'],tools[1]['data']['id'])
        with self.assertRaises(ValueError): self.external.report(run_id,{**first,'sequence':3})

    def test_invalid_report_does_not_mutate_internal_or_external_task(self):
        run_id = self.create()
        before = len(self.store.events(run_id))
        for data in [{'sequence':True,'status':'running','step':'x'}, {'sequence':1,'status':'completed','step':'x'}, {'sequence':1,'status':'running','step':'x','tool':{}}]:
            with self.assertRaises(ValueError): self.external.report(run_id,data)
        internal=self.store.create('internal',provider_id='pi')
        with self.assertRaises(ValueError): self.external.report(internal,{'sequence':1,'status':'running','step':'x'})
        self.assertEqual(len(self.store.events(run_id)),before)

    def test_blocked_and_restart_keep_reports(self):
        run_id=self.create()
        self.external.report(run_id,{'sequence':1,'status':'blocked','step':'等待资料','result':'请在来源软件补充文件'})
        self.assertEqual(self.store.run(run_id)['status'],'awaiting_confirmation')
        self.store.db.close();self.store=Store(Path(self.temp.name)/'state.sqlite3');self.external=ExternalTasks(self.store)
        self.assertEqual(self.external.get(run_id)['sequence'],1)
        self.external.report(run_id,{'sequence':2,'status':'running','step':'继续检查'})
        self.assertEqual(self.store.run(run_id)['status'],'running')

    def test_real_http_skill_command_wrapper_and_failure(self):
        runtime=Runtime(self.store)
        server=WorkbenchServer(('127.0.0.1',0),make_handler(self.store,runtime))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for key,command,code in [('ok',[sys.executable,'-c','print("external result")'],0),('bad',[sys.executable,'-c','raise SystemExit(3)'],1)]:
                args=[sys.executable,str(ROOT/'skills/task-board/scripts/board.py'),'--url',f'http://127.0.0.1:{server.server_port}','run','--source','cli','--external-id',key,'--prompt','验证外部运行','--',*command]
                process=subprocess.run(args,capture_output=True,text=True,timeout=20)
                self.assertEqual(process.returncode,code,process.stderr)
                result=json.loads(process.stdout)
                self.assertEqual(result['sequence'],2)
                self.assertEqual(result['run']['status'],'completed' if key=='ok' else 'failed')
                repeat=subprocess.run(args,capture_output=True,text=True,timeout=20)
                self.assertEqual(repeat.returncode,1)
                self.assertIn('不会自动重复执行',repeat.stderr)
        finally:
            server.shutdown();server.server_close();runtime.shutdown()
