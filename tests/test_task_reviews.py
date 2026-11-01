import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from workbench.store import Store
from workbench.ops.evaluation import task_review, evaluation_events
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from import_task_reviews import apply_reviews, evidence_hash


class TaskReviewsTest(unittest.TestCase):
    def test_review_append_is_idempotent_and_new_evidence_marks_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / 'state.sqlite3'
            store = Store(database)
            try:
                run_id = store.create('isolated task')
                store.emit(run_id, 'status', {'status': 'completed'})
                before = store.run(run_id)
                digest, cursor = evidence_hash(store.db, run_id)
                record = dict(run_id=run_id, verdict='partial', checks=[{'name': 'evidence', 'status': 'partial', 'reason': 'test'}], evidence_sha256=digest, evidence_last_id=cursor)
                self.assertEqual(apply_reviews(database, [record]), 1)
                self.assertEqual(apply_reviews(database, [record]), 0)
                self.assertEqual(store.run(run_id), before)
                self.assertFalse(task_review(store, run_id)['stale'])
                backups = list((Path(tmp) / 'backups').glob('*.sqlite3'))
                self.assertEqual(len(backups), 1)
                with sqlite3.connect(backups[0]) as backup:
                    self.assertEqual(backup.execute("SELECT COUNT(*) FROM events WHERE kind='evaluation.completed'").fetchone()[0], 0)
                store.emit(run_id, 'message.user', {'text': 'new requirement'})
                self.assertTrue(task_review(store, run_id)['stale'])
                with self.assertRaisesRegex(ValueError, '证据已变化'):
                    apply_reviews(database, [record])
                self.assertEqual(len(store.events(run_id)), 4)
            finally:
                store.db.close()

    def test_no_review_remains_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            try:
                self.assertIsNone(task_review(store, store.create('no review')))
            finally:
                store.db.close()


class EvaluationSnapshotTest(unittest.TestCase):
    def test_snapshot_keeps_complete_numeric_evidence_without_private_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'state.sqlite3')
            try:
                run_id = store.create('private prompt')
                for i in range(1002):
                    store.emit(run_id, 'message.started', {'id': str(i), 'text': 'private reply'})
                store.emit(run_id, 'tool.completed', {'id': 'tool', 'is_error': True, 'result': {'content': [{'type': 'text', 'text': '工作台拒绝越界或受保护的路径 /secret'}]}})
                store.emit(run_id, 'tool.completed', {'id': 'tool2', 'result': 'unexpected legacy string'})
                store.emit(run_id, 'message.delta', {'text': 'private streaming content'})
                events = evaluation_events(store, run_id)
                self.assertEqual(len(events), 1004)
                serialized = json.dumps(events, ensure_ascii=False)
                self.assertNotIn('private', serialized)
                self.assertNotIn('/secret', serialized)
                self.assertIn('工作台拒绝越界或受保护的路径', serialized)
                self.assertEqual(events[-1]['data']['result']['content'], [])
            finally:
                store.db.close()
