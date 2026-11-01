"""Paired deterministic scoring comparison, not an Agent capability benchmark."""
import copy
import hashlib
import importlib.metadata
import json
import subprocess
import zipfile
from urllib.request import urlopen
from pathlib import Path

from .deepeval_adapter import normalize_trial, read_bundle
from .deepeval_runner import read_profile, reserve_output
from .runner import ROOT, load_case, source_manifest

OFFICIAL = ROOT / 'evals/pi_official'


def fetch_reference(output):
    """Explicit public-source fetch; pinned hashes, no provider/auth configuration."""
    reference = json.loads((OFFICIAL / 'reference.json').read_text())
    output = reserve_output(output)
    for name, expected in reference['files'].items():
        url = f'https://raw.githubusercontent.com/earendil-works/pi/{reference["commit"]}/{name}'
        with urlopen(url, timeout=30) as response:
            raw = response.read(2097153)
        if len(raw) > 2097152 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'Pinned official source digest mismatch: {name}')
        path = output / name.removeprefix('packages/evals/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    verify_reference(output)
    return {'reference_dir': str(output), 'commit': reference['commit'], 'files_verified': len(reference['files'])}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_reference(directory):
    reference = json.loads((OFFICIAL / 'reference.json').read_text())
    for name, expected in reference['files'].items():
        path = Path(directory) / name.removeprefix('packages/evals/')
        if digest(path) != expected:
            raise ValueError(f'Official reference digest mismatch: {name}')
    package = json.loads((Path(directory) / 'package.json').read_text())
    if package['devDependencies']['vitest-evals'] != reference['vitest_evals_version']:
        raise ValueError('Official source/dependency identity mismatch')
    return reference


def load_samples(directory, cohort):
    manifest, rows = read_bundle(directory)
    profile = read_profile()
    samples = []
    for row, evidence in rows:
        case = load_case(row['case_id'])
        if row['case_sha256'] != digest(ROOT / 'evals/cases' / (case['id'] + '.json')):
            raise ValueError('Case changed since frozen execution')
        sample = normalize_trial(case, row, evidence, profile)
        sample.update(sample_id=f"{cohort}:{case['id']}:{row['trial']}", cohort=cohort,
                      perturbation=None, source_sha256=row['source_sha256'],
                      evidence_sha256=row['evidence_sha256'])
        samples.append(sample)
    return manifest, samples


def diagnostic_controls(samples):
    """Synthetic metric inputs; never presented as additional Agent executions."""
    base = next((s for s in samples if s['case_id'] == 'C01'), None)
    if base is None:
        raise ValueError('Diagnostic controls require C01 in the positive cohort; use --no-controls')
    result = []
    for mutation in ['wrong_argument', 'missing_tool', 'reordered_tools', 'extra_tool',
                     'extra_parameter', 'empty_expectation', 'missing_artifact']:
        sample = copy.deepcopy(base)
        sample.update(sample_id='control:' + mutation, cohort='control', perturbation=mutation)
        if mutation == 'wrong_argument':
            sample['tools'][0]['input_parameters']['path'] = 'wrong.py'
        elif mutation == 'missing_tool':
            sample['tools'] = sample['tools'][1:]
        elif mutation == 'reordered_tools':
            sample['tools'].reverse()
        elif mutation == 'extra_tool':
            sample['tools'].append({'name': 'extra_read_only_tool', 'input_parameters': {}})
        elif mutation == 'extra_parameter':
            sample['tools'][0]['input_parameters']['unexpected_field'] = 'extra'
        elif mutation == 'empty_expectation':
            sample['expected_tools'] = []
        elif mutation == 'missing_artifact':
            sample['artifacts'] = {}
            sample['evidence_errors'].append('missing required artifact snapshots: batches.py')
        result.append(sample)
    return result


def prepare_sample(sample):
    actual = {c['id']: c['passed'] for c in sample['checks']}
    sample['check_output'] = {'checks': actual, 'evidence_complete': not sample['evidence_errors']}
    sample['check_expected'] = {'checks': {key: True for key in actual}, 'evidence_complete': True}
    return sample


def deep_scores(samples):
    from .deepeval_metrics import NoCallsModel, WorkbenchChecksMetric, make_test_case
    from deepeval.metrics import ToolCorrectnessMetric
    from deepeval.test_case import ToolCallParams
    result = []
    for sample in samples:
        case = make_test_case(sample)
        metrics = [
            ToolCorrectnessMetric(model=NoCallsModel(), threshold=1, async_mode=False, eval_mode='llm',
                                  evaluation_params=[ToolCallParams.INPUT_PARAMETERS]),
            WorkbenchChecksMetric('Business checks', sample['checks']),
        ]
        row = {'sample_id': sample['sample_id']}
        for key, metric in zip(('tool', 'business'), metrics):
            score = metric.measure(case, _show_indicator=False)
            row[key] = {'score': score, 'passed': score == 1, 'reason': metric.reason}
        result.append(row)
    return result


def compare_rows(samples, deep, official):
    ids = [s['sample_id'] for s in samples]
    for rows in (deep, official):
        if len(rows) != len(ids) or {r['sample_id'] for r in rows} != set(ids):
            raise ValueError('Judge results do not match planned cohort')
    dmap, pmap = ({r['sample_id']: r for r in rows} for rows in (deep, official))
    result = []
    for sample in samples:
        key = sample['sample_id']
        d, p = dmap[key], pmap[key]
        # This guard is SHARED WORKBENCH POLICY, not a native judge feature.
        blocked = bool(sample['evidence_errors'])
        row = {'sample_id': key, 'cohort': sample['cohort'], 'case_id': sample['case_id'],
               'perturbation': sample['perturbation'], 'evidence_errors': sample['evidence_errors'],
               'deepeval': d, 'pi_native_judges': p, 'shared_guard_blocked': blocked,
               'tool_pass_agreement': None if blocked else d['tool']['passed'] == p['tool']['passed'],
               'business_pass_agreement': None if blocked else d['business']['passed'] == p['business']['passed'],
               'tool_score_equal': None if blocked else abs(d['tool']['score'] - p['tool']['score']) < 1e-12}
        for name, source in [('deepeval_verdict', d), ('pi_adapted_verdict', p)]:
            row[name] = ('blocked' if blocked else 'passed' if source['tool']['passed']
                         and source['business']['passed'] else 'failed')
        result.append(row)
    return result


def run_comparison(positive, negative, reference_dir, output, node, controls=True):
    version = importlib.metadata.version('deepeval')
    if version != read_profile()['deepeval_version']:
        raise ValueError('DeepEval version does not match pinned profile')
    reference = verify_reference(reference_dir)
    pm, pos = load_samples(positive, 'positive')
    nm, neg = load_samples(negative, 'negative')
    if pm['run_config']['fixture_variant'] != 'good' or nm['run_config']['fixture_variant'] != 'broken':
        raise ValueError('Comparison requires explicitly good positive and broken negative cohorts')
    if pm['sha256'] != nm['sha256']:
        raise ValueError('Positive/negative cohorts must use the same Runtime source')
    samples = [prepare_sample(s) for s in pos + neg + (diagnostic_controls(pos) if controls else [])]
    native_entry_hash = digest(OFFICIAL / 'node_modules/vitest-evals/dist/judges/index.mjs')
    output = reserve_output(output)
    sample_path = output / 'samples.json'
    sample_path.write_text(json.dumps(samples, ensure_ascii=False, sort_keys=True))
    snapshot = source_manifest()
    sources = snapshot['files']
    with zipfile.ZipFile(output / 'source.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sources:
            archive.write(ROOT / name, name)
    protocol = {'schema_version': 1, 'scope': 'same-evidence-deterministic-judges-only',
        'official': reference, 'deepeval_version': version, 'sources': sources,
        'comparison_source_sha256': snapshot['sha256'], 'native_judge_entry_sha256': native_entry_hash,
        'python_version': snapshot['python'], 'platform': snapshot['platform'],
        'samples_sha256': digest(sample_path), 'positive_source_sha256': pm['sha256'],
        'negative_source_sha256': nm['sha256'], 'planned_samples': [s['sample_id'] for s in samples],
        'tool_policy': {'ordered': False, 'allow_extras': True, 'required_parameters': 'profile projection',
                        'pass_threshold': 1.0},
        'shared_workbench_guard': 'incomplete evidence => blocked; not a native judge feature',
        'model_calls': False, 'official_docs_lift_executed': False}
    (output / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    deep = deep_scores(samples)
    (output / 'deepeval.json').write_text(json.dumps(deep, ensure_ascii=False, indent=2))
    run = subprocess.run([str(node), str(OFFICIAL / 'judge.mjs'), str(sample_path)],
                         capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise ValueError('Official native judge runner failed; check Node and npm ci dependencies')
    raw = json.loads(run.stdout)
    if (raw.get('version') != reference['vitest_evals_version'] or raw.get('network') is not False
            or raw.get('judge_entry_sha256') != native_entry_hash):
        raise ValueError('Native judge version/network policy mismatch')
    (output / 'pi-native.json').write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    protocol['node_version'] = raw['node_version']
    protocol['sha256'] = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    (output / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    rows = compare_rows(samples, deep, raw['results'])
    (output / 'comparisons.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    cohorts = {}
    for cohort in ['positive', 'negative', 'control']:
        group = [r for r in rows if r['cohort'] == cohort]
        scored = [r for r in group if not r['shared_guard_blocked']]
        cohorts[cohort] = {'planned': len(group), 'scored_pairs': len(scored),
            'blocked': len(group) - len(scored),
            'tool_pass_agreement': sum(r['tool_pass_agreement'] for r in scored),
            'business_pass_agreement': sum(r['business_pass_agreement'] for r in scored),
            'tool_score_equal': sum(r['tool_score_equal'] for r in scored),
            'tool_only_passed_deepeval': sum(r['deepeval']['tool']['passed'] for r in scored),
            'tool_only_passed_pi': sum(r['pi_native_judges']['tool']['passed'] for r in scored),
            'deepeval_passed': sum(r['deepeval_verdict'] == 'passed' for r in scored),
            'pi_adapted_passed': sum(r['pi_adapted_verdict'] == 'passed' for r in scored)}
    summary = {'schema_version': 1, 'protocol_sha256': protocol['sha256'],
               'cohorts': cohorts, 'model_quality_conclusion': None,
               'task_success_uplift': None, 'semantic_accuracy_comparison': None,
               'official_docs_lift_executed': False,
               'source_unchanged': source_manifest()['sha256'] == snapshot['sha256']}
    if not summary['source_unchanged']:
        raise ValueError('Comparison source changed during execution; withholding conclusions')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    lines = ['# DeepEval 与 Pi 官方原生评分器本地对照', '',
        f'Pi {reference["tag"]} / {reference["commit"]}；vitest-evals {raw["version"]}；DeepEval {version}。', '',
        '同一份脚本化执行证据，真实库评分；未运行真实模型、官方文档增益实验或系统收益实验。',
        'Pi 侧为官方采用的两个原生 judge + 工作台验收映射；不冒称官方原题结果。',
        'StructuredOutputJudge 和 BaseMetric 消费相同外置检查结果，不是两个独立业务 oracle。', '',
        '| 分组 | 计划 | 完整配对 | 工具通过判定一致 | 业务判定一致 | DeepEval / Pi 组合通过 |',
        '| --- | --- | --- | --- | --- | --- |']
    for name, c in cohorts.items():
        lines.append(f"| {name} | {c['planned']} | {c['scored_pairs']} | {c['tool_pass_agreement']} | {c['business_pass_agreement']} | {c['deepeval_passed']} / {c['pi_adapted_passed']} |")
    lines.extend(['', '## 原始工具分数或判定差异', '',
                  '| 样本 | DeepEval 工具分 | Pi 工具分 | 通过判定一致 |', '| --- | --- | --- | --- |'])
    for r in rows:
        if r['tool_score_equal'] is False:
            lines.append(f"| {r['sample_id']} | {r['deepeval']['tool']['score']} | {r['pi_native_judges']['tool']['score']} | {r['tool_pass_agreement']} |")
    lines.extend(['', '## 解读与选择', '',
        f"broken 配置队列中，仅工具检查有 {cohorts['negative']['tool_only_passed_deepeval']}/{cohorts['negative']['scored_pairs']} 个通过（DeepEval），Pi 为 {cohorts['negative']['tool_only_passed_pi']}/{cohorts['negative']['scored_pairs']}；组合业务验收后分别有 {cohorts['negative']['deepeval_passed']}、{cohorts['negative']['pi_adapted_passed']} 个通过。",
        '两套工具检查都不能单独判断代码、事实、权限或恢复正确；必须配合任务特定业务验收。',
        '在当前阈值 1.0 下比较通过/失败，比跨框架比较原始平均分更合理。',
        '控制样本是冻结证据的受控扰动，不是新增 Agent 执行；缺产物的 blocked 来自共享完整性策略。',
        '继续采用 DeepEval，利用 Python 业务检查与后续 GEval/Faithfulness；借鉴 Pi 官方的冻结、隔离和配对方法。',
        '本报告不支持任何真实模型质量优劣、语义裁判准确率或工作台收益结论。'])
    (output / 'report.md').write_text('\n'.join(lines) + '\n')
    return summary
