"""Evaluate the evaluation pipeline with positive and misleading-success controls."""
import json
import hashlib
import zipfile
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from evals.runner import ROOT,load_case,run_trial,run_suite


class EvaluationTests(unittest.TestCase):
    def test_good_cases_pass_with_independent_evidence_and_unknown_cost(self):
        for name in ['C01','C02','R03']:
            with self.subTest(case=name):
                result,evidence=run_trial(load_case(name))
                self.assertEqual(result['verdict'],'passed',result)
                self.assertTrue(evidence['events'])
                self.assertIsNone(result['cost']);self.assertIsNone(result['tokens'])
                self.assertIsNone(result['model_requests'])
                if name=='C02':self.assertEqual(result['agent_spawns'],4)
                if name=='R03':self.assertEqual(result['agent_spawns'],1)

    def test_completed_wrong_delivery_is_a_business_failure(self):
        for name in ['C01','C02']:
            with self.subTest(case=name):
                result,_=run_trial(load_case(name),'broken')
                self.assertEqual(result['run_status'],'completed')
                self.assertEqual(result['verdict'],'failed')
                self.assertTrue(any(not c['passed'] and c['id'].startswith('holdout-') for c in result['checks']))

    def test_timeout_remains_failed_and_preserves_trace(self):
        result,evidence=run_trial(load_case('C02'),timeout=.02)
        self.assertEqual(result['verdict'],'failed')
        self.assertIn('TimeoutError',result['error'])
        self.assertTrue(evidence['events'])

    def test_report_records_failed_trials_in_denominator_and_refuses_overwrite(self):
        parent=ROOT/'artifacts/evals';parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='eval-test-',dir=parent));output=temporary/'results'
        try:
            result=run_suite(['C01'],output,repeat=2,variant='broken')
            self.assertEqual((result['trials'],result['failed']),(2,2))
            rows=[json.loads(line) for line in (output/'trials.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertTrue(all(len(row['source_sha256'])==64 and len(row['case_sha256'])==64 for row in rows))
            self.assertTrue(result['source_unchanged'])
            manifest=json.loads((output/'manifest.json').read_text())
            with zipfile.ZipFile(output/'source.zip') as snapshot:
                self.assertEqual(set(snapshot.namelist()),set(manifest['files']))
                for name,expected in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(snapshot.read(name)).hexdigest(),expected)
            self.assertIn('不证明模型质量',(output/'report.md').read_text())
            with self.assertRaises(ValueError):run_suite(['C01'],output)
            with self.assertRaises(ValueError):run_suite(['C01'],ROOT/'.workbench/do-not-write')
        finally:shutil.rmtree(temporary)

    def test_cli_dry_run_and_online_fail_closed(self):
        command=[sys.executable,str(ROOT/'scripts/run_evals.py')]
        dry=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(dry.returncode,0,dry.stderr)
        self.assertEqual(json.loads(dry.stdout)['mode'],'dry-run')
        online=subprocess.run([*command,'--mode','online'],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(online.returncode,0)
        self.assertIn('不会发起在线请求',online.stderr)
        with self.assertRaises(ValueError):load_case('../C01')

    def test_frozen_source_can_replay_parallel_case_without_original_repository(self):
        parent=ROOT/'artifacts/evals';parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='eval-replay-',dir=parent))
        try:
            output=temporary/'original';run_suite(['C02'],output)
            with tempfile.TemporaryDirectory(prefix='eval-restored-') as restored:
                root=Path(restored)
                with zipfile.ZipFile(output/'source.zip') as snapshot:
                    snapshot.extractall(root)
                    for entry in snapshot.infolist():
                        mode=entry.external_attr>>16
                        if mode:(root/entry.filename).chmod(mode&0o777)
                command=[sys.executable,str(root/'scripts/run_evals.py'),'--mode','offline','--cases','C02','--output',str(root/'artifacts/evals/replayed')]
                replay=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=20)
                self.assertEqual(replay.returncode,0,replay.stderr+replay.stdout)
                self.assertEqual(json.loads(replay.stdout)['passed'],1)
        finally:shutil.rmtree(temporary)
