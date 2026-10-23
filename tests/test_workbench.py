"""Store compatibility and startup/HTTP regressions for the current runtime."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from workbench.store import Store
from workbench.runtime import Runtime
from workbench.__main__ import make_handler


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.store = Store(self.root / 'data/state.sqlite3')
        self.runtime = Runtime(self.store)
        self.runtime.config.add_workspace(str(self.workspace))

    def tearDown(self):
        self.runtime.shutdown()
        self.store.db.close()
        self.temp.cleanup()

    def create(self):
        return self.runtime.create('检查项目', str(self.workspace))

    def request(self, path, data=None, origin=None, host='127.0.0.1:4310'):
            class Connection:
                def __init__(self, request):
                    self.input = io.BytesIO(request)
                    self.output = bytearray()
                def makefile(self, *args):
                    return self.input
                def sendall(self, data):
                    self.output.extend(data)
            body = json.dumps(data).encode() if data is not None else b''
            headers = f"{'POST' if data is not None else 'GET'} {path} HTTP/1.0\r\nHost: {host}\r\n"
            if data is not None:
                headers += f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n'
            if origin:
                headers += f'Origin: {origin}\r\n'
            connection = Connection((headers + '\r\n').encode() + body)
            make_handler(self.store, self.runtime)(connection, ('127.0.0.1', 1234), None)
            head, payload = bytes(connection.output).split(b'\r\n\r\n', 1)
            return head, payload

    def test_legacy_database_is_migrated_without_deleting_events(self):
            import sqlite3
            path = self.root / 'legacy.sqlite3'
            db = sqlite3.connect(path)
            db.executescript("CREATE TABLE runs(id TEXT PRIMARY KEY,prompt TEXT,status TEXT,created REAL); CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT,kind TEXT,data TEXT,created REAL);")
            db.execute('INSERT INTO runs VALUES(?,?,?,?)', ('legacy', '旧任务', 'completed', time.time()))
            db.execute('INSERT INTO events(run_id,kind,data,created) VALUES(?,?,?,?)', ('legacy', 'log', '{"text":"旧记录"}', time.time()))
            db.commit()
            db.close()
            migrated = Store(path)
            try:
                self.assertEqual(migrated.run('legacy')['status'], 'completed')
                self.assertEqual(migrated.events('legacy')[0]['data']['text'], '旧记录')
            finally:
                migrated.db.close()

    def test_card_metadata_counts_distinct_files_and_updated_time(self):
            run_id = self.create()
            for path in ('a.py', 'a.py', 'b.py'):
                self.store.emit(run_id, 'file.changed', {'path': path, 'diff': ''})
            task = next(t for t in self.runtime.board()['tasks'] if t['id'] == run_id)
            self.assertEqual(task['file_change_count'], 2)
            self.assertGreaterEqual(task['updated'], task['created'])

    def test_health_endpoint_returns_without_stream(self):
            head, body = self.request('/api/health')
            self.assertIn(b'200 OK', head)
            self.assertIn(b'Content-Length:', head)
            self.assertEqual(json.loads(body), {'status': 'ok', 'service': 'python-agent-workbench'})

    def test_duplicate_launch_does_not_change_running_tasks(self):
            import errno
            import importlib
            module = importlib.import_module('workbench.__main__')
            with patch.object(module, 'WorkbenchServer', side_effect=OSError(errno.EADDRINUSE, 'in use')):
                with patch.object(module, 'Store') as store_factory:
                    with patch('sys.argv', ['workbench']), patch('sys.stderr', new_callable=io.StringIO) as error:
                        with self.assertRaises(SystemExit) as exc:
                            module.main()
                        self.assertEqual(exc.exception.code, 1)
                        self.assertIn('已被占用', error.getvalue())
                    store_factory.assert_not_called()


    def test_http_boundaries_reject_cross_origin_and_invalid_mutations(self):
        before=len(self.store.runs())
        payload={'prompt':'untrusted','workspace':str(self.workspace)}
        head,_=self.request('/api/runs',payload,host='attacker.invalid')
        self.assertIn(b'403',head)
        head,_=self.request('/api/runs',payload,origin='https://attacker.invalid')
        self.assertIn(b'403',head)
        head,_=self.request('/api/runs',{'prompt':''})
        self.assertIn(b'400',head)
        self.assertEqual(len(self.store.runs()),before)

    def test_task_context_http_versions_and_origin_boundary(self):
        task_id=self.create()
        head,body=self.request(f'/api/runs/{task_id}/context')
        self.assertIn(b'200',head);state=json.loads(body)
        self.assertEqual(state['goal'],'检查项目')
        payload={'revision':state['revision'],'kind':'constraint','text':'仅使用本地资料'}
        head,_=self.request(f'/api/runs/{task_id}/context',payload,origin='https://attacker.invalid')
        self.assertIn(b'403',head)
        head,body=self.request(f'/api/runs/{task_id}/context',payload)
        self.assertIn(b'200',head);saved=json.loads(body)
        self.assertEqual(saved['items'][0]['text'],'仅使用本地资料')
        head,body=self.request(f'/api/runs/{task_id}/context',payload)
        self.assertIn(b'400',head);self.assertIn('已更新',json.loads(body)['error'])
        head,_=self.request('/task-context.mjs')
        self.assertIn(b'200',head);self.assertIn(b'text/javascript',head)

    def test_memory_policy_catalog_and_privacy_http_boundaries(self):
        head,body=self.request('/api/memory');self.assertIn(b'200',head)
        initial=json.loads(body);self.assertFalse(initial['policy']['auto_constraints'])
        payload={'revision':initial['policy']['revision'],'long_term_write':True,'auto_constraints':True,'long_term_read':True}
        head,_=self.request('/api/settings/memory',payload,origin='https://attacker.invalid');self.assertIn(b'403',head)
        head,_=self.request('/api/settings/memory',payload);self.assertIn(b'200',head)
        head,_=self.request('/api/settings/memory',payload);self.assertIn(b'400',head)
        head,body=self.request('/api/memory',{'action':'save','revision':initial['revision'],'text':'以后输出用中文','scope':'global'})
        self.assertIn(b'200',head);saved=json.loads(body)
        head,_=self.request('/api/memory',{'action':'clear','revision':saved['revision'],'confirm':False});self.assertIn(b'400',head)
        head,_=self.request('/api/memory',{'action':'save','revision':saved['revision'],'id':[],'text':'bad'});self.assertIn(b'400',head)
        head,_=self.request('/memory-settings.mjs');self.assertIn(b'200',head);self.assertIn(b'text/javascript',head)
