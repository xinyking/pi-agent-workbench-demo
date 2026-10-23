"""Workspace delivery archives made from observed task files and source attachments."""
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

MAX_FILE=10*1024*1024
MAX_TOTAL=50*1024*1024
BLOCKED={'.git','.ssh','.aws','.codex','.workbench','node_modules','.venv','__pycache__'}


def task_events(store,run_id):
    cursor=0
    while True:
        batch=store.events(run_id,cursor)
        if not batch:return
        yield from batch
        cursor=batch[-1]['id']


def read_archive(store,run,workspace):
    with store.lock:
        rows=store.db.execute("SELECT data FROM events WHERE run_id=? AND kind='task.archived' ORDER BY id DESC",(run['id'],)).fetchall()
    archives=[json.loads(row['data']) for row in rows]
    if not archives:return {'archives':[],'summary_markdown':''}
    workspace=Path(workspace).resolve()
    relative=Path(archives[0]['summary_path'])
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0]!='总结':raise ValueError('无效的总结路径')
    for i in range(1,len(relative.parts)+1):
        if workspace.joinpath(*relative.parts[:i]).is_symlink():raise ValueError('总结路径包含符号链接')
    summary=(workspace/relative).resolve()
    if not summary.is_relative_to(workspace):raise ValueError('总结路径超出工作区')
    if summary.stat().st_size>2*1024*1024:raise ValueError('总结文件超过 2 MiB')
    return {'archives':archives,'summary_markdown':summary.read_text(encoding='utf-8')}


def create_archive(store,run,workspace,summary,data_directory,stop=None):
    workspace=Path(workspace).resolve()
    stamp=datetime.now().astimezone()
    title=re.sub(r'[^\w\u4e00-\u9fff-]+','-',run['prompt'],flags=re.UNICODE).strip('-')[:32] or '任务'
    root=workspace/'总结'
    task_dir=root/(title+'-'+run['id'][:8])
    folder=task_dir/('交付-'+stamp.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    for target in (root,task_dir):
        if target.is_symlink():raise ValueError('总结目录不能是符号链接')
        target.mkdir(exist_ok=True)
        if not target.resolve().is_relative_to(workspace):raise ValueError('总结目录超出工作区')
    folder.mkdir()
    entries=[];total=0;seen=set();trace=[];plans=[];roles=set();answer=''

    def cancelled():
        if stop and stop.is_set():
            from .pi_client import PiCancelled
            raise PiCancelled()

    def collect(source,target,category,label):
        nonlocal total
        cancelled()
        entry={'source':label,'category':category}
        created=False
        try:
            if len(entries)>=100:raise ValueError('超过 100 个文件上限')
            if source.is_symlink():raise ValueError('不归档符号链接')
            with open(source,'rb',opener=lambda path,flags:os.open(path,flags|os.O_NOFOLLOW|os.O_NONBLOCK)) as src:
                info=os.fstat(src.fileno())
                if not stat.S_ISREG(info.st_mode):raise ValueError('不是普通文件')
                if info.st_size>MAX_FILE or total+info.st_size>MAX_TOTAL:raise ValueError('超过单文件 10 MiB 或总计 50 MiB 上限')
                target.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256();size=0
                with target.open('xb') as dst:
                    created=True
                    while chunk:=src.read(1024*1024):
                        cancelled();size+=len(chunk)
                        if size>MAX_FILE or total+size>MAX_TOTAL:raise ValueError('文件在复制时超过大小上限')
                        digest.update(chunk);dst.write(chunk)
                after=os.fstat(src.fileno())
                if (after.st_size,after.st_mtime_ns)!=(info.st_size,info.st_mtime_ns):raise ValueError('文件在归档时发生变化')
            total+=size
            entry.update(status='copied',archive=str(target.relative_to(folder)),bytes=size,sha256=digest.hexdigest())
        except (OSError,ValueError) as exc:
            if created and target.is_file():target.unlink()
            entry.update(status='skipped',reason=str(exc))
        entries.append(entry)

    def local_file(raw,category):
        if not isinstance(raw,str) or not raw:return
        candidate=Path(raw)
        if not candidate.is_absolute():candidate=workspace/candidate
        try:
            relative=candidate.relative_to(workspace)
            if any(part in BLOCKED or part.startswith(('.env','.workbench')) for part in relative.parts):raise ValueError('受保护或依赖目录')
            if relative.parts[0]=='总结':raise ValueError('跳过总结目录，避免递归归档')
            for index in range(1,len(relative.parts)+1):
                if workspace.joinpath(*relative.parts[:index]).is_symlink():raise ValueError('路径含符号链接')
            resolved=candidate.resolve()
            if not resolved.is_relative_to(workspace) or resolved.is_relative_to(Path(data_directory).resolve()):raise ValueError('文件不在允许的工作区范围')
            relative=resolved.relative_to(workspace)
            if relative.parts[0]=='总结':raise ValueError('跳过总结目录，避免递归归档')
            if str(relative) in seen:return
            seen.add(str(relative))
            collect(resolved,folder/('交付文件' if category=='output' else '参考文件')/relative,category,str(relative))
        except (ValueError,OSError) as exc:entries.append({'source':raw,'category':category,'status':'skipped','reason':str(exc)})

    try:
        events=list(task_events(store,run['id']))
        # Outputs take precedence when a file was also read earlier.
        for event in events:
            if event['kind']=='file.changed':local_file(event['data'].get('path'),'output')
        for event in events:
            cancelled();kind=event['kind'];data=event['data']
            if kind=='tool.started' and data.get('name')=='read':local_file(data.get('arguments',{}).get('path'),'reference')
            if kind=='plan.updated':plans=data.get('steps',[])
            if kind=='agent.started':roles.add(data.get('role','Agent'))
            if kind=='message.completed' and data.get('agent_id')=='main' and data.get('text'):answer=data['text']
            if kind in ('agent.started','agent.completed','tool.started','tool.completed','file.changed','input.requested','input.answered','status'):
                trace.append({'time':event['created'],'kind':kind,**{k:data[k] for k in ('agent_id','role','name','status','is_error','path') if k in data}})
        with store.lock:
            snapshots=store.db.execute('SELECT data FROM context_snapshots WHERE task_id=?',(run['id'],)).fetchall()
        documents={d['id']:d for row in snapshots for d in json.loads(row['data']).get('documents',[])}
        for doc in documents.values():
            source=Path(data_directory)/'uploads'/doc['id']/('source'+doc['kind'])
            name=re.sub(r'[^\w.\u4e00-\u9fff-]+','-',doc['name'])[:120]
            collect(source,folder/'来源附件'/(doc['id']+'-'+name),'attachment',doc['name'])
        manifest={'task_id':run['id'],'title':run['prompt'],'created':stamp.isoformat(),'summary':summary,'files':entries,'total_bytes':total,'limits':{'file_bytes':MAX_FILE,'total_bytes':MAX_TOTAL,'file_count':100},'scope':'任务记录中已修改/读取的工作区文件和任务上下文中的附件；不扫描整个项目，未被工具记录的外部文件不自动收集。'}
        (folder/'清单.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (folder/'执行轨迹.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        lines=['# 任务完成总结','',run['prompt'],'',f'- 任务 ID：`{run["id"]}`',f'- 交付时间：{stamp.isoformat(timespec="seconds")}',f'- 协作角色：{", ".join(sorted(roles)) or "主 Agent"}','','## 完成摘要','',summary]
        if answer and answer.strip()!=summary.strip():lines+=['','## 最终交付','',answer]
        if plans:lines+=['','## 执行计划','']+[f'- [{"x" if s.get("status")=="completed" else " "}] {s.get("title",s.get("id","步骤"))}' for s in plans]
        lines+=['','## 相关文件','']
        for entry in entries:
            if entry['status']=='copied':lines.append(f'- [{entry["source"]}]({quote(entry["archive"])}) · {entry["category"]}')
            else:lines.append(f'- 未收集：`{entry["source"]}` · {entry["reason"]}')
        if not entries:lines.append('本次任务未记录需要归档的文件。')
        lines+=['','## 归档说明','',manifest['scope'],'原文件保留原位，归档内容为完成时副本；每次追加任务后完成会产生新的交付版本，不覆盖旧总结。','文件清单和 SHA-256 见 [清单.json](清单.json)，工具与 Agent 时间记录见 [执行轨迹.json](执行轨迹.json)。']
        (folder/'总结.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        cancelled()
        return {'directory':str(folder.relative_to(workspace)),'summary_path':str((folder/'总结.md').relative_to(workspace)),'file_count':sum(e['status']=='copied' for e in entries),'skipped_count':sum(e['status']=='skipped' for e in entries)}
    except BaseException:
        shutil.rmtree(folder)
        raise
