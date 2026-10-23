import {createTaskContextView} from './task-context.mjs';
import {createViewSession} from './view-session.mjs';
import {buildFlow,eventsForNode} from './flow.mjs';
import {createTimelineRenderer} from './task-timeline.mjs';
const $=id=>document.getElementById(id);
export function createTaskDetailController({api,node,button,labels,active,terminal,kinds,showError,openDialog,markdownNode,setMarkdown,renderOutput,renderContextState,downloadMarkdown,refreshBoard,scheduleRefresh,setConnection,getTasks,openPaper}){
const session=createViewSession();
let detailAgents=[],detailInputs=[],flowGraph=null,selectedNode=null,currentTab='flow',flowTimer=null,selectedScope=null;
let selected=null,detailSource=null,detailCursor=0,currentRun=null,selectedAgent='all',detailEvents=[];
const answers=new Map(),answerSources=new Map();
const contextView=createTaskContextView({api,node,button,getTaskId:()=>selected,showError,openPaper});
function taskActions(task,container,detail=false){
  if(!['pi','codex','claude'].includes(task.provider_id)){if(detail)container.append(node('span',task.provider_id==='external'?'外部执行：'+task.model+' · 由来源工具回传进度':'旧版任务只读历史','muted'));return;}
  const action=(label,path,primary=false)=>container.append(button(label,async()=>{await api(`/api/runs/${task.id}/${path}`,path==='followup'?{prompt:'继续之前的任务。先检查已有改动和执行结果，避免重复操作。'}:{});await refreshBoard();if(selected===task.id){await loadDetailState();connectDetail();}},primary));
  if(task.status==='queued')action('开始任务','start',true);
  if(task.status==='cancelled')action('继续任务','followup',true);
  if(task.status==='awaiting_confirmation'&&['codex','claude'].includes(task.provider_id))action('继续任务','followup',true);
  if(task.status==='failed')action('重试任务','start',true);if(task.status==='interrupted'){const archiveRetry=(task.reason||'').startsWith('交付已产生，但保存工作区总结失败：');action(archiveRetry?'重新保存总结':(task.reason||'').startsWith('待核对并收尾：')?'继续收尾':'继续任务',archiveRetry?'archive':'start',true);}if(task.status==='completed'&&detail&&task.provider_id==='pi')action('生成总结与归档','archive');
  if(['waiting_input','waiting_approval'].includes(task.status)&&!detail)container.append(button(task.status==='waiting_input'?'补充信息':'处理审批',()=>selectTask(task.id),true));
  
  if(active.has(task.status)||task.status==='queued')action('停止','cancel');
  if(!container.children.length&&!detail)container.append(button('查看详情',()=>selectTask(task.id)));
}
function renderDetailActions(){if(!currentRun)return;$('tab-context').hidden=!['pi','codex','claude'].includes(currentRun.provider_id);const target=$('detail-actions');target.replaceChildren(node('span',labels[currentRun.status]||currentRun.status,'status'));target.firstElementChild.dataset.status=currentRun.status;taskActions(currentRun,target,true);$('detail-meta').textContent=`${currentRun.workspace||'工作区未设置'} · ${currentRun.model||'模型未设置'}`;$('records-timeline-hint').textContent=currentRun.provider_id==='external'?'来源工具回传实际步骤和结果；执行与停止由来源软件负责。':['codex','claude'].includes(currentRun.provider_id)?'每轮外部执行合并显示；工具事件来自 CLI，交付检查由工具自报。':'同一 Agent 的同一执行轮次合并显示；模型回复后可调用工具，再根据结果继续判断。';const disabled=active.has(currentRun.status)||!['pi','codex','claude'].includes(currentRun.provider_id);$('task-followup').hidden=disabled;$('followup-submit').disabled=disabled;$('followup').disabled=disabled;}
async function loadApprovals(){if(!selected)return;const runId=selected;const data=await api(`/api/runs/${runId}`);if(selected!==runId)return;renderApprovals(data.approvals);detailInputs=data.inputs||[];renderInputs();}
function renderApprovals(approvals){$('approvals').replaceChildren();for(const approval of approvals.filter(a=>a.status==='pending')){const a=node('section','','approval');a.append(node('h3',`需要你的审批 · ${approval.data.name}`),node('small',`工作区：${approval.data.workspace}`),node('pre',approval.data.diff!==undefined?approval.data.diff:JSON.stringify(approval.data,null,2)));const actions=node('div','','approval-buttons');for(const allowed of [false,true])actions.append(button(allowed?'允许本次执行':'拒绝',async()=>{await api(`/api/runs/${selected}/approval`,{id:approval.id,allowed});await loadApprovals();await refreshBoard();},allowed));a.append(actions);$('approvals').append(a);}}
async function loadDetailState(){if(!selected)return;const id=selected,token=session.token;const data=await api(`/api/runs/${id}`);if(id!==selected||!session.isCurrent(token))return;currentRun=data.run;$('detail-title').textContent=data.run.prompt;renderDetailActions();renderApprovals(data.approvals);detailAgents=data.agents||[];detailInputs=data.inputs||[];renderAgents(detailAgents);renderInputs();scheduleFlow();if(currentRun.status==='completed'&&currentRun.provider_id==='pi')loadArchive(id).catch(e=>showError(e.message,'detail-error'));else $('task-archive').hidden=true;}
async function loadArchive(id){
 const data=await api(`/api/runs/${id}/archive`);if(selected!==id||currentRun?.status!=='completed')return;
 const target=$('task-archive'),wasOpen=target.querySelector('details')?.open;target.replaceChildren();target.hidden=!data.archives?.length;if(target.hidden)return;
 const archive=data.archives[0],details=node('details');details.open=Boolean(wasOpen);details.append(node('summary','已完成总结 · '+archive.file_count+' 个归档文件'));
 details.append(node('p','归档目录：'+currentRun.workspace+'/'+archive.directory,'archive-path'));
 if(archive.skipped_count)details.append(node('p',`${archive.skipped_count} 个文件未收集，原因见总结中的文件清单。`,'muted'));
 details.append(markdownNode(data.summary_markdown));const actions=node('div','','node-actions');actions.append(button('复制归档路径',()=>navigator.clipboard.writeText(currentRun.workspace+'/'+archive.directory)),button('下载总结',()=>downloadMarkdown(data.summary_markdown)));details.append(actions);target.append(details);
}
function renderAgents(agents){const target=$('agent-list');target.replaceChildren();if(selectedScope)target.append(node('span','当前阶段：'+selectedScope.title,'muted'));const all=button('全部记录',()=>filterAgent('all'));all.setAttribute('aria-pressed',String(selectedAgent==='all'));target.append(all);const attempt=agents.find(a=>a.id==='main')?.data?.attempt_id;for(const agent of agents){const historical=agent.parent_id&&agent.data?.attempt_id!==attempt;const name=(agent.parent_id?'↳ ':'')+agent.role+(historical?'（历史）':'')+' · '+(agent.status==='completed'?'执行完成':labels[agent.status]||agent.status);const b=button(name,()=>filterAgent(agent.id));b.title=agent.task;b.setAttribute('aria-pressed',String(selectedAgent===agent.id));b.className='agent-chip '+agent.status;target.append(b);}}
function filterAgent(id,scope=null){selectedAgent=id;selectedScope=scope;answers.clear();answerSources.clear();$('messages').replaceChildren();$('events').replaceChildren();for(const event of detailEvents)renderEvent(event,true);renderTimeline();loadDetailState().catch(e=>showError(e.message,'detail-error'));}
function renderEvent(event,replay=false){
 const data=event.data,agentId=data.agent_id||'main';
 if(event.kind==='plan.updated'&&currentRun)currentRun.plan=data.steps;
 if(event.kind.startsWith('context.')&&agentId==='main')renderContextState($('detail-context'),data);
 if(!replay){detailEvents.push(event);if(['approval.requested','approval.resolved','external.progress','agent.started','agent.status','agent.completed','input.requested','input.answered'].includes(event.kind))loadDetailState().catch(e=>showError(e.message,'detail-error'));}
 if(!replay&&currentTab==='context'&&event.kind.startsWith('memory.'))contextView.load();
 if(!replay)scheduleFlow();
 if(selectedAgent!=='all'&&agentId!==selectedAgent)return;
 if(selectedScope&&!eventsForNode(selectedScope,[event]).length)return;
 const role=agentId==='main'?(currentRun?.provider_id==='external'?currentRun.model:currentRun?.provider_id==='codex'?'Codex CLI':currentRun?.provider_id==='claude'?'Claude Code':'主 Agent'):(detailEvents.find(e=>e.kind==='agent.started'&&e.data.agent_id===agentId)?.data.role||'子 Agent');
 if(event.kind==='message.user'){const text=data.text||'';if(text.length>400){const entry=node('details','','user record-long-message');entry.append(node('summary',text.replace(/\s+/g,' ').slice(0,110)+'… · 展开要求'),node('div',text));$('messages').append(entry);}else $('messages').append(node('div',text,'user'));}
 else if(event.kind==='agent.started'&&data.parent_agent_id){$('messages').append(node('div',`${role} · 委派任务\n${data.task}`,'user'));}
 else if(event.kind==='message.started'){const text=node('div','','answer');text.append(node('small',role,'agent-message-label'),node('div'));$('messages').append(text);answers.set(agentId,text.lastChild);answerSources.set(agentId,'');}
 else if(event.kind==='message.delta'){if(!answers.has(agentId)){const text=node('div','','answer');text.append(node('small',role,'agent-message-label'),node('div'));$('messages').append(text);answers.set(agentId,text.lastChild);answerSources.set(agentId,'');}answerSources.set(agentId,(answerSources.get(agentId)||'')+(data.text||''));setMarkdown(answers.get(agentId),answerSources.get(agentId));}
 else if(event.kind==='message.completed'&&data.text&&answers.has(agentId)){answerSources.set(agentId,data.text);setMarkdown(answers.get(agentId),data.text);}
 else if(event.kind==='error'){$('messages').append(node('div',data.text,'system-message'));}
 if(!replay)scheduleTimeline();
 $('messages').scrollTop=$('messages').scrollHeight;
}
let timelineTimer=null;
function scheduleTimeline(){if(timelineTimer)return;timelineTimer=setTimeout(()=>{timelineTimer=null;renderTimeline();},120);}
const renderTimelineView=createTimelineRenderer({node,labels,kinds,renderOutput});
function renderTimeline(){
 let events=selectedScope?eventsForNode(selectedScope,detailEvents):detailEvents;
 if(selectedAgent!=='all')events=events.filter(e=>(e.data.agent_id||'main')===selectedAgent);
 renderTimelineView($('events'),events,detailAgents,currentRun);
}

function switchTab(name){currentTab=name;for(const tab of ['flow','records','files','context']){$('pane-'+tab).hidden=tab!==name;$('tab-'+tab).setAttribute('aria-selected',String(tab===name));}if(name==='flow')requestAnimationFrame(drawEdges);if(name==='files')renderFiles();if(name==='records')renderTimeline();if(name==='context')contextView.load();}
for(const b of document.querySelectorAll('[data-tab]'))b.onclick=()=>switchTab(b.dataset.tab);
function renderInputs(){
 const target=$('input-requests');
 const pending=detailInputs.filter(r=>r.status==='pending');
 // Keep the user's draft intact while background snapshots refresh.
 for(const child of [...target.children])if(!pending.some(r=>r.id===child.dataset.request))child.remove();
 for(const request of pending){
  if([...target.children].some(c=>c.dataset.request===request.id))continue;
  const form=node('form','','input-request');form.dataset.request=request.id;
  form.append(node('h3','需要补充信息'),node('p',request.data.question));
  const label=node('label','你的回答'),input=node('textarea');input.rows=3;input.maxLength=16000;input.required=true;label.append(input);form.append(label);
  const choices=node('div','','input-options');for(const option of request.data.options||[])if(typeof option==='string')choices.append(button(option,()=>{input.value=option;input.focus();}));form.append(choices);
  const submit=node('button','提交回答并继续原任务','primary');submit.type='submit';form.append(submit);
  form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{await api(`/api/runs/${selected}/answer`,{id:request.id,answer:input.value});await loadDetailState();await refreshBoard();connectDetail();}catch(e){showError(e.message,'detail-error');}finally{submit.disabled=false;}};
  target.append(form);
 }
}
function scheduleFlow(){if(flowTimer)return;flowTimer=setTimeout(()=>{flowTimer=null;renderFlow();},160);}
function renderFlow(){
 if(!currentRun||!$('detail-dialog').open)return;
 renderDeclaredPlan();
 flowGraph=buildFlow(currentRun,detailAgents,detailInputs,detailEvents);
 const container=$('flow-rows'),focusedNode=document.activeElement?.dataset?.node;container.replaceChildren();
 for(const row of flowGraph.rows){const group=node('div','','flow-row');for(const item of row){const b=button('',()=>{selectedNode=item.id;renderNodeDetail();for(const n of container.querySelectorAll('[data-node]'))n.setAttribute('aria-pressed',String(n.dataset.node===item.id));});b.dataset.node=item.id;b.className='flow-node '+item.status;b.setAttribute('aria-pressed',String(selectedNode===item.id));b.append(node('strong',item.title),node('span',item.text,'flow-description'),node('small',labels[item.status]||item.status));const calls=eventsForNode(item,detailEvents).filter(e=>e.kind==='tool.started'||e.kind==='tool.completed');if(calls.length&&['running','waiting_input','waiting_approval'].includes(item.status)){const call=calls.at(-1);b.append(node('small',(call.kind==='tool.started'?'正在调用：':'最近工具：')+call.data.name));}group.append(b);}container.append(group);}
 if(focusedNode)[...container.querySelectorAll('[data-node]')].find(n=>n.dataset.node===focusedNode)?.focus({preventScroll:true});renderNodeDetail();renderFiles();requestAnimationFrame(drawEdges);
}
function drawEdges(){
 if(!flowGraph||currentTab!=='flow'||!$('detail-dialog').open)return;
 const svg=$('flow-edges'),canvas=$('flow-canvas'),rect=canvas.getBoundingClientRect();svg.replaceChildren();svg.setAttribute('width',0);svg.setAttribute('height',0);svg.setAttribute('width',canvas.scrollWidth);svg.setAttribute('height',canvas.scrollHeight);svg.setAttribute('viewBox',`0 0 ${canvas.scrollWidth} ${canvas.scrollHeight}`);
 const boxes=new Map([...$('flow-rows').querySelectorAll('[data-node]')].map(n=>[n.dataset.node,n.getBoundingClientRect()]));
 for(const [from,to] of flowGraph.edges){const a=boxes.get(from),b=boxes.get(to);if(!a||!b)continue;const x1=a.right-rect.left+canvas.scrollLeft,y1=a.top+a.height/2-rect.top+canvas.scrollTop,x2=b.left-rect.left+canvas.scrollLeft,y2=b.top+b.height/2-rect.top+canvas.scrollTop;const p=document.createElementNS('http://www.w3.org/2000/svg','path');p.setAttribute('d',`M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2} m-6,-4 l6,4 l-6,4`);svg.append(p);}
}
const resizeObserver=new ResizeObserver(()=>requestAnimationFrame(drawEdges));resizeObserver.observe($('flow-canvas'));
$('copy-flow').onclick=async()=>{if(!flowGraph)return;try{await navigator.clipboard.writeText('```mermaid\n'+flowGraph.mermaid+'\n```');$('copy-flow').textContent='已复制';}catch{$('node-detail').replaceChildren(node('pre','```mermaid\n'+flowGraph.mermaid+'\n```'));}};
function renderNodeDetail(){
 const target=$('node-detail'),item=flowGraph?.nodes.find(n=>n.id===selectedNode);if(!item)return;const focusKey=document.activeElement?.dataset?.nodeAction;
 target.replaceChildren(node('h3',item.title),node('p',item.text),node('span',labels[item.status]||item.status,'status'));
 if(item.requestId){const request=detailInputs.find(r=>r.id===item.requestId);if(request?.answer)target.append(node('p','用户回答：'+request.answer));if(request?.status==='pending')target.append(button('补充信息',()=>{const form=[...$('input-requests').children].find(n=>n.dataset.request===item.requestId);form?.scrollIntoView({block:'center'});form?.querySelector('textarea')?.focus();},true));}
 const events=eventsForNode(item,detailEvents);
 const result=[...events].reverse().find(e=>e.kind==='message.completed'&&e.data.text);if(result)target.append(node('h4','最近输出'),markdownNode(result.data.text));
 for(const output of events.filter(e=>e.kind==='output.structured'))target.append(renderOutput(output.data.output));
 const tools=events.filter(e=>e.kind==='tool.started');const files=[...new Set(events.filter(e=>e.kind==='file.changed').map(e=>e.data.path))];
 target.append(node('p',`已记录 ${tools.length} 次工具调用 · ${files.length} 个文件变化`));
 if(tools.length)target.append(node('p','工具：'+[...new Set(tools.map(e=>e.data.name))].join('、')));
 const actions=node('div','','node-actions');actions.append(button('查看执行记录',()=>{filterAgent(item.agentId,item);switchTab('records');}),button('查看文件变化',()=>{selectedAgent=item.agentId;selectedScope=item;switchTab('files');}));for(const [index,b] of [...actions.children].entries())b.dataset.nodeAction=String(index);target.append(actions);if(focusKey)actions.querySelector(`[data-node-action="${focusKey}"]`)?.focus({preventScroll:true});
}
function renderFiles(){
 const changes=detailEvents.filter(e=>e.kind==='file.changed');$('files-count').textContent=new Set(changes.map(e=>e.data.path)).size;
 const visible=changes.filter(e=>(selectedAgent==='all'||(e.data.agent_id||'main')===selectedAgent)&&(!selectedScope||eventsForNode(selectedScope,[e]).length)),groups=new Map();for(const event of visible){const key=event.data.path;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(event);}
 const target=$('files');target.replaceChildren();$('file-filter').replaceChildren(node('span',selectedAgent==='all'?'全部 Agent 的文件变化':'当前 Agent 的文件变化'),button('查看全部',()=>{selectedAgent='all';selectedScope=null;renderFiles();}));
 if(!groups.size)target.append(node('p','暂无已记录的文件变化。Pi write / edit 工具的成功修改会显示在这里。','muted'));
 for(const [path,events] of groups){const section=node('section','','file-change');section.append(node('h3',path));for(const event of events){const role=detailAgents.find(a=>a.id===event.data.agent_id)?.role||'主 Agent';section.append(node('small',`${role} · ${new Date(event.created*1000).toLocaleString()}${event.data.batch_id?' · 阶段 '+event.data.batch_id.slice(0,6):''}`));const diff=node('pre','','diff');if(event.data.diff){for(const line of event.data.diff.split('\n'))diff.append(node('div',line,line.startsWith('+')?'diff-add':line.startsWith('-')?'diff-remove':''));}else diff.textContent='修改已记录；此工具未返回差异内容。';section.append(diff);}target.append(section);}
}
function connectDetail(){if(!selected||!$('detail-dialog').open||detailSource)return;const id=selected,token=session.token;const stream=new EventSource(`/api/runs/${id}/events?after=${detailCursor}`);detailSource=stream;const release=session.own(()=>stream.close());stream.onmessage=event=>{if(selected!==id||detailSource!==stream||!session.isCurrent(token))return;const data=JSON.parse(event.data);if(data.id<=detailCursor)return;detailCursor=data.id;renderEvent(data);if(data.kind==='status'){scheduleRefresh();loadDetailState().catch(e=>showError(e.message,'detail-error'));}};stream.addEventListener('caught-up',()=>{if(detailSource!==stream||!session.isCurrent(token))return;stream.close();release();if(detailSource===stream)detailSource=null;const latest=getTasks().find(t=>t.id===id);if(selected===id&&latest&&!terminal.has(latest.status)){const timer=setTimeout(()=>{if(session.isCurrent(token))connectDetail();},300);session.own(()=>clearTimeout(timer));}});stream.onerror=()=>{if(selected===id&&detailSource===stream&&session.isCurrent(token))setConnection(false);};}
async function selectTask(id){contextView.clear();const token=session.begin(id);detailSource?.close();detailSource=null;selected=id;detailCursor=0;currentRun=null;selectedAgent='all';selectedScope=null;detailEvents=[];detailAgents=[];detailInputs=[];flowGraph=null;selectedNode=null;$('flow-rows').replaceChildren();$('flow-edges').replaceChildren();$('node-detail').replaceChildren(node('p','点击流程节点，查看任务、输出、工具与文件变化。','muted'));$('input-requests').replaceChildren();switchTab('flow');answers.clear();answerSources.clear();$('detail-context').replaceChildren();$('agent-list').replaceChildren();$('messages').replaceChildren();$('events').replaceChildren();$('approvals').replaceChildren();$('followup').value='';$('task-followup').open=false;showError('','detail-error');const task=getTasks().find(t=>t.id===id);$('detail-title').textContent=task?.prompt||'任务详情';await loadDetailState();if(!session.isCurrent(token))return;openDialog('detail-dialog');connectDetail();}
$('detail-dialog').addEventListener('close',dispose);
$('followup-form').onsubmit=async event=>{event.preventDefault();const b=$('followup-submit');b.disabled=true;try{await api(`/api/runs/${selected}/followup`,{prompt:$('followup').value});$('followup').value='';await refreshBoard();await loadDetailState();connectDetail();}catch(e){showError(e.message,'detail-error');}finally{renderDetailActions();}};
function renderDeclaredPlan(){const target=$('declared-plan');target.replaceChildren();let steps=currentRun?.plan||[];if(typeof steps==='string'){try{steps=JSON.parse(steps);}catch{steps=[];}}if(!steps.length)return;target.append(node('h3','任务编排计划'));for(const step of steps){const row=node('div','','plan-step '+step.status);row.append(node('span',{pending:'待执行',running:'执行中',completed:'已完成',failed:'失败'}[step.status]||step.status,'status'),node('strong',step.title),node('small',step.agent||''));if(step.depends_on?.length)row.append(node('small','依赖：'+step.depends_on.join('、')));target.append(row);}target.append(node('p','计划说明预期步骤；下方流程图记录实际发生的执行。','muted'));}

async function updateTasks(tasks){
 if(!selected||!$('detail-dialog').open)return;const task=tasks.find(t=>t.id===selected);if(!task)return;
 const changed=currentRun?.status!==task.status;currentRun=task;renderDetailActions();scheduleFlow();
 if(changed){await loadDetailState();if(!terminal.has(task.status))connectDetail();}
}
function dispose(){contextView.clear();session.close();detailSource?.close();detailSource=null;clearTimeout(timelineTimer);clearTimeout(flowTimer);timelineTimer=null;flowTimer=null;selected=null;currentRun=null;}
return {selectTask,loadDetailState,connectDetail,updateTasks,dispose,get selected(){return selected;}};
}
