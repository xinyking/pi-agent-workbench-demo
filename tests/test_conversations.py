"""Q&A/task separation, contextual handoff, planning messages and restart storage."""
import tempfile
import time
import unittest
from pathlib import Path
from workbench.store import Store
from workbench.runtime import Runtime

FIXTURE=str(Path(__file__).parent/'fixtures/fake_pi.py')


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.workspace=Path(self.temp.name)/'project';self.workspace.mkdir()
        self.store=Store(Path(self.temp.name)/'data/state.sqlite3')
        self.runtime=Runtime(self.store)
        self.runtime.config.binary=FIXTURE;self.runtime.config.version='fixture'
        self.runtime.config.add_workspace(str(self.workspace))
        self.chats=self.runtime.conversations

    def wait(self,condition):
        end=time.monotonic()+7
        while time.monotonic()<end:
            if condition():return
            time.sleep(.01)
        self.fail('conversation did not reach expected state')

    def tearDown(self):
        self.runtime.shutdown()
        self.wait(lambda:not self.runtime.active and not self.chats.active)
        self.store.db.close();self.temp.cleanup()

    def chat(self,mode='chat'):
        return self.chats.create(mode,str(self.workspace))['id']

    def send(self,chat_id,text):
        self.chats.send(chat_id,text)
        self.wait(lambda:chat_id not in self.chats.active)

    def test_plain_qa_does_not_create_task_and_reuses_session(self):
        chat_id=self.chat()
        self.send(chat_id,'我有三年 Python 经验，希望找后端工作')
        self.send(chat_id,'你建议先准备什么？')
        self.assertEqual(self.store.runs(),[])
        chat=self.chats.get(chat_id)
        self.assertEqual(len([m for m in chat['messages'] if m['role']=='user']),2)
        self.assertEqual(chat['status'],'idle')
        sessions=[e['data']['session_id'] for e in self.chats.events(chat_id) if e['kind']=='session.connected']
        self.assertEqual(len(sessions),2);self.assertEqual(sessions[0],sessions[1])

    def test_rename_is_persistent_and_not_overwritten_by_first_message(self):
        chat_id=self.chat()
        self.chats.rename(chat_id,'新对话')
        self.chats.message(chat_id,'user','不要用这条消息覆盖手工名称')
        self.assertEqual(self.chats.get(chat_id)['title'],'新对话')
        self.chats.rename(chat_id,'  论文阅读  ')
        self.assertEqual(self.chats.get(chat_id)['title'],'论文阅读')
        for title in ('','x'*81,'bad\nname',None):
            with self.assertRaises(ValueError):self.chats.rename(chat_id,title)

    def test_delete_hides_chat_but_preserves_linked_task_context_and_documents(self):
        import base64
        chat_id=self.chat('planning')
        doc=self.runtime.documents.upload(chat_id,'paper.txt',base64.b64encode(b'original evidence').decode())
        task_id=self.chats.send(chat_id,'读取论文')['task_id']
        self.wait(lambda:task_id not in self.runtime.active)
        self.chats.delete(chat_id)
        self.assertNotIn(chat_id,[c['id'] for c in self.chats.list()])
        with self.assertRaises(ValueError):self.chats.get(chat_id)
        self.assertEqual(self.store.run(task_id)['status'],'completed')
        with self.store.lock:context_id=self.store.db.execute('SELECT id FROM context_snapshots WHERE task_id=?',(task_id,)).fetchone()['id']
        self.assertIn('读取论文',self.chats.context(task_id,context_id)['text'])
        self.assertIn('original evidence',self.runtime.documents.read(chat_id,doc['id'])['text'])

    def test_delete_rejects_active_chat(self):
        import threading
        chat_id=self.chat();self.chats.active[chat_id]=threading.Event()
        try:
            with self.assertRaises(ValueError):self.chats.delete(chat_id)
            self.assertEqual(self.chats.get(chat_id)['id'],chat_id)
        finally:self.chats.active.pop(chat_id)

    def test_execution_snapshot_tracks_real_events_and_excludes_tool_bodies(self):
        chat_id=self.chat()
        self.send(chat_id,'回答一个问题')
        kinds=[e['kind'] for e in self.chats.get(chat_id)['execution']]
        self.assertIn('request.started',kinds)
        self.assertIn('session.connected',kinds)
        self.assertIn('answer.started',kinds)
        self.chats.execution(chat_id,'tool.started',{'name':'write','arguments':{'path':'notes.md','content':'private contents'},'result':'private result'})
        event=self.chats.get(chat_id)['execution'][-1]
        self.assertEqual(event['arguments'],{'path':'notes.md'})
        self.assertNotIn('private',str(event))
        self.assertIn('created',event)

    def test_composer_mode_switch_preserves_conversation_and_creates_task_on_send(self):
        chat_id=self.chat()
        self.send(chat_id,'目标杭州，不考虑外包')
        before=self.chats.get(chat_id)['messages']
        changed=self.chats.set_mode(chat_id,'planning')
        self.assertEqual(changed['messages'],before)
        self.assertIsNone(changed['task_id'])
        task_id=self.chats.send(chat_id,'基于前面内容制定求职计划')['task_id']
        self.wait(lambda:task_id not in self.runtime.active)
        self.assertEqual(self.chats.get(chat_id)['task_id'],task_id)
        self.assertTrue(any('不考虑外包' in r['data'] for r in self.store.db.execute('SELECT data FROM context_snapshots WHERE task_id=?',(task_id,))))
        self.chats.set_mode(chat_id,'chat')
        self.send(chat_id,'先讨论一下准备方法')
        self.assertEqual(len(self.store.runs()),1)

    def test_mode_switch_validates_value_and_blocks_active_execution(self):
        chat_id=self.chat()
        with self.assertRaises(ValueError):self.chats.set_mode(chat_id,'unknown')
        import threading
        self.chats.active[chat_id]=threading.Event()
        try:
            with self.assertRaises(ValueError):self.chats.set_mode(chat_id,'planning')
            self.assertEqual(self.chats.get(chat_id)['mode'],'chat')
        finally:self.chats.active.pop(chat_id)

    def test_planning_progress_forwards_child_tools_and_plan_without_outputs(self):
        chat_id=self.chat('planning')
        task_id=self.chats.send(chat_id,'制定计划')['task_id']
        self.wait(lambda:task_id not in self.runtime.active)
        self.chats.on_task_event(task_id,'tool.started',{'agent_id':'child','name':'read','arguments':{'path':'paper.txt'}})
        event=self.chats.get(chat_id)['execution'][-1]
        self.assertEqual(event['agent_id'],'child')
        self.assertEqual(event['name'],'read')
        self.assertEqual(event['arguments']['path'],'paper.txt')

    def test_planning_creates_real_task_and_syncs_result(self):
        chat_id=self.chat('planning')
        result=self.chats.send(chat_id,'制定求职计划')
        task_id=result['task_id']
        self.wait(lambda:task_id not in self.runtime.active)
        self.assertEqual(len(self.store.runs()),1)
        self.assertEqual(self.store.run(task_id)['status'],'completed')
        chat=self.chats.get(chat_id)
        self.assertEqual(chat['task_id'],task_id)
        self.assertTrue(any(m['role']=='assistant' and '可观察结果' in m['text'] for m in chat['messages']))

    def test_qa_converts_to_task_with_original_context(self):
        chat_id=self.chat()
        self.send(chat_id,'三年后端经验，目标杭州，不考虑外包')
        self.send(chat_id,'[create-task] 请基于前面内容创建任务')
        chat=self.chats.get(chat_id);task_id=chat['task_id']
        self.assertIsNotNone(task_id)
        self.wait(lambda:task_id not in self.runtime.active)
        self.assertEqual(len(self.store.runs()),1)
        event=next(e for e in self.chats.events(chat_id) if e['kind']=='task.created')
        snapshot=self.chats.context(task_id,event['data']['context_id'])
        self.assertIn('不考虑外包',snapshot['text'])
        self.assertEqual(len(snapshot['digest']),64)
        self.assertEqual(chat['mode'],'chat')

    def test_user_can_create_agent_through_chat(self):
        chat_id=self.chat();self.send(chat_id,'[create-agent] 请创建面试辅导子 Agent')
        self.assertTrue(any(a['name']=='interview-mentor' for a in self.runtime.agents.list()))
        self.assertFalse(self.store.runs())

    def test_planning_answers_missing_information_in_same_conversation(self):
        chat_id=self.chat('planning')
        task_id=self.chats.send(chat_id,'[ask] 帮我找工作')['task_id']
        self.wait(lambda:self.store.run(task_id)['status']=='waiting_input')
        self.assertEqual(self.chats.get(chat_id)['status'],'attention')
        self.chats.send(chat_id,'有三年开发经验，目标后端岗位')
        self.wait(lambda:task_id not in self.runtime.active)
        self.assertEqual(self.store.run(task_id)['status'],'completed')
        self.assertEqual(len(self.store.runs()),1)

    def test_context_snapshot_is_immutable_and_cannot_cross_tasks(self):
        chat_id=self.chat();self.chats.message(chat_id,'user','最早的明确约束')
        context_id,_=self.chats.snapshot(chat_id)
        task_id=self.runtime.create('测试',str(self.workspace))
        with self.store.lock,self.store.db:self.store.db.execute('UPDATE context_snapshots SET task_id=? WHERE id=?',(task_id,context_id))
        original=self.chats.context(task_id,context_id)
        self.chats.message(chat_id,'user','后面的消息')
        self.assertEqual(original,self.chats.context(task_id,context_id))
        with self.assertRaises(ValueError):self.chats.context('other-task',context_id)

    def test_validation_does_not_create_tasks_for_missing_workspace(self):
        with self.assertRaises(ValueError):self.chats.create('planning')
        chat_id=self.chats.create('chat')['id']
        self.send(chat_id,'不需要工作区的普通问题')
        self.assertFalse(self.store.runs())
        self.assertEqual(self.chats.get(chat_id)['status'],'idle')

    def test_planning_workspace_cannot_silently_change(self):
        chat_id=self.chat('planning')
        self.chats.send(chat_id,'制定计划')
        self.wait(lambda:not self.runtime.active)
        other=Path(self.temp.name)/'other';other.mkdir()
        self.runtime.config.add_workspace(str(other))
        with self.assertRaisesRegex(ValueError,'固定在原工作区'):
            self.chats.send(chat_id,'继续',str(other))
        self.assertEqual(self.chats.get(chat_id)['workspace'],str(self.workspace))

    def test_stream_deltas_and_snapshot_cursor_resume_without_duplication(self):
        chat_id=self.chat()
        message_id=self.chats.message(chat_id,'assistant','','streaming')
        self.chats.update_message(chat_id,message_id,'第一段')
        snapshot=self.chats.get(chat_id)
        self.assertEqual(snapshot['messages'][-1]['text'],'第一段')
        self.chats.update_message(chat_id,message_id,'第一段第二段')
        events=self.chats.events(chat_id,snapshot['cursor'])
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['kind'],'message.delta')
        self.assertEqual(events[0]['data']['delta'],'第二段')
        restored=snapshot['messages'][-1]['text']+events[0]['data']['delta']
        self.assertEqual(restored,self.chats.get(chat_id)['messages'][-1]['text'])
        self.chats.update_message(chat_id,message_id,'修改后的最终结果','completed')
        self.assertEqual(self.chats.events(chat_id)[-1]['kind'],'message')

    def test_unreadable_document_does_not_block_conversation(self):
        import base64
        from unittest.mock import patch
        chat_id=self.chat()
        with patch('workbench.documents.importlib.util.find_spec',return_value=None):
            doc=self.runtime.documents.upload(chat_id,'unreadable.pdf',base64.b64encode(b'%PDF-1.4\n').decode())
        self.assertEqual(doc['status'],'needs_parser')
        self.send(chat_id,'讨论其他可用资料')
        self.assertEqual(self.chats.get(chat_id)['status'],'idle')
        self.assertTrue(self.chats.get(chat_id)['messages'])
