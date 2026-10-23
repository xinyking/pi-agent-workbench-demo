import {renderSourcePacket} from './source-packet-view.mjs';
/** Task-scoped context view. User memory and Agent reports stay visibly distinct. */
export function createTaskContextView({api,node,button,getTaskId,showError,openPaper}){
 const target=document.getElementById('task-context-content');
 let generation=0,data=null,editing=null;
 const kinds={constraint:'用户约束',decision:'已记录决定',progress:'工作进度',open_question:'待确认问题'};
 function clear(){generation++;data=null;editing=null;target.replaceChildren();}
 async function load(){
  const id=getTaskId(),token=++generation;if(!id)return;
  try{const result=await api(`/api/runs/${id}/context`);if(token!==generation||getTaskId()!==id)return;data=result;render();}
  catch(error){if(token===generation&&getTaskId()===id)target.replaceChildren(node('p',error.message,'form-error'));}
 }
 function render(){
  if(data?.kind==='external_executor'){
   target.replaceChildren(node('h3','外部执行上下文'),node('p',`${data.vendor} · ${data.mode==='read-only'?'只读分析':'允许修改工作区'}`,'muted'),node('p','初始输入 SHA-256：'+data.digest,'muted'));
   target.append(node('p','每轮使用固定初始输入和历次追加要求，独立启动 CLI；只使用明确选中的资料；不自动导出长期记忆。'));
   const details=node('details');details.append(node('summary','查看实际初始输入'),node('pre',JSON.stringify(data.packet,null,2)));target.append(details);
   for(const attempt of data.attempts)target.append(node('p',`第 ${attempt.number} 轮 · ${attempt.status} · 输入摘要 ${attempt.digest}`,'muted'));
   renderSourcePacket(target,data.source_packet,{node,button,openPaper,showError});
   return;
  }
  // A live refresh must not discard the user's partially edited constraint.
  const previousInput=target.querySelector('[data-memory-draft]'),draft=previousInput?.value;
  const restoreFocus=previousInput===document.activeElement,selection=restoreFocus?[previousInput.selectionStart,previousInput.selectionEnd]:null;
  target.replaceChildren();
  const header=node('header','','task-memory-heading');header.append(node('h3','任务上下文'),node('p','目标和用户约束保留在任务中；Agent 笔记单独标记，原文可按需取回。','muted'));target.append(header);
  if(data.policy&&!data.policy.task_memory)header.append(node('p','任务记忆已关闭。这里可查看既有记录；不会新增工作记忆或注入这些条目。可在设置 → 记忆与隐私中管理。','muted'));
  renderSourcePacket(target,data.source_packet,{node,button,openPaper,showError});
  const grid=node('div','','task-memory-grid'),main=node('section','','task-memory-main'),side=node('aside','','task-memory-side');grid.append(main,side);target.append(grid);
  const goal=node('section','','memory-card');goal.append(node('h4','任务目标'),node('p',data.goal,'memory-text'));
  if(data.latest_request!==data.goal)goal.append(node('h4','最新补充要求'),node('p',data.latest_request,'memory-text'));
  main.append(goal);
  const list=node('section','','memory-card');list.append(node('h4','约束与工作记忆'));
  if(!data.items.length)list.append(node('p','尚未单独记录约束。原始任务与对话仍会保留，可在下方添加需要持续遵守的要求。','muted'));
  for(const item of data.items){const row=node('article','','memory-item');row.append(node('strong',kinds[item.kind]||item.kind),node('small',item.authority==='user'?'来源：用户在任务面板维护':item.authority==='user_extracted'?'来源：自动提炼 · 用户原文':'来源：Agent 工作笔记 · '+(item.source.agent_id==='main'?'主 Agent':item.source.agent_id),'muted'),node('p',item.text,'memory-text'));
   const actions=node('div','','node-actions');actions.append(button('编辑',()=>{editing=item;render();target.querySelector('[data-memory-draft]').value=item.text;target.querySelector('[data-memory-draft]').focus();}),button('移除',async()=>{const id=getTaskId();try{await api(`/api/runs/${id}/context`,{action:'remove',id:item.id,revision:data.revision});if(id===getTaskId())await load();}catch(e){showError(e.message,'detail-error');}}));if(data.policy&&!data.policy.task_memory)for(const b of actions.querySelectorAll('button'))b.disabled=true;row.append(actions);list.append(row);}
  main.append(list);
  for(const conflict of data.conflicts||[]){const card=node('section','','memory-card memory-conflict');card.append(node('h4','约束需要确认'),node('p','新提炼的要求与手工固定的约束不一致：'),node('p',conflict.quote,'memory-text'));for(const [choice,title] of [['accept','采用新约束'],['keep','保留现有约束']])card.append(button(title,async()=>{const id=getTaskId();try{await api(`/api/runs/${id}/context`,{action:'resolve_conflict',conflict_id:conflict.id,choice,revision:data.revision});if(id===getTaskId())await load();}catch(e){showError(e.message,'detail-error');}}));main.append(card);}
  const form=node('form','','memory-card memory-editor'),label=node('label',editing?'编辑记忆 · '+kinds[editing.kind]:'添加用户约束'),input=node('textarea');input.rows=3;input.maxLength=2000;input.required=true;input.dataset.memoryDraft='';input.placeholder='例如：只使用本地资料；交付使用中文；预算不超过 200 元';input.value=draft??editing?.text??'';label.append(input);form.append(label);
  const actions=node('div','','form-actions'),save=node('button',editing?'保存修改':'保存约束','primary');save.type='submit';if(data.policy&&!data.policy.task_memory){save.disabled=true;input.disabled=true;}actions.append(save);if(editing)actions.append(button('取消编辑',()=>{editing=null;input.value='';render();}));form.append(actions);
  form.onsubmit=async event=>{event.preventDefault();const id=getTaskId();save.disabled=true;try{await api(`/api/runs/${id}/context`,{action:'save',id:editing?.id,kind:editing?.kind||'constraint',text:input.value,revision:data.revision});if(id!==getTaskId())return;editing=null;input.value='';await load();}catch(e){showError(e.message,'detail-error');await load();}finally{save.disabled=false;}};main.append(form);
  const stats=node('section','','memory-card');stats.append(node('h4','最近一次上下文组装'));
  const assembly=data.assemblies?.[0];
  if(assembly){const m=assembly.data;if(m.payload.revision<data.revision)stats.append(node('p','当前记忆已更新，将在下一次模型调用中使用。','muted'));stats.append(node('p',`记忆版本 ${assembly.revision??m.payload.revision} · ${(m.bytes/1024).toFixed(1)} / ${(m.budget_bytes/1024).toFixed(0)} KB`),node('p',`${m.payload.user_memory.length} 条用户记忆 · ${m.payload.agent_notes.length} 条 Agent 笔记 · ${m.source_context_ids.length} 份原始对话快照`,'muted'));if(m.omitted_optional_items)stats.append(node('p',`${m.omitted_optional_items} 项可选笔记或计划未放入本次上下文，可通过记忆工具取回。`,'muted'));stats.append(node('p','这里只统计工作台注入的任务记忆；完整会话占用由上方 Pi 上下文状态展示。','muted'));
   const detail=node('details');detail.append(node('summary','查看本次组装内容与来源'),node('pre',JSON.stringify(m.payload,null,2),'memory-projection'));stats.append(detail);
  }else stats.append(node('p',data.policy?.assembly_history===false?'组装快照记录已关闭。模型仍按当前开关读取所需记忆。':'任务下一次调用模型时，会组装并记录当前上下文。','muted'));
  side.append(stats);
  const artifacts=node('section','','memory-card');artifacts.append(node('h4','外置工具原文'));
  if(!data.artifacts?.length)artifacts.append(node('p','较大的工具结果会保留原文，并以预览进入上下文。','muted'));
  for(const a of data.artifacts||[]){
   const entry=node('details');entry.append(node('summary',`${a.name} · ${a.chars.toLocaleString()} 字符`));const content=node('pre','','memory-projection'),more=button('读取原文',async()=>{
    const id=getTaskId();const result=await api(`/api/runs/${id}/context/artifacts/${a.id}?offset=${offset}`);if(id!==getTaskId()||!target.contains(entry))return;
    content.textContent+=result.text;offset=result.next_offset;more.textContent='继续读取';more.hidden=offset===null;
   });let offset=0;entry.append(content,more,node('small','原文编号 '+a.id,'muted'));artifacts.append(entry);
  }
  side.append(artifacts);
  const durable=node('section','','memory-card');durable.append(node('h4','跨任务长期记忆'));if(!data.policy?.long_term_read)durable.append(node('p','跨任务读取已关闭。可在设置 → 记忆与隐私中开启并管理。','muted'));else if(!data.long_term?.length)durable.append(node('p','没有适用于此项目的已确认长期记忆。','muted'));for(const item of data.long_term||[])durable.append(node('small',item.scope==='global'?'全局偏好':'同项目的历史偏好','muted'),node('p',item.text,'memory-text'));side.append(durable);
  const compaction=node('section','','memory-card');compaction.append(node('h4','压缩与恢复'));
  if(!data.compactions?.length)compaction.append(node('p','尚无压缩记录。Pi 压缩历史后，任务记忆仍会在后续模型调用前重新载入。','muted'));
  for(const c of data.compactions||[]){const d=node('details');d.append(node('summary',new Date(c.created*1000).toLocaleString()+' · '+(c.error?'压缩失败':'已压缩')),node('p',c.error||c.summary||'已保存压缩记录','memory-text'));compaction.append(d);}
  side.append(compaction);
  if(restoreFocus){input.focus({preventScroll:true});input.setSelectionRange(...selection);}
 }
 return {load,clear};
}
