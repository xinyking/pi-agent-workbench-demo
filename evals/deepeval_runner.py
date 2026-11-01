"""Offline/replay scoring with frozen inputs and separate engineering/quality verdicts."""
import hashlib
import importlib.metadata
import json
import math
import platform
import sys
from pathlib import Path

from .deepeval_adapter import normalize_trial, read_bundle
from .runner import ROOT, load_case

PROFILE = ROOT / 'evals/deepeval_profile.json'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_profile():
    return json.loads(PROFILE.read_text())


def reserve_output(output):
    path = Path(output).resolve()
    if not path.is_relative_to((ROOT / 'artifacts/evals').resolve()):
        raise ValueError('DeepEval output must be inside artifacts/evals/')
    path.mkdir(parents=True, exist_ok=False)
    return path


def score_sample(sample, profile, judge, judge_kind):
    from .deepeval_metrics import make_test_case, metric_plan
    base = {'case_id': sample['case_id'], 'trial': sample['trial'], 'metrics': [],
            'measurements': sample['measurements'], 'evidence': sample['source_evidence']}
    if sample['evidence_errors']:
        known_failure = sample.get('execution_failed') or any(c.get('passed') is False for c in sample['checks'])
        base['metrics'].append({'id': 'evidence_integrity', 'kind': 'deterministic', 'status': 'blocked',
                                'score': None, 'passed': None, 'reason': '; '.join(sample['evidence_errors'])})
        groups = {'P01': 'citation_checks', 'P02': 'safety_checks', 'P03': 'constraint_checks',
                  'R03': 'recovery_checks'}
        missing = ['business_checks', 'tool_contract', 'delivery_quality', 'faithfulness']
        if sample['case_id'] in groups:
            missing.append(groups[sample['case_id']])
        for key in missing:
            applicable = key != 'faithfulness' or sample['case_id'] in ('P01', 'P02')
            base['metrics'].append({'id': key, 'kind': 'semantic' if key in ('delivery_quality', 'faithfulness')
                                   else 'deterministic', 'status': 'blocked' if applicable else 'not_applicable',
                                   'score': None, 'passed': None, 'reason': 'Incomplete input evidence'})
        base.update(verdict='failed' if known_failure else 'blocked', quality_verdict=None,
                    error='; '.join(sample['evidence_errors']))
        return base
    test_case = make_test_case(sample)
    for key, metric in metric_plan(sample, profile, judge):
        semantic = key in ('delivery_quality', 'faithfulness')
        result = {'id': key, 'evaluator': metric.__class__.__name__, 'threshold': metric.threshold,
                  'kind': 'semantic' if semantic else 'deterministic', 'score': None, 'passed': None}
        if key == 'faithfulness' and not sample['retrieval_context']:
            result.update(status='blocked', reason='Missing retrieved source text')
        else:
            try:
                score = metric.measure(test_case, _show_indicator=False)
                if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError('Metric returned an invalid score')
                result.update(score=score, passed=metric.is_successful(), reason=metric.reason,
                              status='simulated' if semantic and judge_kind == 'contract' else 'measured')
            except Exception as exc:
                # Do not persist exception bodies which can contain model output or input data.
                result.update(status='blocked', reason=f'Evaluator failed ({type(exc).__name__})')
        base['metrics'].append(result)
    required_semantic = ['delivery_quality'] + (['faithfulness'] if sample['case_id'] in ('P01', 'P02') else [])
    for key in required_semantic:
        if not any(m['id'] == key for m in base['metrics']):
            base['metrics'].append({'id': key, 'kind': 'semantic', 'status': 'blocked',
                'score': None, 'passed': None, 'reason': 'Judge not configured or retrieved evidence missing'})
    if sample['case_id'] not in ('P01', 'P02'):
        base['metrics'].append({'id': 'faithfulness', 'kind': 'semantic', 'status': 'not_applicable',
                               'score': None, 'passed': None, 'reason': 'Not a document-grounded case'})
    measured = [m for m in base['metrics'] if m['status'] == 'measured']
    # A known hard failure stays failed even when another metric cannot be scored.
    failed = any(m['passed'] is False for m in measured)
    blocked = any(m['status'] == 'blocked' for m in base['metrics'])
    base['verdict'] = 'failed' if failed else 'blocked' if blocked else 'passed'
    base['quality_verdict'] = (base['verdict'] if judge_kind == 'local-http' and not blocked else None)
    return base


def score_bundle(input_dir, output, judge_kind='none', judge=None):
    output = Path(output)
    profile = read_profile()
    version = importlib.metadata.version('deepeval')
    if version != profile['deepeval_version']:
        raise ValueError(f'DeepEval version mismatch: expected {profile["deepeval_version"]}, got {version}')
    manifest, trials = read_bundle(input_dir)
    samples = []
    for row, evidence in trials:
        case = load_case(row['case_id'])
        expected_hash = row.get('case_sha256')
        current_hash = sha256((ROOT / 'evals/cases' / f'{case["id"]}.json').read_bytes())
        if expected_hash != current_hash:
            raise ValueError('Case identity changed; replay must use the frozen matching source')
        sample = normalize_trial(case, row, evidence, profile)
        sample['case_sha256'] = expected_hash
        samples.append(sample)
    frozen = json.dumps(samples, ensure_ascii=False, sort_keys=True).encode()
    (output / 'samples.json').write_bytes(frozen)
    (output / 'profile.json').write_bytes(PROFILE.read_bytes())
    sources = {str(p.relative_to(ROOT)): sha256(p.read_bytes()) for p in [
        *sorted((ROOT / 'evals').glob('deepeval_*.py')), ROOT / 'scripts/run_deepeval.py',
        ROOT / 'evals/requirements.lock', PROFILE]}
    protocol = {'schema_version': 1, 'profile_sha256': sha256(PROFILE.read_bytes()),
        'samples_sha256': sha256(frozen), 'sources': sources, 'deepeval_version': version,
        'python': sys.version.split()[0], 'platform': platform.platform(),
        'execution_level': manifest.get('run_config', {}).get('mode', 'unknown'),
        'runtime_source_sha256': manifest.get('sha256'), 'judge_kind': judge_kind,
        'judge_model': judge.get_model_name() if judge else None,
        'judge_request_budget': judge.max_requests if judge else None,
        'planned_trials': [{'case_id': s['case_id'], 'trial': s['trial'],
                            'case_sha256': s['case_sha256']} for s in samples],
        'official_comparison': 'deferred', 'telemetry': False, 'cloud_upload': False}
    protocol['sha256'] = sha256(json.dumps(protocol, sort_keys=True).encode())
    (output / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    rows = []
    with (output / 'scores.jsonl').open('w') as stream:
        for sample in samples:
            row = score_sample(sample, profile, judge, judge_kind)
            row['sample_id'] = f"{sample['case_id']}-{sample['trial']}"
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            rows.append(row)
    coverage = {}
    for row in rows:
        for metric in row['metrics']:
            counts = coverage.setdefault(metric['id'], {'measured': 0, 'simulated': 0, 'blocked': 0,
                                                       'not_applicable': 0, 'passed': 0, 'failed': 0})
            counts[metric['status']] += 1
            if metric['status'] == 'measured':
                counts['passed' if metric['passed'] else 'failed'] += 1
    summary = {'schema_version': 1, 'protocol_sha256': protocol['sha256'], 'trials': len(rows),
        **{key: sum(r['verdict'] == key for r in rows) for key in ('passed', 'failed', 'blocked')},
        'judge_kind': judge_kind, 'judge_requests': judge.requests if judge else 0,
        'incomplete_trials': sum(any(m['status'] == 'blocked' for m in r['metrics']) for r in rows),
        'coverage': coverage, 'model_quality_conclusion': None, 'task_success_uplift': None,
        'official_comparison': None, 'agent_tokens': None, 'agent_cost': None}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    lines = ['# DeepEval 工作台评测', '',
        f'DeepEval {version}；裁判模式：{judge_kind}。', '',
        '**contract 是合成 HTTP 裁判，仅验证协议；模拟分数不代表语义质量。**',
        '脚本化 Runtime 执行不代表真实 Agent 能力；本批没有模型收益或 Pi 官方对比结论。', '',
        '| 样本 | 工程/评分流程 | 指标 | 分数 | 阈值 | 状态 |', '| --- | --- | --- | --- | --- | --- |']
    for row in rows:
        if not row['metrics']:
            lines.append(f"| {row['sample_id']} | {row['verdict']} | evidence | — | — | blocked |")
        for metric in row['metrics']:
            lines.append(f"| {row['sample_id']} | {row['verdict']} | {metric['id']} | {metric['score']} | {metric.get('threshold', '—')} | {metric['status']} |")
    lines.extend(['', '详细原因见 scores.jsonl；输入与事件引用见 samples.json；覆盖率见 summary.json。',
                  '失败保留在样本总数中，blocked 不算成功；不适用和模拟评分单列。Token/费用未知保持 null。'])
    (output / 'report.md').write_text('\n'.join(lines) + '\n')
    return summary
