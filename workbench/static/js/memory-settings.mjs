import {enhanceSelect,disposeSelect} from './workspace-picker.mjs';
const $=id=>document.getElementById(id);
const toggles=[
 ['task_memory','任务工作记忆','保存并使用当前任务的目标、要求和工作笔记。关闭后停止新增及注入，原始任务历史仍保留。'],
 ['auto_constraints','自动提炼约束','需开启任务工作记忆。只处理开启后的用户要求；保留原文来源，遇到手工约束冲突时由你选择。'],
 ['long_term_write','允许记录长期记忆','明确的“以后 / 默认 / 请记住”类要求会生成项目记忆候选，确认后才能用于其他任务。'],
 ['long_term_read','使用跨任务长期记忆','只读取同项目记忆及你明确保存的全局偏好，当前任务要求优先。'],
 ['tool_offload','保存长工具结果原文','长结果保存到本机，只将预览交给模型；关闭后保留工具的原始返回方式。'],
 ['assembly_history','保存上下文组装快照','用于检查每次实际注入内容。关闭后不新增快照；长期偏好正文不会复制进快照。'],
];
export function createMemorySettings({api,node,showError}){
 let data=null,editing=null,generation=0;
 const root=$('settings-memory-root');
 function button(text,fn){const b=node('button',text);b.type='button';b.onclick=async()=>{b.disabled=true;try{await fn();}catch(e){showError(e.message,'settings-message');}finally{b.disabled=false;}};return b;}
 async function load(){const token=++generation;root.setAttribute('aria-busy','true');try{const value=await api('/api/memory');if(token!==generation)return;data=value;render();}catch(e){if(token===generation)showError(e.message,'settings-message');}finally{if(token===generation)root.removeAttribute('aria-busy');}}
 function render(){
  for(const select of root.querySelectorAll('select'))disposeSelect(select);
  root.replaceChildren();
  const form=node('form','','memory-policy-form'),group=node('div','','settings-group');form.id='memory-policy-form';
  for(const [key,title,help] of toggles){const row=node('label','','settings-row memory-toggle'),copy=node('div'),input=node('input');input.type='checkbox';input.id='memory-'+key;input.setAttribute('role','switch');input.checked=data.policy[key];copy.append(node('strong',title),node('p',help));row.append(copy,input);group.append(row);}
  const row=node('div','','settings-row'),copy=node('div'),ttl=node('select');ttl.id='memory-retention';ttl.setAttribute('aria-label','长期记忆有效期');copy.append(node('strong','长期记忆有效期'),node('p','新保存或确认的记忆使用此期限。到期后在下次访问时清理；修改期限不会延长已有条目。'));for(const days of [30,90,365]){const o=node('option',days+' 天');o.value=String(days);ttl.append(o);}ttl.value=String(data.policy.retention_days);row.append(copy,ttl);group.append(row);form.append(group);
  const save=node('button','保存记忆设置','primary');save.type='submit';form.append(save);
  form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{const values=Object.fromEntries(toggles.map(([key])=>[key,$('memory-'+key).checked]));await api('/api/settings/memory',{revision:data.policy.revision,...values,retention_days:Number(ttl.value)});await load();showError('记忆设置已保存，下次记忆读取或写入即按新设置执行。','settings-message');}catch(e){showError(e.message,'settings-message');}finally{save.disabled=false;}};root.append(form);enhanceSelect(ttl);
  const notice=node('div','','settings-boundary');notice.append(node('strong','关闭记录与删除历史是两件事'),node('p','这些开关控制额外记忆，不删除任务、聊天、附件或 Pi 会话。已有会话可能已经看到过相关内容；关闭开关不会撤回模型已收到的信息。'));root.append(notice);
  const list=node('section','','memory-manager');list.append(node('h4',`长期记忆 · ${data.items.length} / 100`));if(!data.items.length)list.append(node('p','没有长期记忆。新功能默认关闭，不会回扫旧聊天。','muted'));
  for(const item of data.items){const entry=node('article','','memory-manager-item');entry.dataset.memoryId=item.id;entry.append(node('span',item.status==='pending'?'待确认':'已启用','status'),node('small',item.scope==='global'?'全局偏好（所有项目）':'项目：'+item.scope,'muted'),node('p',item.text,'memory-text'),node('small','有效至 '+new Date(item.expires*1000).toLocaleDateString()+(item.source_task_id?' · 来源任务 '+item.source_task_id.slice(0,8):' · 手工保存'),'muted'));const actions=node('div','','node-actions');
   if(item.status==='pending'){const confirm=button('确认使用',async()=>{await api('/api/memory',{action:'confirm',id:item.id,revision:data.revision});await load();});confirm.disabled=!data.policy.long_term_write;actions.append(confirm);}
   const edit=button('编辑',()=>{editing=item;render();$('long-memory-text').focus();});edit.disabled=!data.policy.long_term_write;actions.append(edit,button('删除',async()=>{await api('/api/memory',{action:'delete',id:item.id,revision:data.revision});if(editing?.id===item.id)editing=null;await load();}));entry.append(actions);list.append(entry);
  }root.append(list);
  const editor=node('form','','settings-group long-memory-editor');editor.id='long-memory-form';const textLabel=node('label',editing?'编辑长期记忆':'手工添加长期记忆'),input=node('textarea');input.id='long-memory-text';input.maxLength=1000;input.rows=3;input.required=true;input.value=editing?.text||'';input.placeholder='例如：这个项目的交付文档使用中文';textLabel.append(input);const scopeLabel=node('label','保存范围'),scope=node('select');scope.id='long-memory-scope';for(const value of [...data.workspaces,'global']){const o=node('option',value==='global'?'全局偏好 · 所有项目':value.split('/').at(-1)+' · 仅此项目');o.value=value;scope.append(o);}if(editing)scope.value=editing.scope;scopeLabel.append(scope);editor.append(textLabel,scopeLabel,node('p','优先保存项目约定和工作偏好，不保存凭据、联系方式或明显敏感标识。','muted'));
  const actions=node('div','','form-actions'),submit=node('button',editing?'保存修改':'保存长期记忆','primary');submit.type='submit';submit.disabled=!data.policy.long_term_write;input.disabled=!data.policy.long_term_write;scope.disabled=!data.policy.long_term_write;actions.append(submit);if(editing)actions.append(button('取消编辑',()=>{editing=null;render();}));editor.append(actions);editor.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{await api('/api/memory',{action:'save',revision:data.revision,id:editing?.id,text:input.value,scope:scope.value});editing=null;await load();showError('长期记忆已保存。只有开启跨任务读取后才会用于后续任务。','settings-message');}catch(e){showError(e.message,'settings-message');}finally{submit.disabled=false;}};root.append(editor);enhanceSelect(scope);
  const cleanup=node('section','','memory-cleanup');cleanup.append(node('h4','清理额外记忆'),node('p','只清理本工作台的额外记忆。原始对话、任务结果、Pi 会话和已有数据库备份不会一并删除。','muted'));
  function confirmClear(action,label,help){const area=node('div'),start=button(label,()=>{const confirmation=node('div','','memory-clear-confirm');confirmation.append(node('p',help),button('取消',()=>confirmation.remove()),button('确认清除',async()=>{await api('/api/memory',{action,revision:data.revision,confirm:true});editing=null;await load();showError('额外记忆已清除，原始任务与对话保留。','settings-message');}));area.replaceChildren(start,confirmation);});area.append(start);cleanup.append(area);}
  confirmClear('clear','清除全部长期记忆','清除所有已确认和待确认长期记忆。已发送给模型的内容及原始历史不会被撤回。');
  confirmClear('clear_task_memory','清除并关闭额外任务记忆','清除工作笔记、提炼来源、组装快照和外置原文，并关闭对应记录功能。请先停止运行中的任务；原始任务与对话仍保留。');root.append(cleanup);
 }
 function close(){generation++;editing=null;for(const select of root.querySelectorAll('select'))disposeSelect(select);root.replaceChildren();}
 return {load,close};
}
