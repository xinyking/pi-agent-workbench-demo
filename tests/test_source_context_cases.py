"""Verify source support and constraint regression checks, including deceptive output."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from evals.runner import ROOT,load_case,run_trial,run_suite


class SourceContextCases(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('pypdf'),'optional pypdf')
    def test_pdf_facts_have_matching_source_pages_and_no_injection_action(self):
        for name in ['P01','P02']:
            with self.subTest(case=name):
                result,evidence=run_trial(load_case(name))
                self.assertEqual(result['verdict'],'passed',result)
                self.assertEqual(result['source_metrics']['citation_support_rate'],1)
                self.assertEqual(len(evidence['source']['pdf_sha256']),64)
                self.assertEqual(len(evidence['answer']['claims']),5)
                if name=='P02':self.assertEqual(result['source_metrics']['unsafe_read_calls'],0)

    @unittest.skipUnless(importlib.util.find_spec('pypdf'),'optional pypdf')
    def test_wrong_page_and_followed_document_instruction_fail_despite_completion(self):
        for name,failed_check in [('P01','source-sample'),('P02','no-sensitive-read-event')]:
            with self.subTest(case=name):
                result,_=run_trial(load_case(name),'broken')
                self.assertEqual(result['run_status'],'completed')
                self.assertEqual(result['verdict'],'failed')
                self.assertIn(failed_check,[c['id'] for c in result['checks'] if not c['passed']])
                if name=='P01':self.assertEqual(result['source_metrics']['fact_accuracy'],1)
                if name=='P02':self.assertEqual(result['source_metrics']['unsafe_read_calls'],1)

    def test_long_history_retrieval_preserves_constraints_and_new_user_override(self):
        result,evidence=run_trial(load_case('P03'))
        self.assertEqual(result['verdict'],'passed',result)
        self.assertEqual(evidence['answer']['requirements']['city'],'Shanghai')
        self.assertIs(evidence['answer']['requirements']['allow_network'],False)
        self.assertGreater(result['source_metrics']['context_read_calls'],1)
        checks={c['id']:c['passed'] for c in result['checks']}
        self.assertTrue(checks['history-actually-excerpted'])
        self.assertTrue(checks['full-snapshot-retrieved'])
        self.assertTrue(checks['cross-task-context-denied'])

    def test_excerpt_only_answer_loses_old_constraints_and_is_rejected(self):
        result,_=run_trial(load_case('P03'),'broken')
        self.assertEqual(result['run_status'],'completed')
        self.assertEqual(result['verdict'],'failed')
        self.assertLess(result['source_metrics']['constraint_retention_rate'],1)
        self.assertEqual(result['source_metrics']['context_read_calls'],0)

    def test_missing_pdf_dependency_is_blocked_not_success_or_model_failure(self):
        parent=ROOT/'artifacts/evals';parent.mkdir(parents=True,exist_ok=True)
        temporary=Path(tempfile.mkdtemp(prefix='source-blocked-',dir=parent))
        try:
            with patch('evals.source_context.importlib.util.find_spec',return_value=None):
                summary=run_suite(['P01'],temporary/'result')
            self.assertEqual((summary['blocked'],summary['passed'],summary['failed']),(1,0,0))
            row=json.loads((temporary/'result/trials.jsonl').read_text())
            self.assertEqual(row['verdict'],'blocked')
            self.assertIsNone(row['score']);self.assertIsNone(row['run_status'])
        finally:shutil.rmtree(temporary)
