"""Isolated, deterministic evaluation pipeline; no online model adapter."""
import hashlib
import json
import os
import platform
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch
from workbench.runtime import Runtime
from workbench.store import Store
from .source_context import prepare as prepare_source,grade as grade_source,EvaluationBlocked

ROOT=Path(__file__).resolve().parents[1]
CASE_DIR=ROOT/'evals/cases'
PEER=ROOT/'evals/fixtures/pi_peer.py'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest():
    paths=[ROOT/'pyproject.toml',ROOT/'scripts/run_evals.py',*sorted((ROOT/'workbench').rglob('*.py')),*sorted((ROOT/'workbench/pi').rglob('*.ts')),*sorted((ROOT/'evals').rglob('*.py')),*sorted(CASE_DIR.glob('*.json')),*sorted((ROOT/'.pi/agents').glob('*.md')),*sorted((ROOT/'workbench/pi/vendor').glob('LICENSE*'))]
    files={str(p.relative_to(ROOT)):digest(p) for p in paths}
    return {'sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),'files':files,'schema_version':1,'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,'platform':platform.platform(),'model':'deterministic-rpc-peer','pi_version':None,'network_model_calls':False}


def load_case(name):
    if name not in {'C01','C02','R03','P01','P02','P03'}:raise ValueError('未实现或未知用例：'+name)
    case=json.loads((CASE_DIR/(name+'.json')).read_text())
    for path in [*case['files'],*case['allowed_changes']]:
        if Path(path).is_absolute() or '..' in Path(path).parts:raise ValueError('用例路径越界')
    return case


def collect_events(store,run_id):
    events=[];cursor=0
    while page:=store.events(run_id,cursor):
        events.extend(page);cursor=page[-1]['id']
    return events


def run_trial(case,variant='good',timeout=20):
    started=time.monotonic();checks=[];evidence={};runtime=None;store=None;run_id=None
    result={'schema_version':1,'case_id':case['id'],'split':case['split'],'level':'offline-pipeline','fixture_variant':variant,'run_status':None,'verdict':'failed','tokens':None,'cost':None,'model_requests':None,'error':None}
    with tempfile.TemporaryDirectory(prefix='workbench-eval-') as temporary:
        root=Path(temporary);workspace=root/'project';workspace.mkdir()
        for name,content in case['files'].items():(workspace/name).write_text(content)
        initial={str(p.relative_to(workspace)):digest(p) for p in workspace.rglob('*') if p.is_file()}
        store=Store(root/'state/state.sqlite3');runtime=Runtime(store);runtime.config.binary=str(PEER);runtime.config.version='eval-fixture';runtime.config.add_workspace(str(workspace))
        spawned=[];factory=runtime.client_factory
        def client(*args,**kwargs):
            spawned.append(time.monotonic());return factory(*args,**kwargs)
        runtime.client_factory=client
        try:
            source_info=prepare_source(runtime,case,workspace) if case['id'].startswith('P') else None
            initial={str(p.relative_to(workspace)):digest(p) for p in workspace.rglob('*') if p.is_file()}
            # This environment change is scoped to a serial offline trial, never the live service.
            with patch.dict(os.environ,{'WORKBENCH_EVAL_VARIANT':variant,'PI_CODING_AGENT_DIR':str(root/'isolated-pi')}):
                with patch('workbench.runtime.create_archive',side_effect=PermissionError('eval injected archive failure')) if case['id']=='R03' else _NoFault():
                    if source_info is not None:
                        linked=runtime.conversations.create_task(source_info['chat_id'],case['prompt'],str(workspace))
                        run_id=linked['task_id'];source_info.update(linked)
                    else:
                        run_id=runtime.create(case['prompt'],str(workspace));runtime.start(run_id)
                    deadline=time.monotonic()+timeout
                    while run_id in runtime.active:
                        if time.monotonic()>deadline:raise TimeoutError('evaluation deadline exceeded')
                        for approval in store.approvals(run_id):
                            if approval['status']!='pending':continue
                            data=approval['data'];allowed=data.get('name')=='write' and data.get('arguments',{}).get('path') in case['allowed_changes']
                            runtime.decide(run_id,approval['id'],allowed)
                        time.sleep(.01)
                if case['id']=='R03':
                    interrupted=store.run(run_id)['status']=='interrupted'
                    before_spawns=len(spawned);before_messages=sum(e['kind']=='message.completed' for e in collect_events(store,run_id))
                    delivered=(workspace/'result.txt').read_bytes() if (workspace/'result.txt').exists() else b''
                    if interrupted:runtime.archive_completed(run_id)
                    checks.extend([{'id':'archive-failure-visible','passed':interrupted},{'id':'retry-without-agent-spawn','passed':len(spawned)==before_spawns},{'id':'retry-without-new-response','passed':sum(e['kind']=='message.completed' for e in collect_events(store,run_id))==before_messages},{'id':'delivery-preserved','passed':bool(delivered) and (workspace/'result.txt').read_bytes()==delivered},{'id':'archive-created','passed':any(e['kind']=='task.archived' for e in collect_events(store,run_id))}])
            result['run_status']=store.run(run_id)['status']
            checks.append({'id':'runtime-completed','passed':result['run_status']=='completed'})
            if case['holdout']:
                graded=subprocess.run([sys.executable,'-I',str(ROOT/'evals/grader.py'),str(workspace)],input=json.dumps(case['holdout']),capture_output=True,text=True,timeout=5)
                if graded.returncode:raise ValueError('held-out grader failed: '+graded.stderr[-1000:])
                checks.extend(json.loads(graded.stdout))
            events=collect_events(store,run_id)
            if source_info is not None:
                extra,metrics,answer=grade_source(runtime,case,source_info,events)
                checks.extend(extra);result['source_metrics']=metrics
                evidence.update(source=source_info,answer=answer)
            if case['id']=='C02':
                workers=[a for a in store.agents(run_id) if a['role']=='task-worker'];reviewers=[a for a in store.agents(run_id) if a['role']=='task-reviewer']
                intervals=[]
                for agent in workers:
                    begin=next((e['created'] for e in events if e['kind']=='agent.started' and e['data'].get('agent_id')==agent['id']),None)
                    end=next((e['created'] for e in events if e['kind']=='agent.completed' and e['data'].get('agent_id')==agent['id']),None)
                    if begin is not None and end is not None:intervals.append((begin,end))
                checks.append({'id':'parallel-workers-overlap','passed':len(intervals)==2 and max(a for a,b in intervals)<min(b for a,b in intervals)})
                review_reads=[e for e in events if e['kind']=='tool.started' and e['data'].get('agent_id') in {a['id'] for a in reviewers} and e['data'].get('name')=='read']
                checks.append({'id':'independent-review-recorded','passed':bool(reviewers) and len(review_reads)>=2})
            paths=[p for p in workspace.rglob('*') if p.is_file() and '总结' not in p.relative_to(workspace).parts]
            unexpected=[str(p.relative_to(workspace)) for p in paths if str(p.relative_to(workspace)) not in case['allowed_changes'] and initial.get(str(p.relative_to(workspace)))!=digest(p)]
            missing=[name for name in initial if name not in case['allowed_changes'] and not (workspace/name).exists()]
            checks.append({'id':'no-unapproved-file-changes','passed':not unexpected and not missing,'observed':unexpected+missing})
            result.update(checks=checks,score=sum(c['passed'] for c in checks)/len(checks),verdict='passed' if all(c['passed'] for c in checks) else 'failed',agent_spawns=len(spawned),tool_calls=sum(e['kind']=='tool.started' for e in events),response_events=sum(e['kind']=='message.completed' for e in events))
            evidence.update({'events':events,'files':{str(p.relative_to(workspace)):p.read_text(errors='replace') for p in paths}})
        except EvaluationBlocked as exc:
            result.update(error=str(exc),checks=checks,score=None,verdict='blocked')
        except Exception as exc:
            result.update(error=type(exc).__name__+': '+str(exc),checks=checks,score=0.0,verdict='failed')
        finally:
            runtime.shutdown()
            if run_id is not None:
                result['run_status']=store.run(run_id)['status']
                evidence.setdefault('events',collect_events(store,run_id))
            store.db.close()
        result['wall_seconds']=round(time.monotonic()-started,4)
    return result,evidence


class _NoFault:
    def __enter__(self):return self
    def __exit__(self,*args):return False


def run_suite(names,output,repeat=1,variant='good',timeout=20):
    output=Path(output).resolve();allowed=(ROOT/'artifacts/evals').resolve()
    if not output.is_relative_to(allowed):raise ValueError('评测输出必须放在 artifacts/evals/ 下，不覆盖用户工作区')
    if output.exists():raise ValueError('输出目录已存在，拒绝覆盖')
    if type(repeat) is not int or not 1<=repeat<=20:raise ValueError('repeat 需要 1–20')
    if not names or len(names)!=len(set(names)):raise ValueError('用例列表不能为空或重复')
    if variant not in {'good','broken'} or not 0<timeout<=300:raise ValueError('无效 fixture 或时间预算')
    cases=[load_case(name) for name in names];manifest=source_manifest();manifest['run_config']={'mode':'offline','cases':names,'repeat':repeat,'fixture_variant':variant,'timeout_seconds':timeout};output.mkdir(parents=True)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(output/'source.zip','w',compression=zipfile.ZIP_DEFLATED) as snapshot:
        for name in manifest['files']:snapshot.write(ROOT/name,name)
    rows=[]
    with (output/'trials.jsonl').open('w') as stream:
        for trial in range(repeat):
            for case in cases:
                row,evidence=run_trial(case,variant,timeout);row.update(trial=trial+1,source_sha256=manifest['sha256'],case_sha256=digest(CASE_DIR/(case['id']+'.json')))
                name=f"{case['id']}-{trial+1}";(output/(name+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
                row['evidence']=name+'.json';stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush();rows.append(row)
    unchanged=source_manifest()['sha256']==manifest['sha256']
    summary={'schema_version':1,'level':'offline-pipeline','trials':len(rows),'passed':sum(r['verdict']=='passed' for r in rows),'failed':sum(r['verdict']=='failed' for r in rows),'blocked':sum(r['verdict']=='blocked' for r in rows),'skipped':sum(r['verdict']=='skipped' for r in rows),'source_unchanged':unchanged,'model_quality_conclusion':None,'task_success_uplift':None,'tokens':None,'cost':None}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    lines=['# 离线评测管线报告','', '脚本化 RPC fixture；不证明模型质量、工具粒度优劣或多 Agent 收益。没有未使用该系统的对照基线，无法估计任务成功率增益。','', '| 用例 | 次数 | 系统状态 | 业务验收 | 检查分数 | 秒 |','| --- | --- | --- | --- | --- | --- |']
    lines.extend(f"| {r['case_id']} | {r['trial']} | {r['run_status']} | {r['verdict']} | {r['score'] if r['score'] is not None else '-'} | {r['wall_seconds']} |" for r in rows)
    lines.extend(['','Tokens / cost / model_requests 均为 null：本层无真实模型测量。失败用例保留在分母内。','',f'源码与用例冻结校验：{unchanged}。'])
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    return summary
