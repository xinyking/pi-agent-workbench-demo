"""Exercise the actual subprocess RPC transport against a deterministic peer."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from workbench.store import Store
from workbench.runtime import Runtime

FIXTURE = str(Path(__file__).parent / 'fixtures/fake_pi.py')


class PiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / 'project'
        self.workspace.mkdir()
        self.store = Store(Path(self.temp.name) / 'data/state.sqlite3')
        self.runtime = Runtime(self.store)
        self.runtime.config.binary = FIXTURE
        self.runtime.config.version = 'fixture'
        self.runtime.config.add_workspace(str(self.workspace))

    def tearDown(self):
        self.runtime.shutdown()
        self.wait(lambda: not self.runtime.active)
        self.store.db.close()
        self.temp.cleanup()

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if condition(): return
            time.sleep(.01)
        self.fail('Pi task did not reach expected state')

    def start(self, prompt):
        run_id = self.runtime.create(prompt, str(self.workspace))
        self.assertEqual(self.store.run(run_id)['status'], 'queued')
        self.runtime.start(run_id)
        return run_id

    def finish(self, run_id):
        self.wait(lambda: run_id not in self.runtime.active)

    def test_root_task_completes_with_explicit_report(self):
        run_id = self.start('完成一个任务')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')
        agents = self.store.agents(run_id)
        self.assertEqual(len(agents), 1)
        self.assertEqual(agents[0]['id'], 'main')
        self.assertEqual(agents[0]['status'], 'completed')
        self.assertIn('\u2028', next(e['data']['text'] for e in self.store.events(run_id) if e['kind']=='message.delta'))
        self.assertEqual(self.store.run(run_id)['status'], 'completed')


    def test_completed_task_has_workspace_summary_and_archive_api(self):
        import json
        run_id=self.start('阅读并总结论文');self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'],'completed')
        head,body=self.request('/api/runs/'+run_id+'/archive');self.assertIn(b'200',head)
        data=json.loads(body);self.assertEqual(len(data['archives']),1)
        archive=data['archives'][0]
        self.assertTrue((self.workspace/archive['summary_path']).is_file())
        self.assertIn('任务完成总结',data['summary_markdown'])
        head,body=self.request('/api/runs/'+run_id+'/archive',{})
        self.assertIn(b'200',head)
        self.assertNotEqual(json.loads(body)['directory'],archive['directory'])
        self.assertTrue((self.workspace/archive['summary_path']).exists())
        events=self.store.events(run_id)
        self.assertLess(next(e['id'] for e in events if e['kind']=='task.archived'),next(e['id'] for e in events if e['kind']=='task.completed'))

    def test_summary_write_failure_does_not_report_task_completed(self):
        with patch('workbench.runtime.create_archive',side_effect=OSError('磁盘不可写')):
            run_id=self.start('生成交付');self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'],'interrupted')
        self.assertIn('保存工作区总结失败',self.store.run(run_id)['reason'])
        self.assertFalse(any(e['kind']=='task.completed' for e in self.store.events(run_id)))
        with patch.object(self.runtime,'client_factory',side_effect=AssertionError('重新归档不应调用模型')):
            result=self.runtime.archive_completed(run_id)
        self.assertEqual(self.store.run(run_id)['status'],'completed')
        self.assertTrue((self.workspace/result['summary_path']).exists())

    def test_parallel_children_keep_parent_and_event_identity(self):
        run_id = self.start('[parallel]')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')
        agents = self.store.agents(run_id)
        self.assertEqual(len(agents), 3)
        children = [a for a in agents if a['parent_id']=='main']
        self.assertEqual(len(children), 2)
        deltas = [e for e in self.store.events(run_id) if e['kind']=='message.delta']
        self.assertEqual(len({e['data']['agent_id'] for e in deltas}), 3)
        self.assertTrue(all(e['data'].get('attempt_id') for e in deltas))
        self.assertEqual(len(self.store.runs()), 1)
        self.assertEqual(self.runtime.board()['tasks'][0]['agent_count'], 2)
        self.assertEqual(self.runtime.board()['tasks'][0]['agents_completed'], 2)

    def test_child_failure_requires_attention(self):
        run_id = self.start('[parallel][childfail]')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'failed')
        self.assertIn('子任务', self.store.run(run_id)['reason'])
        self.assertEqual(sum(a['status']=='failed' for a in self.store.agents(run_id)), 2)

    def test_pi_file_write_waits_for_approval(self):
        run_id = self.start('[write]')
        self.wait(lambda: self.store.run(run_id)['status']=='waiting_approval')
        self.assertFalse((self.workspace / 'result.txt').exists())
        approval = self.store.approvals(run_id)[0]
        self.assertEqual(approval['data']['agent_id'], 'main')
        self.runtime.decide(run_id, approval['id'], True)
        self.finish(run_id)
        self.assertEqual((self.workspace / 'result.txt').read_text(), 'done')
        self.assertEqual(self.runtime.board()['tasks'][0]['file_change_count'], 1)

    def test_denied_write_produces_no_file_change(self):
        run_id = self.start('[write]')
        self.wait(lambda: self.store.run(run_id)['status']=='waiting_approval')
        self.runtime.decide(run_id, self.store.approvals(run_id)[0]['id'], False)
        self.finish(run_id)
        self.assertFalse((self.workspace / 'result.txt').exists())
        self.assertFalse(any(e['kind']=='file.changed' for e in self.store.events(run_id)))

    def test_parallel_approvals_do_not_clear_attention_early(self):
        run_id = self.start('[parallel][approvals]')
        self.wait(lambda: len(self.store.approvals(run_id))==2)
        approvals = self.store.approvals(run_id)
        self.runtime.decide(run_id, approvals[0]['id'], False)
        self.wait(lambda: len(self.runtime.waiters)==1)
        self.assertEqual(self.store.run(run_id)['status'], 'waiting_approval')
        self.runtime.decide(run_id, approvals[1]['id'], False)
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')

    def test_cancel_while_waiting_expires_approval(self):
        run_id = self.start('[write]')
        self.wait(lambda: self.store.run(run_id)['status']=='waiting_approval')
        self.runtime.cancel(run_id)
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'cancelled')
        self.assertEqual(self.store.approvals(run_id)[0]['status'], 'expired')
        self.assertEqual(self.store.agents(run_id)[0]['status'], 'cancelled')

    def test_unexpected_process_exit_is_failure(self):
        run_id = self.start('[exit]')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'failed')

    def test_agent_end_before_retry_is_not_completion(self):
        gate = self.workspace / 'release'
        with patch.dict(os.environ, {'FAKE_PI_GATE': str(gate)}):
            run_id = self.start('[retry]')
            self.wait(lambda: any(e['kind']=='pi.agent_end' for e in self.store.events(run_id)))
            self.assertEqual(self.store.run(run_id)['status'], 'running')
            gate.touch()
            self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')

    def test_followup_reuses_the_same_pi_session_id(self):
        run_id = self.start('第一次')
        self.finish(run_id)
        self.runtime.start(run_id, '继续完善')
        self.finish(run_id)
        sessions = [e['data']['session_id'] for e in self.store.events(run_id) if e['kind']=='session.connected']
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0], sessions[1])

    def test_legacy_tasks_cannot_silently_switch_execution_engine(self):
        run_id = self.store.create('旧任务', str(self.workspace), 'old-provider', 'old-model')
        with self.assertRaisesRegex(ValueError, '旧版任务'):
            self.runtime.start(run_id)

    def test_missing_resume_pauses_and_answer_resumes_same_task(self):
        run_id = self.start('[ask]')
        self.wait(lambda: self.store.run(run_id)['status'] == 'waiting_input')
        self.assertEqual(self.runtime.board()['tasks'][0]['bucket'], 'attention')
        request = self.store.inputs(run_id)[0]
        self.assertIn('简历', request['data']['question'])
        self.assertIn(run_id, self.runtime.active)
        self.runtime.answer(run_id, request['id'], '三年 Python 开发经验')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')
        self.assertEqual(len(self.store.runs()), 1)
        with self.assertRaises(ValueError): self.runtime.answer(run_id, request['id'], '重复回答')

    def test_parallel_questions_remain_attention_until_all_answered(self):
        run_id = self.start('[parallel][askchildren]')
        self.wait(lambda: len(self.store.inputs(run_id)) == 2)
        requests = self.store.inputs(run_id)
        self.runtime.answer(run_id, requests[0]['id'], '回答一')
        self.assertEqual(self.store.run(run_id)['status'], 'waiting_input')
        self.runtime.answer(run_id, requests[1]['id'], '回答二')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')
        batches = [e for e in self.store.events(run_id) if e['kind'] == 'workflow.batch_started']
        self.assertEqual(len(batches[0]['data']['tasks']), 2)

    def test_plain_reply_without_completion_report_requires_attention(self):
        run_id = self.start('[unreported]')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'interrupted')
        self.assertTrue(any(e['kind']=='task.needs_completion' for e in self.store.events(run_id)))
        self.assertFalse(any(e['kind']=='error' for e in self.store.events(run_id)))
        self.assertEqual(self.runtime.board()['tasks'][0]['bucket'], 'attention')

    def test_pending_question_survives_restart_and_answer_is_saved(self):
        run_id = self.runtime.create('找工作', str(self.workspace))
        request = self.store.input_request(run_id, 'main', {'question': '请提供简历'})
        self.store.emit(run_id, 'status', {'status': 'waiting_input'})
        self.store.db.close()
        self.store = Store(Path(self.temp.name) / 'data/state.sqlite3')
        self.runtime.store = self.store
        self.runtime.config.store = self.store
        self.assertEqual(self.store.run(run_id)['status'], 'waiting_input')
        self.runtime.answer(run_id, request, '已有三年工作经验')
        self.finish(run_id)
        self.assertEqual(self.store.inputs(run_id)[0]['answer'], '已有三年工作经验')
        self.assertEqual(self.store.run(run_id)['status'], 'completed')

    def test_restart_reclassifies_only_missing_completion_marker_and_preserves_history(self):
        run_id=self.runtime.create('已有交付',str(self.workspace))
        other=self.runtime.create('实际错误',str(self.workspace))
        self.store.emit(run_id,'status',{'status':'failed','reason':'Pi 尚未明确报告任务完成，请查看执行记录后继续任务'})
        self.store.emit(run_id,'message.completed',{'agent_id':'main','text':'已有交付内容'})
        self.store.emit(other,'status',{'status':'failed','reason':'模型连接失败'})
        path=self.store.path
        self.store.db.close()
        self.store=Store(path)
        self.runtime.store=self.store;self.runtime.config.store=self.store
        self.assertEqual(self.store.run(run_id)['status'],'interrupted')
        self.assertEqual(self.store.run(other)['status'],'failed')
        self.assertTrue(any(e['data'].get('text')=='已有交付内容' for e in self.store.events(run_id)))

    def test_cancel_pending_input(self):
        run_id = self.start('[ask]')
        self.wait(lambda: self.store.run(run_id)['status'] == 'waiting_input')
        self.runtime.cancel(run_id)
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'cancelled')
        self.assertEqual(self.store.inputs(run_id)[0]['status'], 'cancelled')

    def request(self, path, data=None):
        import io
        import json
        from workbench.__main__ import make_handler
        class Connection:
            def __init__(self, request):
                self.input, self.output = io.BytesIO(request), bytearray()
            def makefile(self, *args): return self.input
            def sendall(self, data): self.output.extend(data)
        body = json.dumps(data).encode() if data is not None else b''
        headers = f"{'POST' if data is not None else 'GET'} {path} HTTP/1.0\r\nHost: 127.0.0.1:4310\r\n"
        if data is not None: headers += f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n'
        connection = Connection((headers+'\r\n').encode()+body)
        make_handler(self.store, self.runtime)(connection, ('127.0.0.1', 1234), None)
        return bytes(connection.output).split(b'\r\n\r\n', 1)

    def test_http_answer_and_detail_contract(self):
        import json
        head, body = self.request('/api/runs', {'prompt': '[ask]', 'workspace': str(self.workspace)})
        self.assertIn(b'201', head)
        run_id = json.loads(body)['id']
        self.runtime.start(run_id)
        self.wait(lambda: self.store.run(run_id)['status'] == 'waiting_input')
        _, body = self.request('/api/runs/'+run_id)
        request = json.loads(body)['inputs'][0]
        head, _ = self.request(f'/api/runs/{run_id}/answer', {'id': request['id'], 'answer': '后端开发三年'})
        self.assertIn(b'200', head)
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'], 'completed')

    def test_answer_cannot_target_other_task_or_invalid_input(self):
        run_id = self.start('[ask]')
        self.wait(lambda: self.store.run(run_id)['status'] == 'waiting_input')
        request = self.store.inputs(run_id)[0]
        other = self.runtime.create('另一个任务', str(self.workspace))
        for task_id, request_id, answer in [(other,request['id'],'回答'), (run_id,request['id'],' '), (run_id,[],'回答')]:
            with self.assertRaises(ValueError): self.runtime.answer(task_id,request_id,answer)
        self.assertEqual(self.store.inputs(run_id)[0]['status'], 'pending')
        self.runtime.answer(run_id,request['id'],'有效回答')
        self.finish(run_id)

    def test_flow_module_is_served_with_javascript_mime(self):
        head, body = self.request('/flow.mjs')
        self.assertIn(b'200', head)
        self.assertIn(b'text/javascript', head)
        self.assertIn(b'export function buildFlow', body)
        _, html = self.request('/')
        self.assertIn(b'type="module"', html)
        self.assertIn('已完成'.encode(), html)
        self.assertNotIn('待确认'.encode(), html)

    def test_custom_agent_can_be_delegated_with_prompt_snapshot(self):
        self.runtime.agents.save({'name':'interview-coach','description':'模拟面试','prompt':'根据用户资料模拟面试','tools':['read']})
        run_id=self.start('[parallel][custom]')
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'],'completed',self.store.run(run_id)['reason'])
        self.assertTrue(any(a['role']=='interview-coach' for a in self.store.agents(run_id)))
        snapshots=list((self.store.path.parent/'agent-snapshots').glob('interview-coach-*.md'))
        self.assertEqual(len(snapshots),1)
        self.assertIn('模拟面试',snapshots[0].read_text())

    def test_agent_library_http_save_toggle_import_and_delete(self):
        import json
        head,body=self.request('/api/agents',{'name':'interview-coach','description':'面试助手','prompt':'模拟面试','tools':['read']})
        self.assertIn(b'200',head)
        self.request('/api/agents/toggle',{'name':'interview-coach','enabled':False})
        _,body=self.request('/api/agents')
        self.assertFalse(next(a for a in json.loads(body)['agents'] if a['name']=='interview-coach')['enabled'])
        head,_=self.request('/api/agents/delete',{'name':'interview-coach'})
        self.assertIn(b'200',head)
        head,_=self.request('/api/agents',{'markdown':'---\nname: report-editor\ndescription: 编辑文档\ntools: read, find\n---\n\n负责文档审阅。'})
        self.assertIn(b'200',head)

    def test_retry_cannot_discard_unfinished_saved_plan(self):
        import json
        run_id=self.runtime.create('完成两个阶段',str(self.workspace))
        plan=[{'id':'research','title':'调查','task':'调查资料','agent':'researcher','depends_on':[],'status':'pending'}]
        self.store.emit(run_id,'plan.updated',{'steps':plan})
        self.store.emit(run_id,'status',{'status':'interrupted'})
        self.runtime.start(run_id)
        self.finish(run_id)
        self.assertEqual(self.store.run(run_id)['status'],'failed')
        self.assertIn('未完成步骤',self.store.run(run_id)['reason'])
        self.assertEqual(json.loads(self.store.run(run_id)['plan'])[0]['status'],'pending')

    def test_document_upload_http_read_download_and_cross_scope(self):
        import base64,json
        first=self.runtime.conversations.create('chat',str(self.workspace))['id']
        other=self.runtime.conversations.create('chat',str(self.workspace))['id']
        head,body=self.request('/api/conversations/'+first+'/documents',{'name':'paper.md','content_base64':base64.b64encode(b'# Reading note').decode()})
        self.assertIn(b'201',head)
        document=json.loads(body)['document']
        head,body=self.request(f'/api/conversations/{first}/documents/{document["id"]}?page=1')
        self.assertIn(b'200',head);self.assertIn('Reading note',json.loads(body)['text'])
        head,_=self.request(f'/api/conversations/{other}/documents/{document["id"]}?page=1')
        self.assertIn(b'400',head)
        head,body=self.request(f'/api/conversations/{first}/documents/{document["id"]}/download')
        self.assertIn(b'Content-Disposition: attachment',head)
        self.assertEqual(body,b'# Reading note')

    def test_markdown_and_picker_modules_are_local_and_served(self):
        for path in ['/markdown.mjs','/workspace-picker.mjs','/vendor/marked.mjs','/progress.mjs','/timeline.mjs','/sidebar.mjs','/trace.mjs','/vendor/temml.mjs']:
            head,_=self.request(path)
            self.assertIn(b'200',head);self.assertIn(b'text/javascript',head)
        head,body=self.request('/chat.css')
        self.assertIn(b'200',head);self.assertIn(b'text/css',head)
        head,body=self.request('/vendor/temml.css')
        self.assertIn(b'200',head);self.assertIn(b'text/css',head)
        self.assertNotIn(b'@font-face',body)

    def test_composer_mode_endpoint_preserves_conversation(self):
        import json
        chat=self.runtime.conversations.create('chat',str(self.workspace))
        head,body=self.request('/api/conversations/'+chat['id']+'/mode',{'mode':'planning'})
        self.assertIn(b'200',head)
        result=json.loads(body)
        self.assertEqual(result['id'],chat['id'])
        self.assertEqual(result['mode'],'planning')
        self.assertIsNone(result['task_id'])
        self.assertEqual(self.store.runs(),[])
        head,_=self.request('/api/conversations/'+chat['id']+'/mode',{'mode':'invalid'})
        self.assertIn(b'400',head)

    def test_conversation_rename_delete_http(self):
        import json
        chat=self.runtime.conversations.create('chat',str(self.workspace))
        route='/api/conversations/'+chat['id']
        head,body=self.request(route+'/rename',{'title':'论文阅读'})
        self.assertIn(b'200',head);self.assertEqual(json.loads(body)['title'],'论文阅读')
        head,_=self.request(route+'/rename',{'title':''});self.assertIn(b'400',head)
        head,body=self.request(route+'/delete',{})
        self.assertIn(b'200',head);self.assertTrue(json.loads(body)['deleted'])
        head,_=self.request(route);self.assertIn(b'404',head)
        self.assertNotIn(chat['id'],[c['id'] for c in self.runtime.conversations.list()])

    def test_new_empty_conversation_can_be_deleted_without_workspace_or_messages(self):
        import json
        head,body=self.request('/api/conversations',{'mode':'chat'})
        self.assertIn(b'201',head)
        chat=json.loads(body)
        self.assertEqual(chat['title'],'新对话')
        self.assertEqual(chat['messages'],[])
        self.assertEqual(chat['workspace'],'')
        self.assertIsNone(chat['task_id'])
        head,body=self.request('/api/conversations/'+chat['id']+'/delete',{})
        self.assertIn(b'200',head);self.assertTrue(json.loads(body)['deleted'])
        self.assertNotIn(chat['id'],[c['id'] for c in self.runtime.conversations.list()])
