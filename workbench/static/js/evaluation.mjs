import {initDeepseekBilling} from './billing.mjs';
import {renderUsageSummary,summarizeUsage,taskEstimate,estimateText,totalEstimate,renderTaskEstimate} from './usage-view.mjs';
// Runtime checks and saved retrospective reviews remain distinct.
export const reviewLabel=r=>!r?'未评测':r.stale?'待重评':({passed:'通过',partial:'部分达标',incomplete:'未完成',failed:'不通过'}[r.verdict]||'未知');
export function taskEvaluation(run,events){
 const has=kind=>events.some(e=>e.kind===kind);
 const tools=events.filter(e=>e.kind==='tool.started').length;
 const errors=events.filter(e=>e.kind==='error'||e.kind==='tool.completed'&&e.data?.is_error);
 return {status:run.status,completed:run.status==='completed',delivery:has('task.reported')||has('executor.result')||has('output.structured'),confirmed:has('task.confirmed'),tools,errors:errors.length};
}
// Event identities include attempt and agent: tool IDs can repeat between workers.
export function evaluationMetrics(events){
 const seen=new Set(),unique=events.filter(e=>{if(e.id==null)return true;if(seen.has(e.id))return false;seen.add(e.id);return true;});
 const key=e=>`${e.data?.attempt_id||''}:${e.data?.agent_id||'main'}`;
 const scopes=new Map();
 for(const e of unique){const k=key(e);if(!scopes.has(k))scopes.set(k,[]);scopes.get(k).push(e);}
 let requests=0,requestScopes=0,completedOnly=0;
 for(const rows of scopes.values()){
  const starts=rows.filter(e=>e.kind==='model.started');
  const normalized=rows.filter(e=>e.kind==='message.started');
  const messages=normalized.length?normalized:rows.filter(e=>e.kind==='pi.message_start'&&e.data?.message?.role==='assistant');
  const usage=rows.filter(e=>e.kind==='usage');
  if(starts.length||messages.length){requests+=starts.length||messages.length;requestScopes++;}
  else completedOnly+=usage.length;
 }
 const calls=new Map(),results=new Map();let anonymous=0;
 for(const e of unique){if(!['tool.started','tool.completed'].includes(e.kind))continue;const id=e.data?.id;const k=id?`${key(e)}:${id}`:`anonymous:${anonymous++}`;(e.kind==='tool.started'?calls:results).set(k,e);}
 const toolErrors=[...results.values()].filter(e=>e.data?.is_error===true);
 const toolSuccess=[...results.values()].filter(e=>e.data?.is_error===false).length;
 const blocked=toolErrors.filter(e=>(e.data?.result?.content||[]).some(c=>c.type==='text'&&typeof c.text==='string'&&['工作台拒绝越界或受保护的路径','工作台尚未启用逐次审批 Shell'].some(prefix=>c.text.startsWith(prefix)))).length;
 const errors=unique.filter(e=>e.kind==='error').length;
 const turns=unique.filter(e=>e.kind==='pi.turn_start').length;
 const usage=summarizeUsage(unique);
 return {requests:requestScopes?requests:null,completedOnly,tools:calls.size,toolResults:results.size,toolSuccess,toolErrors:toolErrors.length,unknownResults:results.size-toolSuccess-toolErrors.length,pendingTools:[...calls.keys()].filter(k=>!results.has(k)).length,errors,turns:turns||null,blocked,denied:unique.filter(e=>e.kind==='approval.resolved'&&e.data?.decision==='denied').length,usage};
}
export function renderEvaluationMetrics(events,node){
 const m=evaluationMetrics(events),card=node('section','','memory-card');
 card.append(node('h4','效率、可靠性与安全'));
 const rate=m.toolSuccess+m.toolErrors?`${(100*m.toolSuccess/(m.toolSuccess+m.toolErrors)).toFixed(1)}%（${m.toolSuccess}/${m.toolSuccess+m.toolErrors}）`:'未知';
 for(const text of [
  `模型请求：${m.requests??'未知'} 次已观测调用 · ${m.completedOnly} 条仅有用量的完成记录（不并入开始次数）；厂商内部重试未知`,
  `工具调用：${m.tools} 次 · 已收结果 ${m.toolResults} · 尚无结果 ${m.pendingTools}`,
  `错误：工具失败 ${m.toolErrors} 次 · 系统错误事件 ${m.errors} 条（可能同源，不相加当独立故障）`,
  `工具成功率：${rate} · 状态未知结果 ${m.unknownResults} 条（未返回结果不计成功）`,
  `执行步数：${m.turns??'未知'} 个模型轮次；工具调用单独计数，不以长期计划阶段代替`,
  '重复任务成功率 / 成功值及耗时方差：样本不足 · 尚无同任务、模型、预算和评分协议的独立重复试验',
  `安全违规：未知（未接独立安全检测） · 已知策略拦截 ${m.blocked} 次 · 审批拒绝 ${m.denied} 次；拦截不等于违规已执行`
 ])card.append(node('p',text));
 return card;
}
export function renderTaskEvaluation(target,run,events,node,labels,renderUsageSummary){
 const e=taskEvaluation(run,events);
 const review=run.evaluation;
 target.replaceChildren(node('h3','任务评测结果'),node('p',`业务质量：${reviewLabel(review)} · ${review?'事后证据审阅':'尚无独立业务评分'}`,'muted'));
 if(review){
  const report=node('section','','memory-card');report.append(node('h4',`${review.reviewer} · ${review.reviewed_at}`),node('p',review.summary));
  if(review.stale)report.append(node('p','评测后任务已有新事件，以下是历史结论，需重新评测。'));
  for(const check of review.checks||[])report.append(node('p',`${({passed:'通过',partial:'部分',blocked:'待核验',failed:'未达标'}[check.status]||check.status)} · ${check.name}：${check.reason}`));
  report.append(node('p','范围与限制：'+review.limitations,'muted'));target.append(report);
 }
 const card=node('section','','memory-card');
 for(const text of [`运行状态：${labels[run.status]||run.status}`,`完成检查：${e.completed?'执行完成':'尚未完成'}`,`交付记录：${e.delivery?'已记录':'未观察到'}`,`人工验收：${e.confirmed?'已确认':'未记录'}`,`工具调用：${e.tools} 次 · 错误事件：${e.errors} 条`])card.append(node('p',text));
 if(run.reason)card.append(node('p','当前原因：'+run.reason));
 target.append(card,renderTaskEstimate(run,events,node),renderEvaluationMetrics(events,node),events.some(e=>e.kind==='usage')?renderUsageSummary(events,node,{showCost:false}):node('p','Token / 费用：未知 · 尚无用量记录'),node('p','基于当前已加载的任务事件，历史错误保留计数，不代表最终验收失败；无记录不代表已经通过。','muted'));
}
export function initEvaluation({api,selectTask,labels,showBoardView}){
 const $=id=>document.getElementById(id),page=$('evaluation-page');
 const node=(tag,text='',cls='')=>{const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;};
 const billing=initDeepseekBilling({api,node});
 let tasks=[],selected=null,version=0;
 const active=()=>document.body.dataset.view==='evaluation';
 const title=task=>task.prompt.split('\n')[0].slice(0,90);
 const num=n=>n==null?'未知':String(n);
 function reveal(id,focus=true){
  const task=tasks.find(t=>t.id===id);if(!task)return;selected=id;
  $('evaluation-detail').hidden=false;$('evaluation-detail-title').textContent=title(task);
  renderTaskEvaluation($('evaluation-detail-content'),task,task.evaluation_events,node,labels,renderUsageSummary);
  for(const b of $('evaluation-results').querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.task===id));
  if(focus){$('evaluation-detail-title').focus({preventScroll:true});$('evaluation-detail').scrollIntoView({block:'start',behavior:'instant'});}
 }
 async function load(){
  const token=++version;$('evaluation-error').textContent='';$('evaluation-summary').textContent='正在汇总所有任务…';$('evaluation-refresh').disabled=true;
  try{
   const data=await api('/api/evaluations');if(token!==version||!active())return;tasks=data.tasks;
   $('evaluation-summary').textContent=`${tasks.length} 个任务 · 已评测 ${tasks.filter(t=>t.evaluation&&!t.evaluation.stale).length} · 待重评 ${tasks.filter(t=>t.evaluation?.stale).length} · 未评测 ${tasks.filter(t=>!t.evaluation).length}`;
   const stats=tasks.map(t=>evaluationMetrics(t.evaluation_events)),sum=k=>stats.reduce((n,m)=>n+(m[k]||0),0);
   const estimates=tasks.map(t=>taskEstimate(t,t.evaluation_events)),total=totalEstimate(estimates);
   $('evaluation-totals').replaceChildren();
   for(const [label,value,hint] of [['已观测请求',num(stats.some(m=>m.requests!=null)?sum('requests'):null),'厂商内部重试未知'],['工具调用',sum('tools'),`失败 ${sum('toolErrors')} 次 · 系统错误 ${sum('errors')} 条`],['估算金额',total.text,`${total.unknown} 条用量未计价`],['已知策略拦截',sum('blocked'),'安全违规未独立检测']]){
    const card=node('article','','evaluation-stat');card.append(node('p',label),node('strong',value),node('small',hint));$('evaluation-totals').append(card);
   }
   const table=node('table'),head=node('thead'),tr=node('tr');
   for(const label of ['任务 / 状态','业务结论','请求 / 步数','工具 / 失败','工具成功率','估算金额','拦截','详情'])tr.append(node('th',label));head.append(tr);table.append(head);const body=node('tbody');
   tasks.forEach((task,i)=>{
    const m=stats[i],row=node('tr'),name=node('td');name.append(node('strong',title(task)),node('small',labels[task.status]||task.status));row.append(name);
    const denominator=m.toolSuccess+m.toolErrors;
    for(const value of [reviewLabel(task.evaluation),`${num(m.requests)} / ${num(m.turns)}`,`${m.tools} / ${m.toolErrors}`,denominator?`${(100*m.toolSuccess/denominator).toFixed(1)}% (${m.toolSuccess}/${denominator})`:'未知',estimateText(estimates[i]),m.blocked])row.append(node('td',value));
    const cell=node('td'),button=node('button','查看评测');button.type='button';button.dataset.task=task.id;button.onclick=()=>{history.replaceState(null,'',`#evaluations/${task.id}`);reveal(task.id);};cell.append(button);row.append(cell);body.append(row);
   });
   table.append(body);$('evaluation-results').replaceChildren(tasks.length?table:node('p','暂无任务；执行任务后可在此查看结果。'));
   if(tasks.length)reveal(selected&&tasks.some(t=>t.id===selected)?selected:tasks[0].id,false);else $('evaluation-detail').hidden=true;
  }catch(error){if(token===version){$('evaluation-summary').textContent='报告读取失败';$('evaluation-error').textContent=error.message;}}
  finally{if(token===version)$('evaluation-refresh').disabled=false;}
 }
 function leave(){version++;page.hidden=true;delete document.body.dataset.view;$('evaluation-open').classList.remove('active');$('evaluation-open').removeAttribute('aria-current');if(location.hash.startsWith('#evaluations'))history.replaceState(null,'',location.pathname+location.search);}
 function open(){const wanted=location.hash.split('/')[1];showBoardView();document.body.dataset.view='evaluation';page.hidden=false;$('evaluation-open').classList.add('active');$('evaluation-open').setAttribute('aria-current','page');selected=wanted||selected;$('board-home').classList.remove('active');$('board-home').removeAttribute('aria-current');history.replaceState(null,'',selected?`#evaluations/${selected}`:'#evaluations');$('evaluation-title').focus();load();billing.load();}
 document.addEventListener('workbench-view-change',leave);
 $('evaluation-open').onclick=open;$('evaluation-refresh').onclick=load;
 $('evaluation-task-open').onclick=()=>{if(selected)selectTask(selected).catch(e=>{$('evaluation-error').textContent=e.message;});};
 window.addEventListener('hashchange',()=>{if(location.hash.startsWith('#evaluations'))open();else if(active())showBoardView();});
 if(location.hash.startsWith('#evaluations'))open();
}
