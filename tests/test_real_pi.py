"""Opt-in real installed Pi integration with a local, fee-free test provider."""
import os
import shutil
import tempfile
import time
import unittest
from functools import partial
from pathlib import Path
from unittest.mock import patch
from workbench.store import Store
from workbench.runtime import Runtime
from workbench.pi_client import PiClient


@unittest.skipUnless(os.environ.get('WORKBENCH_TEST_REAL_PI') == '1' and shutil.which('pi'), 'set WORKBENCH_TEST_REAL_PI=1 to test installed Pi without model calls')
class RealPiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pi_environment=patch.dict(os.environ,{'PI_CODING_AGENT_DIR':str(Path(self.temp.name)/'pi-config')})
        self.pi_environment.start();self.addCleanup(self.pi_environment.stop)
        self.workspace = Path(self.temp.name) / 'project'
        self.workspace.mkdir()
        (self.workspace/'example.txt').write_text('real Pi read evidence')
        self.store = Store(Path(self.temp.name)/'data/state.sqlite3')
        self.runtime = Runtime(self.store)
        self.runtime.config.add_workspace(str(self.workspace))
        fixture = Path(__file__).parent/'fixtures/offline_provider.ts'
        self.runtime.client_factory = partial(PiClient, extra_extensions=[fixture.resolve()])

    def tearDown(self):
        self.runtime.shutdown()
        self.wait(lambda: not self.runtime.active)
        self.store.db.close()
        self.temp.cleanup()

    def wait(self, condition):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if condition(): return
            time.sleep(.02)
        self.fail('real Pi test timed out')

    def start(self):
        run_id=self.runtime.create('验证实际 Pi 工具和子任务链路',str(self.workspace),model='workbench-fixture/fixture')
        self.runtime.start(run_id)
        return run_id

    def assert_success(self, run_id):
        self.wait(lambda:run_id not in self.runtime.active)
        run=self.store.run(run_id)
        self.assertEqual(run['status'],'completed',run.get('reason'))
        self.assertFalse(any(e['kind']=='pi.extension_error' for e in self.store.events(run_id)))

    def test_actual_pi_delegation_and_read_tools(self):
        run_id=self.start()
        self.assert_success(run_id)
        agents=self.store.agents(run_id)
        self.assertEqual(len(agents),3)
        tools=[e for e in self.store.events(run_id) if e['kind']=='tool.completed']
        reads=[e for e in tools if e['data']['name']=='read']
        self.assertEqual(len(reads),2)
        self.assertTrue(all(not e['data']['is_error'] for e in reads))
        self.assertTrue(all(e['data']['agent_id']!='main' for e in reads))
        self.assertTrue(any('real Pi read evidence' in str(e['data']['result']) for e in reads))

    def test_actual_pi_write_hook_requires_approval(self):
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_WRITE':'1'}):
            run_id=self.start()
            self.wait(lambda:self.store.run(run_id)['status'] in ('waiting_approval','failed'))
            self.assertEqual(self.store.run(run_id)['status'],'waiting_approval',self.store.run(run_id)['reason'])
            self.assertFalse((self.workspace/'created.txt').exists())
            self.runtime.decide(run_id,self.store.approvals(run_id)[0]['id'],True)
            self.assert_success(run_id)
            self.assertEqual((self.workspace/'created.txt').read_text(),'from real Pi')
            change = next(e for e in self.store.events(run_id) if e['kind']=='file.changed')
            self.assertIn('+from real Pi', change['data']['diff'])

    def test_followup_restores_real_pi_message_history(self):
        run_id=self.start()
        self.assert_success(run_id)
        self.runtime.start(run_id,'继续核对结果')
        self.assert_success(run_id)
        sessions=[e['data'] for e in self.store.events(run_id) if e['kind']=='session.connected' and e['data']['agent_id']=='main']
        self.assertEqual(len(sessions),2)
        self.assertEqual(sessions[0]['session_id'],sessions[1]['session_id'])
        self.assertGreater(sessions[1]['message_count'],sessions[0]['message_count'])

    def test_actual_pi_asks_user_and_resumes(self):
        with patch.dict(os.environ, {'WORKBENCH_REAL_TEST_ASK': '1'}):
            run_id = self.start()
            self.wait(lambda: self.store.run(run_id)['status'] in ('waiting_input', 'failed'))
            self.assertEqual(self.store.run(run_id)['status'], 'waiting_input', self.store.run(run_id)['reason'])
            request = self.store.inputs(run_id)[0]
            self.runtime.answer(run_id, request['id'], 'Python 开发三年，希望寻找后端岗位')
            self.assert_success(run_id)
            self.assertEqual(len(self.store.runs()), 1)

    def test_actual_pi_uses_custom_role_snapshot(self):
        self.runtime.agents.save({'name':'task-general','description':'通用调查助手','prompt':'读取委派资料，使用中文报告。','tools':['read']})
        with patch.dict(os.environ, {'WORKBENCH_REAL_TEST_CUSTOM': '1'}):
            run_id=self.start()
            self.assert_success(run_id)
            self.assertTrue(any(a['role']=='task-general' for a in self.store.agents(run_id)))
            self.assertTrue(list((self.store.path.parent/'agent-snapshots').glob('task-general-*.md')))

    def test_actual_pi_plain_conversation_keeps_history_without_tasks(self):
        chats=self.runtime.conversations
        chat_id=chats.create('chat',str(self.workspace),'workbench-fixture/fixture')['id']
        chats.send(chat_id,'我有三年后端经验')
        self.wait(lambda:chat_id not in chats.active)
        self.assertEqual(chats.get(chat_id)['status'],'idle')
        self.assertFalse(self.store.runs())
        chats.send(chat_id,'结合前文建议准备内容')
        self.wait(lambda:chat_id not in chats.active)
        sessions=[e['data'] for e in chats.events(chat_id) if e['kind']=='session.connected']
        self.assertEqual(len(sessions),2)
        self.assertEqual(sessions[0]['session_id'],sessions[1]['session_id'])
        self.assertGreater(sessions[1]['message_count'],sessions[0]['message_count'])
        self.assertIn('历史用户消息数：2',chats.get(chat_id)['messages'][-1]['text'])

    def test_actual_pi_conversation_creates_task_with_readable_context(self):
        chats=self.runtime.conversations
        chat_id=chats.create('chat',str(self.workspace),'workbench-fixture/fixture')['id']
        chats.send(chat_id,'我有三年经验，目标杭州，不考虑外包')
        self.wait(lambda:chat_id not in chats.active)
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_CREATE_TASK':'1','WORKBENCH_REAL_TEST_CONTEXT':'1'}):
            chats.send(chat_id,'请基于前面内容创建任务')
            self.wait(lambda:chat_id not in chats.active)
            task_id=chats.get(chat_id)['task_id']
            self.assertIsNotNone(task_id)
            self.assert_success(task_id)
        reads=[e for e in self.store.events(task_id) if e['kind']=='tool.completed' and e['data']['name']=='workbench_context']
        self.assertEqual(len(reads),1)
        self.assertFalse(reads[0]['data']['is_error'])
        self.assertIn('不考虑外包',str(reads[-1]['data']['result']))

    def test_actual_pi_creates_agent_from_conversation(self):
        chats=self.runtime.conversations
        chat_id=chats.create('chat',str(self.workspace),'workbench-fixture/fixture')['id']
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_CREATE_AGENT':'1'}):
            chats.send(chat_id,'请创建面试辅导 Agent')
            self.wait(lambda:chat_id not in chats.active)
        self.assertTrue(any(a['name']=='interview-mentor' for a in self.runtime.agents.list()))
        self.assertFalse(self.store.runs())

    def test_official_structured_output_extension_terminates_and_delivers(self):
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_STRUCTURED':'1'}):
            run_id=self.start()
            self.assert_success(run_id)
        outputs=[e for e in self.store.events(run_id) if e['kind']=='output.structured']
        self.assertEqual(len(outputs),1)
        self.assertEqual(outputs[0]['data']['output']['headline'],'求职计划')
        self.assertEqual(len(outputs[0]['data']['output']['actionItems']),2)
        self.assertTrue(any(e['kind']=='message.completed' and '完善简历' in e['data']['text'] for e in self.store.events(run_id)))

    def test_actual_pi_plan_executes_dependencies_in_separate_batches(self):
        import json
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_PLAN':'1'}):
            run_id=self.start()
            self.assert_success(run_id)
        plan=json.loads(self.store.run(run_id)['plan'])
        self.assertEqual([s['status'] for s in plan],['completed','completed'])
        self.assertEqual(plan[1]['depends_on'],['research'])
        batches=[e for e in self.store.events(run_id) if e['kind']=='workflow.batch_started']
        self.assertEqual(len(batches),2)
        updates=[e for e in self.store.events(run_id) if e['kind']=='plan.updated']
        self.assertTrue(any(e['data']['steps'][0]['status']=='completed' and e['data']['steps'][1]['status']=='pending' for e in updates))

    def test_actual_pi_context_extension_reports_usage(self):
        run_id=self.start()
        self.assert_success(run_id)
        telemetry=[e for e in self.store.events(run_id) if e['kind']=='context.usage']
        self.assertTrue(telemetry)
        self.assertTrue(any(e['data']['agent_id']=='main' for e in telemetry))

    def test_real_pi_long_context_compaction_waits_and_preserves_constraint(self):
        chats=self.runtime.conversations
        chat_id=chats.create('chat',str(self.workspace),'workbench-fixture/fixture')['id']
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_LONG':'1'}):
            for text in ['目标杭州，不考虑外包。[seed-long]','补充背景一。[seed-long]','补充背景二。[seed-long]','[compact-now] 请继续保留我的约束']:
                chats.send(chat_id,text)
                self.wait(lambda:chat_id not in chats.active)
                self.assertEqual(chats.get(chat_id)['status'],'idle')
            events=chats.events(chat_id)
            phases=[e['data']['phase'] for e in events if e['kind'].startswith('context.')]
            self.assertIn('requested',phases)
            self.assertIn('started',phases)
            self.assertIn('completed',phases,[(e['kind'],e['data']) for e in events if e['kind']=='context.failed'])
            self.assertLess(phases.index('started'),phases.index('completed'))
            chat=chats.get(chat_id)
            self.assertIn('不考虑外包',chat['messages'][0]['text'])
            completed=next(e for e in events if e['kind']=='context.completed')
            self.assertIn('不考虑外包',completed['data']['summary'])
            chats.send(chat_id,'继续核对我的硬约束')
            self.wait(lambda:chat_id not in chats.active)
            self.assertIn('不考虑外包',chats.get(chat_id)['messages'][-1]['text'])
            files=list((self.store.path.parent/'chat-sessions'/chat_id).rglob('*.jsonl'))
            self.assertTrue(any('"type":"compaction"' in f.read_text().replace(' ','') for f in files))

    def test_real_pi_reads_uploaded_paper_and_delivers_cited_notes(self):
        import base64
        chats=self.runtime.conversations
        chat_id=chats.create('planning',str(self.workspace),'workbench-fixture/fixture')['id']
        document=self.runtime.documents.upload(chat_id,'论文原文.txt',base64.b64encode('研究设计：样本量为 120。局限：单中心研究。'.encode()).decode())
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_PAPER':'1'}):
            task_id=chats.send(chat_id,'读取附件，输出有来源的阅读笔记')['task_id']
            self.assert_success(task_id)
        reads=[e for e in self.store.events(task_id) if e['kind']=='tool.completed' and e['data']['name']=='workbench_document']
        self.assertEqual(len(reads),3)
        self.assertTrue(all(not e['data'].get('is_error') for e in reads))
        self.assertIn('样本量为 120',str(reads[-1]['data']['result']))
        output=next(e['data']['output'] for e in self.store.events(task_id) if e['kind']=='output.structured')
        self.assertIn('单中心研究',output['summary'])
        self.assertIn('paper:'+document['id'],output['summary'])

    def test_real_pi_task_memory_offload_and_reinjection_after_compaction(self):
        (self.workspace/'example.txt').write_text('原始长工具资料\n'*1200)
        run_id=self.runtime.create('阅读资料并检查交付',str(self.workspace),model='workbench-fixture/fixture')
        self.runtime.task_context.update(run_id,{'revision':1,'kind':'constraint','text':'禁止联网，使用中文'})
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_MEMORY':'1'}):
            self.runtime.start(run_id);self.assert_success(run_id)
            first=self.runtime.task_context.overview(run_id)
            self.assertTrue(first['artifacts'])
            self.assertTrue(first['compactions'], first)
            self.assertNotIn('禁止联网',first['compactions'][0].get('summary',''))
            self.assertEqual(self.store.run(run_id)['summary'],'MEMORY_OK')
            self.runtime.start(run_id,'继续检查，所有用户约束保持')
            self.assert_success(run_id)
        second=self.runtime.task_context.overview(run_id)
        self.assertEqual(self.store.run(run_id)['summary'],'MEMORY_OK')
        self.assertGreater(second['revision'],first['revision'])
        self.assertEqual(second['latest_request'],'继续检查，所有用户约束保持')
        self.assertTrue(all(any(i['text']=='禁止联网，使用中文' for i in a['data']['payload']['user_memory']) for a in second['assemblies']))
        events=self.store.events(run_id)
        self.assertGreaterEqual(len([e for e in events if e['kind']=='memory.assembled']),10)
        self.assertTrue(any(e['kind']=='memory.offloaded' for e in events))

    def test_real_pi_grounded_constraints_and_confirmed_cross_task_memory(self):
        policy=self.runtime.memory.policy()
        self.runtime.memory.configure({'revision':policy['revision'],'auto_constraints':True,'long_term_write':True,'long_term_read':True})
        first=self.runtime.create('以后回答都用中文',str(self.workspace),model='workbench-fixture/fixture')
        catalog=self.runtime.memory.catalog()
        self.runtime.memory.manage({'revision':catalog['revision'],'action':'confirm','id':catalog['items'][0]['id']})
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_PRIVATE_MEMORY':'1'}):
            second=self.runtime.create('预算不超过200元',str(self.workspace),model='workbench-fixture/fixture')
            self.runtime.start(second);self.assert_success(second)
        self.assertEqual(self.store.run(second)['summary'],'PRIVATE_MEMORY_OK')
        self.assertNotEqual(first,second)
        tools=[e for e in self.store.events(second) if e['kind']=='tool.completed' and e['data']['name']=='workbench_memory']
        self.assertEqual(len(tools),2)
        self.assertFalse(any(t['data'].get('is_error') for t in tools))
        with self.store.lock:
            records=[r['data'] for r in self.store.db.execute('SELECT data FROM task_context_assemblies WHERE task_id=?',(second,))]
        self.assertTrue(records);self.assertTrue(all('以后回答都用中文' not in r for r in records))

    def test_real_pi_all_memory_disabled_does_not_inject_or_persist_task_memory(self):
        policy=self.runtime.memory.policy()
        self.runtime.memory.configure({'revision':policy['revision'],'task_memory':False,'auto_constraints':False,'long_term_read':False,'long_term_write':False,'tool_offload':False,'assembly_history':False})
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_PRIVATE_MEMORY':'1','WORKBENCH_REAL_TEST_MEMORY_DISABLED':'1'}):
            task=self.runtime.create('核对关闭记忆后的正常执行',str(self.workspace),model='workbench-fixture/fixture')
            self.runtime.start(task);self.assert_success(task)
        self.assertEqual(self.store.run(task)['summary'],'MEMORY_DISABLED_OK')
        with self.store.lock:
            self.assertFalse(self.store.db.execute('SELECT 1 FROM task_context_state WHERE task_id=?',(task,)).fetchone())
            self.assertFalse(self.store.db.execute('SELECT 1 FROM task_context_assemblies WHERE task_id=?',(task,)).fetchone())

    def test_real_pi_reads_only_selected_source_packet(self):
        import base64
        chat=self.runtime.conversations.create('chat',str(self.workspace))
        doc=self.runtime.documents.upload(chat['id'],'selected.md',base64.b64encode(b'selected').decode())
        self.runtime.documents._save_pages(doc['id'],['SELECTED_FIRST_PAGE','UNSELECTED_PRIVATE','SELECTED_THIRD_PAGE'])
        selection={'conversation_id':chat['id'],'documents':[{'id':doc['id'],'pages':[1,3]}],'message_ids':[],'constraints':'只读取选中页','budget':16000}
        model='workbench-fixture/fixture';preview=self.runtime.sources.preview('核验选中原文',selection,'pi',model)
        with patch.dict(os.environ,{'WORKBENCH_REAL_TEST_SOURCES':'1','WORKBENCH_SOURCE_DOCUMENT':doc['id']}):
            task=self.runtime.create('核验选中原文',str(self.workspace),model=model,source_selection=selection,preview_digest=preview['digest'])
            self.runtime.start(task);self.assert_success(task)
        self.assertEqual(self.store.run(task)['summary'],'SOURCE_SCOPE_OK')
        self.assertNotIn('UNSELECTED_PRIVATE',str(self.store.events(task)))
