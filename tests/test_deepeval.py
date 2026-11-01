"""Evidence integrity, real DeepEval metrics and bounded local judge contracts."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from evals.deepeval_adapter import normalize_trial, read_bundle
from evals.deepeval_runner import read_profile, reserve_output, score_bundle, score_sample
from evals.runner import ROOT, load_case, run_suite, run_trial

HAS_DEEPEVAL = importlib.util.find_spec('deepeval') is not None


class _EvidenceFixture:
    @classmethod
    def setUpClass(cls):
        cls.profile = read_profile()
        cls.case = load_case('C01')
        cls.row, cls.evidence = run_trial(cls.case)
        cls.row.update(case_id='C01', trial=1, evidence='C01-1.json')

    def sample(self, evidence=None, row=None):
        return normalize_trial(self.case, row or self.row, evidence or self.evidence, self.profile)


class EvidenceAdapterTests(_EvidenceFixture, unittest.TestCase):
    def test_root_answer_artifacts_and_projected_arguments_are_preserved(self):
        sample = self.sample()
        self.assertEqual(sample['evidence_errors'], [])
        self.assertIn('batches.py', sample['artifacts'])
        self.assertEqual(sample['tools'][1]['input_parameters'], {'path': 'batches.py'})
        self.assertIn('content', sample['tools'][1]['raw_arguments'])
        self.assertIsNone(sample['measurements']['cost'])

    def test_child_message_cannot_replace_root_delivery(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['events'].append({'kind': 'message.completed', 'data': {'agent_id': 'child', 'text': 'fake final'}})
        self.assertNotEqual(self.sample(evidence)['actual_output'], 'fake final')

    def test_same_tool_id_across_agents_and_attempts_is_not_cross_joined(self):
        evidence = copy.deepcopy(self.evidence)
        start = copy.deepcopy(next(e for e in evidence['events'] if e['kind'] == 'tool.started'))
        end = copy.deepcopy(next(e for e in evidence['events'] if e['kind'] == 'tool.completed'))
        pairs = []
        for agent, attempt in [('child', 'a'), ('child', 'b')]:
            a, b = copy.deepcopy(start), copy.deepcopy(end)
            a['data'].update(agent_id=agent, attempt_id=attempt)
            b['data'].update(agent_id=agent, attempt_id=attempt)
            b['data']['result'] = {'content': [{'type': 'text', 'text': attempt}]}
            pairs.append((a, b))
        evidence['events'] += [pairs[0][0], pairs[1][0], pairs[1][1], pairs[0][1]]
        sample = self.sample(evidence)
        self.assertEqual(sample['evidence_errors'], [])
        self.assertEqual([t['output']['content'][0]['text'] for t in sample['tools'][-2:]], ['a', 'b'])

    def test_missing_result_or_final_answer_is_not_success(self):
        for kind in ('tool.completed', 'message.completed'):
            evidence = copy.deepcopy(self.evidence)
            evidence['events'] = [e for e in evidence['events'] if e['kind'] != kind]
            self.assertTrue(self.sample(evidence)['evidence_errors'])

    def test_removing_failed_acceptance_check_cannot_create_a_pass(self):
        row = copy.deepcopy(self.row)
        row['checks'] = [c for c in row['checks'] if c['id'] != 'holdout-1']
        self.assertIn('holdout-1', '; '.join(self.sample(row=row)['evidence_errors']))

    def test_absent_document_read_is_not_replaced_with_reference_text(self):
        case = load_case('P01')
        sample = normalize_trial(case, self.row, self.evidence, self.profile)
        self.assertEqual(sample['retrieval_context'], [])

    def test_missing_artifact_snapshots_block_regrading(self):
        evidence = copy.deepcopy(self.evidence)
        evidence['files'] = {}
        self.assertIn('missing required artifact', '; '.join(self.sample(evidence)['evidence_errors']))

    @unittest.skipUnless(importlib.util.find_spec('pypdf'), 'pypdf required')
    def test_each_required_source_page_needs_actual_read_text(self):
        case = load_case('P01')
        row, evidence = run_trial(case)
        row.update(trial=1)
        complete = normalize_trial(case, row, evidence, self.profile)
        self.assertEqual(complete['evidence_errors'], [])
        event = next(e for e in evidence['events'] if e['kind'] == 'tool.completed'
                     and e['data'].get('result', {}).get('details', {}).get('page') == 2)
        event['data']['result']['details'].pop('text')
        incomplete = normalize_trial(case, row, evidence, self.profile)
        self.assertIn('missing retrieved source pages: 2', '; '.join(incomplete['evidence_errors']))


@unittest.skipUnless(HAS_DEEPEVAL, 'optional DeepEval extra not installed; use .venv-evals')
class DeepEvalMetricTests(_EvidenceFixture, unittest.TestCase):
    def test_deterministic_metrics_do_not_connect_to_network(self):
        from evals.deepeval_metrics import make_test_case, metric_plan
        sample = self.sample()
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Unexpected network')) as connect:
            for key, metric in metric_plan(sample, self.profile):
                self.assertEqual(metric.measure(make_test_case(sample), _show_indicator=False), 1)
            connect.assert_not_called()

    def test_wrong_parameter_fails_real_tool_correctness_metric(self):
        from evals.deepeval_metrics import make_test_case, metric_plan
        sample = self.sample()
        sample['tools'][0]['input_parameters']['path'] = 'wrong.py'
        metric = dict(metric_plan(sample, self.profile))['tool_contract']
        self.assertLess(metric.measure(make_test_case(sample), _show_indicator=False), 1)

    def test_perfect_synthetic_judge_cannot_override_wrong_delivery(self):
        from evals.deepeval_judge import LocalHTTPJudge, contract_judge_server
        row, evidence = run_trial(self.case, 'broken')
        row.update(trial=1)
        sample = self.sample(evidence, row)
        with contract_judge_server() as url:
            judge = LocalHTTPJudge(url, 'contract')
            scored = score_sample(sample, self.profile, judge, 'contract')
        self.assertEqual(scored['verdict'], 'failed')
        self.assertIsNone(scored['quality_verdict'])
        quality = next(m for m in scored['metrics'] if m['id'] == 'delivery_quality')
        self.assertEqual((quality['score'], quality['status']), (1, 'simulated'))

    def test_unconfigured_judge_is_blocked_not_passed(self):
        row = score_sample(self.sample(), self.profile, None, 'none')
        self.assertEqual(row['verdict'], 'blocked')
        self.assertIsNone(next(m for m in row['metrics'] if m['id'] == 'delivery_quality')['score'])

    def test_missing_evidence_and_known_execution_failure_remain_distinct(self):
        sample = self.sample()
        sample['evidence_errors'].append('missing tool result')
        self.assertEqual(score_sample(sample, self.profile, None, 'none')['verdict'], 'blocked')
        sample['execution_failed'] = True
        self.assertEqual(score_sample(sample, self.profile, None, 'none')['verdict'], 'failed')

    def test_judge_budget_timeout_remote_address_and_schema_fail_closed(self):
        from evals.deepeval_judge import LocalHTTPJudge, contract_judge_server
        from pydantic import BaseModel

        class Score(BaseModel):
            score: float
            reason: str

        for url in ['https://example.com/v1', 'http://localhost/v1', 'http://127.0.0.1/v1?key=secret']:
            with self.assertRaises(ValueError):
                LocalHTTPJudge(url, 'contract')
        with contract_judge_server() as url:
            judge = LocalHTTPJudge(url, 'contract', max_requests=1)
            self.assertEqual(judge.generate('first', schema=Score).score, 10)
            with self.assertRaisesRegex(ValueError, 'budget exhausted'):
                judge.generate('second', schema=Score)
            with self.assertRaisesRegex(ValueError, 'structured response'):
                judge.generate('missing schema')
            judge = LocalHTTPJudge(url, 'contract', max_input_chars=1)
            with self.assertRaisesRegex(ValueError, 'character budget'):
                judge.generate('too long', schema=Score)
            judge = LocalHTTPJudge(url, 'contract')
            with patch.object(judge.opener, 'open', side_effect=TimeoutError('secret body')):
                with self.assertRaisesRegex(ValueError, r'failure \(TimeoutError\)') as caught:
                    judge.generate('timeout', schema=Score)
                self.assertNotIn('secret body', str(caught.exception))

    def test_judge_out_of_range_score_is_blocked(self):
        from evals.deepeval_metrics import NoCallsModel
        class InvalidJudge(NoCallsModel):
            def generate(self, prompt, schema=None, **kwargs):
                return schema(score=100, reason='bad score')
        row = score_sample(self.sample(), self.profile, InvalidJudge(), 'local-http')
        self.assertEqual(row['verdict'], 'blocked')
        self.assertIsNone(next(m for m in row['metrics'] if m['id'] == 'delivery_quality')['score'])


class DeepEvalBundleTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'artifacts/evals'
        parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='deepeval-test-', dir=parent))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_output_refuses_overwrite_and_user_data_path(self):
        reserve_output(self.root / 'new')
        with self.assertRaises(FileExistsError):
            reserve_output(self.root / 'new')
        with self.assertRaises(ValueError):
            reserve_output(ROOT / '.workbench/deepeval-forbidden')

    def test_bundle_rejects_missing_trial_and_escaping_evidence_path(self):
        run_suite(['C01'], self.root / 'runtime', repeat=2)
        trials = self.root / 'runtime/trials.jsonl'
        original = trials.read_text()
        trials.write_text(original.splitlines()[0] + '\n')
        with self.assertRaisesRegex(ValueError, 'planned cohort'):
            read_bundle(self.root / 'runtime')
        rows = [json.loads(line) for line in original.splitlines()]
        rows[0]['evidence'] = '../outside.json'
        trials.write_text('\n'.join(json.dumps(r) for r in rows))
        with self.assertRaisesRegex(ValueError, 'escapes'):
            read_bundle(self.root / 'runtime')

    def test_bundle_rejects_tampered_artifacts_and_source_identity(self):
        run_suite(['C01'], self.root / 'runtime')
        evidence = self.root / 'runtime/C01-1.json'
        original = evidence.read_text()
        evidence.write_text(original.replace('batches.py', 'wrong.py'))
        with self.assertRaisesRegex(ValueError, 'digest'):
            read_bundle(self.root / 'runtime')
        evidence.write_text(original)
        trials = self.root / 'runtime/trials.jsonl'
        row = json.loads(trials.read_text())
        row['source_sha256'] = '0' * 64
        trials.write_text(json.dumps(row) + '\n')
        with self.assertRaisesRegex(ValueError, 'source identity'):
            read_bundle(self.root / 'runtime')

    @unittest.skipUnless(HAS_DEEPEVAL, 'optional DeepEval extra not installed')
    def test_replay_freezes_inputs_and_reports_unscored_coverage(self):
        run_suite(['C01'], self.root / 'runtime')
        output = reserve_output(self.root / 'scored')
        summary = score_bundle(self.root / 'runtime', output)
        self.assertEqual((summary['trials'], summary['blocked']), (1, 1))
        self.assertEqual(summary['coverage']['delivery_quality']['blocked'], 1)
        self.assertTrue((output / 'protocol.json').exists())
        self.assertTrue((output / 'samples.json').exists())
        self.assertIsNone(summary['task_success_uplift'])

    def test_cli_dry_run_needs_no_deepeval_and_rejects_external_judge(self):
        command = [sys.executable, str(ROOT / 'scripts/run_deepeval.py')]
        dry = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertFalse(json.loads(dry.stdout)['online_calls'])
        invalid = subprocess.run([*command, '--mode', 'offline', '--judge', 'local-http',
                                  '--output', str(self.root / 'invalid')],
                                 capture_output=True, text=True, timeout=15)
        self.assertEqual(invalid.returncode, 2)
        self.assertFalse((self.root / 'invalid').exists())
