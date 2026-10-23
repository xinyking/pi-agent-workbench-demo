import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from workbench.runtime import Runtime
from workbench.store import Store
from workbench.executor_protocols import Protocol

FIXTURE=str(Path(__file__).parent/'fixtures/fake_executor.py')
class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'data/state.sqlite3');self.runtime=Runtime(self.store);self.runtime.config.add_workspace(str(self.root));self.runtime.executors.binaries={'codex':FIXTURE,'claude':FIXTURE}
    def tearDown(self):
        self.runtime.shutdown();self.store.db.close();self.temp.cleanup()
    def create(self,prompt='检查文档',vendor='codex',**options):return self.runtime.create(prompt,str(self.root),vendor,executor_options=options)
    def wait(self,id):
        deadline=time.monotonic()+6
        while id in self.runtime.active and time.monotonic()<deadline:time.sleep(.02)
        self.assertNotIn(id,self.runtime.active);return self.store.run(id)
    def test_both_protocols_complete_and_preserve_context(self):
        for vendor in ('codex','claude'):
            id=self.create(vendor=vendor,context='仅使用中文');self.runtime.start(id);self.assertEqual(self.wait(id)['status'],'completed')
            data=self.runtime.executors.overview(id);self.assertEqual(data['packet']['context'],'仅使用中文');self.assertEqual(data['attempts'][0]['status'],'completed')
            events=self.store.events(id);self.assertTrue(any(x['kind']=='tool.completed' for x in events));self.assertTrue(any(x['kind']=='executor.result' for x in events))
    def test_no_false_success(self):
        for prompt in ('MALFORMED_TEST','EXIT_TEST','NO_FINISH_TEST'):
            id=self.create(prompt);self.runtime.start(id);self.assertEqual(self.wait(id)['status'],'failed')
    def test_cancel_and_followup_new_attempt(self):
        id=self.create('HANG_TEST');self.runtime.start(id);time.sleep(.1);self.assertTrue(self.runtime.cancel(id));self.assertEqual(self.wait(id)['status'],'cancelled')
        id=self.create();self.runtime.start(id);self.wait(id);self.runtime.start(id,'补充检查');self.assertEqual(self.wait(id)['status'],'completed');self.assertEqual(len(self.runtime.executors.overview(id)['attempts']),2)
        self.runtime.start(id,'不要遗漏上次要求');self.wait(id)
        with self.store.lock:body=self.store.db.execute('SELECT input FROM executor_attempts WHERE run_id=? ORDER BY number DESC',(id,)).fetchone()[0]
        self.assertIn('补充检查',body);self.assertIn('不要遗漏上次要求',body)
    def test_blocked_and_validation(self):
        id=self.create('BLOCK_TEST');self.runtime.start(id);self.assertEqual(self.wait(id)['status'],'awaiting_confirmation')
        for options in ({'mode':'dangerous'},{'context':'x'*32001},{'conversation_id':'absent'}):
            with self.assertRaises(ValueError):self.create(**options)
        with self.assertRaises(ValueError):self.runtime.create('test',str(self.root),'unknown')
    def test_explicit_conversation_is_immutable(self):
        chat=self.runtime.conversations.create('chat',str(self.root))
        with self.store.lock,self.store.db:self.store.db.execute('INSERT INTO conversation_messages(conversation_id,role,text,status,created) VALUES(?,?,?,?,?)',(chat['id'],'user','明确聊天来源','completed',time.time()))
        id=self.create(conversation_id=chat['id']);packet=self.runtime.executors.overview(id)['packet'];self.assertEqual(packet['conversation']['messages'][0]['text'],'明确聊天来源')
        with self.store.lock,self.store.db:self.store.db.execute("UPDATE conversation_messages SET text='changed'")
        self.assertEqual(self.runtime.executors.overview(id)['packet'],packet)
    def test_private_reasoning_not_forwarded(self):
        self.assertEqual(Protocol('codex').feed({'type':'item.completed','item':{'type':'reasoning','text':'secret'}}),[])
        self.assertEqual(Protocol('claude').feed({'type':'assistant','message':{'content':[{'type':'thinking','thinking':'secret'}]}}),[])

    def test_permission_arguments_and_prompt_not_in_argv(self):
        for vendor in ('codex','claude'):
            id=self.create(vendor=vendor);run=self.store.run(id);data=self.runtime.executors.overview(id)
            args=self.runtime.executors._command(run,self.root,FIXTURE,data,self.root/'schema.json')
            self.assertNotIn(run['prompt'],args)
            if vendor=='codex':self.assertEqual(args[args.index('--sandbox')+1],'read-only');self.assertIn('never',args)
            else:self.assertEqual(args[args.index('--tools')+1],'Read,Grep,Glob');self.assertIn('dontAsk',args)
    def test_cancel_waiting_writer_does_not_release_other_owner(self):
        lock=threading.Lock();lock.acquire();self.runtime.mutation_locks[str(self.root.resolve())]=lock
        id=self.create(mode='workspace-write');self.runtime.start(id);time.sleep(.1);self.runtime.cancel(id);self.wait(id)
        self.assertTrue(lock.locked());lock.release()
    def test_missing_capability_does_not_create_task(self):
        self.runtime.executors.binaries['codex']='/bin/echo';self.runtime.executors.cache.clear()
        with self.assertRaises(ValueError):self.create()
        self.assertEqual(self.store.runs(),[])
    def test_shutdown_cancels_child_and_marks_attempt(self):
        id=self.create('HANG_TEST');self.runtime.start(id);time.sleep(.1);self.runtime.shutdown();self.assertEqual(self.wait(id)['status'],'cancelled')

    def test_restart_cleans_only_registered_owned_process(self):
        from workbench.executors import Executors
        attempt='restart-fixture'
        process=subprocess.Popen([FIXTURE],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,start_new_session=True,env={**os.environ,'WORKBENCH_EXECUTOR_ATTEMPT':attempt})
        try:
            ticks=Path(f'/proc/{process.pid}/stat').read_text().rsplit(')',1)[1].split()[19]
            with self.store.lock,self.store.db:self.store.db.execute('INSERT INTO executor_attempts(id,run_id,number,input,digest,status,created,process_id,start_ticks) VALUES(?,?,?,?,?,?,?,?,?)',(attempt,'fixture',1,'','hash','running',time.time(),str(process.pid),ticks))
            Executors(self.runtime);process.wait(timeout=3)
            self.assertNotEqual(process.returncode,0)
            with self.store.lock:self.assertEqual(self.store.db.execute('SELECT status FROM executor_attempts WHERE id=?',(attempt,)).fetchone()[0],'interrupted')
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdin.close()
