"""Registry persistence, Pi Markdown interoperability, and execution boundaries."""
import tempfile
import unittest
from pathlib import Path
from workbench.agent_registry import AgentRegistry
from workbench.store import Store


class AgentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'state.sqlite3')
        self.registry = AgentRegistry(self.store)
        self.agent = {'name':'interview-coach','description':'模拟面试','prompt':'根据真实经历模拟面试，不编造事实。','tools':['read','find'],'model':''}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_builtin_general_and_coding_roles_are_available(self):
        names = {a['name'] for a in self.registry.list()}
        self.assertTrue({'planner','researcher','writer','analyst','critic','career-coach','context-curator','project-coordinator','task-worker'}.issubset(names))
        self.assertEqual(len(names),14)

    def test_custom_agent_round_trips_through_pi_markdown(self):
        saved = self.registry.save(self.agent)
        self.assertEqual(AgentRegistry.parse(AgentRegistry.markdown(saved)), saved)
        self.assertEqual(AgentRegistry(self.store).list()[-1]['name'],self.agent['name'])

    def test_prompt_snapshot_is_immutable_and_content_addressed(self):
        first=self.registry.snapshot(self.agent)
        second=self.registry.snapshot({**self.agent,'prompt':'新的职责'})
        self.assertNotEqual(first,second)
        self.assertIn('模拟面试',first.read_text())
        self.assertNotIn('新的职责',first.read_text())
        self.assertEqual(first,self.registry.snapshot(self.agent))
        self.assertEqual(first.stat().st_mode&0o777,0o600)

    def test_builtin_cannot_be_overwritten_or_deleted(self):
        with self.assertRaisesRegex(ValueError,'内置'): self.registry.save({**self.agent,'name':'planner'})
        with self.assertRaisesRegex(ValueError,'自定义'): self.registry.remove('planner')

    def test_disabled_roles_are_not_in_execution_catalog(self):
        self.registry.toggle('planner',False)
        self.assertNotIn('planner',{a['name'] for a in self.registry.list(enabled_only=True)})
        self.registry.toggle('planner',True)
        self.assertIn('planner',{a['name'] for a in self.registry.list(enabled_only=True)})

    def test_import_validation_blocks_path_and_unrecognized_tools(self):
        for fields in [{'name':'../../outside'}, {'tools':['workbench_delegate']}, {'tools':[]}, {'model':'--extension'}, {'description':''}]:
            with self.subTest(fields=fields),self.assertRaises(ValueError):self.registry.save({**self.agent,**fields})
        with self.assertRaises(ValueError):self.registry.save({'markdown':'plain text'})
        with self.assertRaises(ValueError):self.registry.toggle('planner','true')

    def test_delete_custom_agent_retains_builtin_roles(self):
        self.registry.save(self.agent)
        self.registry.remove(self.agent['name'])
        self.assertEqual(len(self.registry.list()),14)

    def test_parallel_snapshot_readers_only_observe_complete_role(self):
        from concurrent.futures import ThreadPoolExecutor
        def read_snapshot(_):
            path=self.registry.snapshot(self.agent)
            text=path.read_text()
            self.assertEqual(AgentRegistry.parse(text)['name'],self.agent['name'])
            self.assertIn('根据真实经历',text)
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            return path
        with ThreadPoolExecutor(max_workers=12) as workers:
            paths=list(workers.map(read_snapshot,range(80)))
        self.assertEqual(len(set(paths)),1)
        self.assertEqual(list(paths[0].parent.glob('.role-*')),[])

    def test_failed_snapshot_publication_leaves_no_partial_final_or_temp_file(self):
        from unittest.mock import patch
        with patch('workbench.agent_registry.os.replace',side_effect=OSError('injected publication failure')):
            with self.assertRaises(OSError):self.registry.snapshot(self.agent)
        self.assertEqual(list((self.store.path.parent/'agent-snapshots').iterdir()),[])
        self.assertIn('tools:',self.registry.snapshot(self.agent).read_text())
