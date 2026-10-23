"""Task memory authority, versioning, isolation and recoverable context assembly."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from workbench.runtime import Runtime
from workbench.store import Store
from workbench.task_context import ASSEMBLY_BYTE_BUDGET


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root/'project'; self.workspace.mkdir()
        self.store = Store(self.root/'data/state.sqlite3')
        self.runtime = Runtime(self.store)
        self.runtime.config.add_workspace(str(self.workspace))
        self.context = self.runtime.task_context
        self.task = self.runtime.create('整理本地资料，使用中文', str(self.workspace))

    def tearDown(self):
        self.runtime.shutdown(); self.store.db.close(); self.temp.cleanup()

    def save(self, **data):
        return self.context.update(self.task, {'revision': self.context.overview(self.task)['revision'], **data})

    def test_user_constraints_are_versioned_and_cannot_be_overwritten_by_agent(self):
        state = self.save(kind='constraint', text='禁止联网')
        item = state['items'][0]
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            self.context.tool(self.task, {'action':'save','id':item['id'],'revision':state['revision'],'kind':'progress','text':'允许联网'})
        with self.assertRaisesRegex(ValueError, '不能自行'):
            self.context.tool(self.task, {'action':'save','revision':state['revision'],'kind':'constraint','text':'擅自添加要求'})
        self.save(id=item['id'], kind='constraint', text='仅使用本地 PDF')
        with self.assertRaisesRegex(ValueError, '已更新'):
            self.context.update(self.task, {'revision':state['revision'],'action':'remove','id':item['id']})
        with self.store.lock:
            old = json.loads(self.store.db.execute('SELECT data FROM task_memory_versions WHERE task_id=? AND revision=?',(self.task,state['revision'])).fetchone()['data'])
        self.assertEqual(old['items'][0]['text'], '禁止联网')
        self.assertEqual(self.context.overview(self.task)['items'][0]['text'], '仅使用本地 PDF')

    def test_agent_reports_have_sources_and_cannot_edit_other_agents_notes(self):
        state = self.context.tool(self.task, {'action':'save','revision':1,'kind':'progress','text':'已完成页面提取'}, 'worker-a')
        item = state['items'][0]
        self.assertEqual((item['authority'],item['source']['agent_id']),('agent_note','worker-a'))
        with self.assertRaisesRegex(ValueError, '自己的'):
            self.context.tool(self.task, {'action':'remove','revision':state['revision'],'id':item['id']},'worker-b')
        updated = self.context.tool(self.task, {'action':'save','revision':state['revision'],'id':item['id'],'kind':'progress','text':'已核验来源'},'worker-a')
        self.assertEqual(updated['items'][0]['text'],'已核验来源')

    def test_fresh_projection_preserves_constraints_and_does_not_append_transcript(self):
        self.save(kind='constraint', text='预算不超过 200，禁止联网')
        self.context.request(self.task, '城市改为上海，其余要求保持')
        before = self.store.messages(self.task)
        first = self.context.assemble(self.task)
        payload = json.loads(first['text'])
        self.assertEqual(payload['latest_request']['text'], '城市改为上海，其余要求保持')
        self.assertIn('预算不超过 200', payload['user_memory'][0]['text'])
        state = self.context.overview(self.task)
        self.context.tool(self.task, {'action':'save','revision':state['revision'],'kind':'decision','text':'先检查附件目录'})
        second = self.context.assemble(self.task)
        self.assertGreater(second['revision'],first['revision'])
        self.assertIn('先检查附件目录', second['text'])
        self.assertEqual(self.store.messages(self.task),before)
        self.assertEqual(hashlib.sha256(second['text'].encode()).hexdigest(),second['digest'])

    def test_unicode_budget_keeps_user_constraints_and_long_requests_are_recoverable(self):
        self.context.request(self.task, '长要求。'*4000)
        for index in range(6): self.save(kind='constraint',text=str(index)+'中文约束'*480)
        projection = self.context.assemble(self.task)
        self.assertLessEqual(len(projection['text'].encode()), ASSEMBLY_BYTE_BUDGET)
        payload = json.loads(projection['text'])
        self.assertEqual(len(payload['user_memory']),6)
        self.assertTrue(payload['latest_request']['is_excerpt'])
        offset=0; pieces=[]
        while True:
            page = self.context.tool(self.task, {'action':'read_source','field':'latest_request','offset':offset})
            pieces.append(page['text'])
            if page['next_offset'] is None: break
            offset=page['next_offset']
        self.assertEqual(''.join(pieces),'长要求。'*4000)
        with self.assertRaisesRegex(ValueError, '上限'):
            self.save(kind='constraint',text='新约束'*600)
        self.assertEqual(len(self.context.overview(self.task)['items']),6)

    def test_tool_artifact_round_trip_digest_and_task_isolation(self):
        text='原始工具结果\n'*2000
        stored=self.context.offload(self.task,'main',{'text':text,'name':'read','tool_call_id':'read-1'})
        duplicate=self.context.offload(self.task,'main',{'text':text,'name':'read','tool_call_id':'read-1'})
        self.assertEqual(stored['artifact_id'], duplicate['artifact_id'])
        pieces=[];offset=0
        while True:
            page=self.context.artifact(self.task,stored['artifact_id'],offset);pieces.append(page['text'])
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertEqual(''.join(pieces),text)
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(),stored['digest'])
        other=self.runtime.create('另一个任务',str(self.workspace))
        with self.assertRaisesRegex(ValueError,'无权'):
            self.context.artifact(other,stored['artifact_id'])
        for offset in (-1,True,'0'):
            with self.assertRaises(ValueError):self.context.artifact(self.task,stored['artifact_id'],offset)
        item=self.save(kind='constraint',text='仅本任务使用')['items'][0]
        with self.assertRaisesRegex(ValueError,'不存在'):
            self.context.update(other,{'revision':1,'id':item['id'],'action':'remove'})

    def test_restart_and_lazy_legacy_initialization_preserve_data(self):
        self.save(kind='constraint',text='不能覆盖用户文件')
        self.store.db.close()
        self.store=Store(self.root/'data/state.sqlite3');self.runtime.store=self.store
        self.context=self.runtime.task_context
        self.assertEqual(self.context.overview(self.task)['items'][0]['text'],'不能覆盖用户文件')
        legacy=self.store.create('旧任务目标',str(self.workspace),'pi','default')
        self.assertEqual(self.context.overview(legacy)['goal'],'旧任务目标')
        self.assertEqual(self.context.overview(legacy)['revision'],1)
