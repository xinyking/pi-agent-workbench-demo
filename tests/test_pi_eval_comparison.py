"""Cross-library checks of genuine Pi native judges and DeepEval policy boundaries."""
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals.pi_comparison import OFFICIAL, compare_rows, deep_scores, diagnostic_controls, prepare_sample, verify_reference
from evals.runner import ROOT

NODE = shutil.which('node')
HAS_NATIVE = bool(NODE) and (OFFICIAL / 'node_modules/vitest-evals/package.json').exists()
HAS_DEEP = importlib.util.find_spec('deepeval') is not None


def sample():
    return prepare_sample({'sample_id': 'test', 'case_id': 'C01', 'cohort': 'control', 'perturbation': None,
        'input': 'Read and repair file.py', 'actual_output': 'done', 'artifacts': {},
        'checks': [{'id': 'independent-acceptance', 'passed': True}], 'retrieval_context': [],
        'evidence_errors': [],
        'tools': [{'name': name, 'input_parameters': {'path': 'file.py'}} for name in ['read', 'write']],
        'expected_tools': [{'name': name, 'input_parameters': {'path': 'file.py'}} for name in ['read', 'write']]})


class ComparisonPolicyTests(unittest.TestCase):
    def test_diagnostic_controls_require_explicit_c01_seed(self):
        with self.assertRaisesRegex(ValueError, 'require C01'):
            diagnostic_controls([{**sample(), 'case_id': 'R03'}])

    def test_default_cli_is_dry_run_without_optional_dependencies(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/compare_pi_evals.py')],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['model_calls'])

    def test_missing_and_duplicate_judge_results_cannot_be_counted_as_agreement(self):
        s = sample()
        row = {'sample_id': 'test', 'tool': {'score': 1, 'passed': True},
               'business': {'score': 1, 'passed': True}}
        for broken in [[], [row, row], [{**row, 'sample_id': 'other'}]]:
            with self.assertRaisesRegex(ValueError, 'planned cohort'):
                compare_rows([s], [row], broken)

    def test_guarded_pair_is_blocked_and_excluded_from_agreement(self):
        s = sample()
        s['evidence_errors'] = ['missing artifact']
        row = {'sample_id': 'test', 'tool': {'score': 1, 'passed': True},
               'business': {'score': 1, 'passed': True}}
        result = compare_rows([s], [row], [row])[0]
        self.assertEqual(result['pi_adapted_verdict'], 'blocked')
        self.assertEqual(result['deepeval_verdict'], 'blocked')
        self.assertIsNone(result['tool_pass_agreement'])

    def test_wrong_official_source_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'package.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'digest mismatch'):
                verify_reference(directory)


@unittest.skipUnless(HAS_NATIVE and HAS_DEEP, 'requires Node, npm ci in evals/pi_official, and DeepEval extra')
class NativeJudgeComparisonTests(unittest.TestCase):
    def compare(self, s):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.json'
            path.write_text(json.dumps([s]))
            result = subprocess.run([NODE, str(OFFICIAL / 'judge.mjs'), str(path)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            official = json.loads(result.stdout)
        self.assertFalse(official['network'])
        self.assertEqual(official['version'], '0.15.0')
        return compare_rows([s], deep_scores([s]), official['results'])[0]

    def test_both_pass_complete_matching_tools_and_business_checks(self):
        result = self.compare(sample())
        self.assertEqual((result['deepeval_verdict'], result['pi_adapted_verdict']), ('passed', 'passed'))

    def test_partial_tool_match_different_scores_same_failed_decision(self):
        s = sample()
        s['tools'][0]['input_parameters']['path'] = 'wrong.py'
        result = self.compare(s)
        self.assertEqual(result['deepeval']['tool']['score'], 0.5)
        self.assertEqual(result['pi_native_judges']['tool']['score'], 0)
        self.assertTrue(result['tool_pass_agreement'])
        self.assertEqual(result['deepeval_verdict'], 'failed')

    def test_empty_expected_tools_is_a_real_policy_difference(self):
        s = sample()
        s['expected_tools'] = []
        result = self.compare(s)
        self.assertFalse(result['tool_pass_agreement'])
        self.assertEqual(result['deepeval']['tool']['score'], 0)
        self.assertEqual(result['pi_native_judges']['tool']['score'], 1)

    def test_tool_success_cannot_override_independent_business_failure(self):
        s = sample()
        s['checks'][0]['passed'] = False
        prepare_sample(s)
        result = self.compare(s)
        self.assertTrue(result['deepeval']['tool']['passed'])
        self.assertTrue(result['pi_native_judges']['tool']['passed'])
        self.assertEqual((result['deepeval_verdict'], result['pi_adapted_verdict']), ('failed', 'failed'))
