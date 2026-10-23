import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workbench.archives import create_archive,read_archive
from workbench.store import Store
from workbench.runtime import Runtime


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name);self.workspace=self.root/'project';self.workspace.mkdir()
        self.store=Store(self.root/'data/state.sqlite3');self.runtime=Runtime(self.store)
        self.run_id=self.store.create('论文阅读 / 任务总结',str(self.workspace),'pi','default')

    def tearDown(self):
        self.runtime.shutdown();self.store.db.close();self.temp.cleanup()

    def archive(self):
        return create_archive(self.store,self.store.run(self.run_id),self.workspace,'完成论文阅读并输出笔记',self.root/'data')

    def test_summary_copies_task_files_and_preserves_originals_and_versions(self):
        (self.workspace/'notes.md').write_text('# 阅读笔记')
        (self.workspace/'paper.txt').write_text('来源资料')
        self.store.emit(self.run_id,'file.changed',{'path':'notes.md'})
        self.store.emit(self.run_id,'tool.started',{'name':'read','arguments':{'path':'paper.txt'},'agent_id':'researcher'})
        first=self.archive();folder=self.workspace/first['directory']
        self.assertEqual((folder/'交付文件/notes.md').read_text(),'# 阅读笔记')
        self.assertEqual((folder/'参考文件/paper.txt').read_text(),'来源资料')
        self.assertTrue((self.workspace/'notes.md').exists())
        self.assertIn('完成论文阅读',(self.workspace/first['summary_path']).read_text())
        manifest=json.loads((folder/'清单.json').read_text())
        self.assertEqual(len(manifest['files']),2)
        self.assertTrue(all(len(f['sha256'])==64 for f in manifest['files']))
        (self.workspace/'notes.md').write_text('新版笔记')
        second=self.archive();self.assertNotEqual(first['directory'],second['directory'])
        self.assertEqual((folder/'交付文件/notes.md').read_text(),'# 阅读笔记')

    def test_attached_source_is_collected_from_task_context(self):
        chat=self.runtime.conversations.create('chat')
        doc=self.runtime.documents.upload(chat['id'],'paper.txt',base64.b64encode(b'original paper').decode())
        context_id,_=self.runtime.conversations.snapshot(chat['id'])
        with self.store.lock,self.store.db:self.store.db.execute('UPDATE context_snapshots SET task_id=? WHERE id=?',(self.run_id,context_id))
        result=self.archive();files=list((self.workspace/result['directory']/'来源附件').iterdir())
        self.assertEqual(len(files),1);self.assertEqual(files[0].read_bytes(),b'original paper')

    def test_unsafe_paths_and_large_files_are_reported_not_copied(self):
        outside=self.root/'private.txt';outside.write_text('private')
        (self.workspace/'link.txt').symlink_to(outside)
        (self.workspace/'.env').write_text('secret')
        (self.workspace/'large.txt').write_text('too large')
        for path in ['../private.txt','link.txt','.env','large.txt']:
            self.store.emit(self.run_id,'file.changed',{'path':path})
        with patch('workbench.archives.MAX_FILE',3):result=self.archive()
        self.assertEqual(result['file_count'],0);self.assertEqual(result['skipped_count'],4)
        manifest=json.loads((self.workspace/result['directory']/'清单.json').read_text())
        self.assertTrue(all(f['status']=='skipped' and f['reason'] for f in manifest['files']))

    def test_archive_root_symlink_cannot_write_outside_workspace(self):
        target=self.root/'outside';target.mkdir()
        (self.workspace/'总结').symlink_to(target,target_is_directory=True)
        with self.assertRaises(ValueError):self.archive()
        self.assertEqual(list(target.iterdir()),[])

    def test_special_file_is_skipped_without_blocking_and_dot_paths_stay_in_archive(self):
        import os
        os.mkfifo(self.workspace/'pipe')
        (self.workspace/'sub').mkdir();(self.workspace/'note.md').write_text('safe')
        self.store.emit(self.run_id,'file.changed',{'path':'pipe'})
        self.store.emit(self.run_id,'file.changed',{'path':'sub/../note.md'})
        result=self.archive();folder=self.workspace/result['directory']
        self.assertEqual(result['skipped_count'],1)
        self.assertEqual((folder/'交付文件/note.md').read_text(),'safe')

    def test_summary_reader_uses_only_recorded_safe_archive_path(self):
        result=self.archive();self.store.emit(self.run_id,'task.archived',result)
        read=read_archive(self.store,self.store.run(self.run_id),self.workspace)
        self.assertIn('任务完成总结',read['summary_markdown'])
        self.store.emit(self.run_id,'task.archived',{'summary_path':'../private.txt'})
        with self.assertRaises(ValueError):read_archive(self.store,self.store.run(self.run_id),self.workspace)
