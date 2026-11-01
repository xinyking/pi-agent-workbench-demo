import {renderUsageSummary,usageText} from './usage-view.mjs';
import {buildWaterfall,withAttempts} from './timeline.mjs';

export function createTimelineRenderer({node,labels,kinds,renderOutput,onAssembly}){
function eventTitle(event){const d=event.data;return event.kind==='status'?(labels[d.status]||d.status):({'usage':'模型用量与费用','memory.assembled':'调用前组装记忆','memory.updated':'记忆已更新','memory.offloaded':'工具原文已外置','external.progress':'外部步骤进度','message.completed':'模型回复','agent.started':'开始执行','agent.completed':'Agent 结束','agent.status':'Agent 状态','workflow.batch_started':'并行子任务开始','workflow.batch_completed':'子任务汇总','input.requested':'请求补充信息','input.answered':'用户已补充信息','task.archived':'已保存总结与文件归档','archive.failed':'保存归档失败','task.needs_completion':'待核对并收尾','task.reported':'提交完成报告','task.completed':'任务已完成','executor.session':'外部会话已连接','executor.result':'外部交付（工具自报）'}[event.kind]||kinds[event.kind]||event.kind);}
function appendEventBody(target,event){
 const d=event.data;
 if(event.kind==='usage'){target.append(node('p',`${d.provider||'Provider 未记录'} / ${d.model||'模型未记录'}`),node('p',usageText(d)));if(d.assembly_id)target.append(node('p','关联组装：'+d.assembly_id,'muted'));}
 if(event.kind==='memory.assembled'){target.append(node('p',`记忆版本 ${d.revision} · ${d.bytes} / ${d.budget_bytes} 字节 · 遗漏 ${d.omitted_optional_items??0} 项`),node('p',`用户记忆 ${d.counts?.user_memory??'未知'} · Agent 笔记 ${d.counts?.agent_notes??'未知'} · 长期记忆 ${d.counts?.long_term_memory??'未知'}`));if(d.history_saved===false)target.append(node('p','快照记录已关闭；仅保留组装元数据。','muted'));}
 if(d.is_error)target.append(node('p','工具执行失败','system-message'));
 if(event.kind==='output.structured')target.append(renderOutput(d.output));
 for(const key of ['question','answer','text','summary','error','task','reason','summary_path','directory'])if(d[key])target.append(node('p',String(d[key])));
 if(d.arguments)target.append(node('h4','调用参数'),node('pre',Object.entries(d.arguments).map(([k,v])=>`${k}: ${typeof v==='string'?v:JSON.stringify(v)}`).join('\n')));
 if(d.result){const content=d.result.content?.filter(p=>p.type==='text').map(p=>p.text).join('\n');target.append(node('h4','工具结果'),node('pre',content||JSON.stringify(d.result,null,2)));}
 if(d.diff)target.append(node('pre',d.diff,'diff'));
 const raw=node('details','','raw-event');raw.append(node('summary','原始事件'),node('pre',JSON.stringify(d,null,2)));target.append(raw);
}
let taskId=null,attemptChoice='latest';
function renderTimeline(target,events,detailAgents,currentRun){
 if(taskId!==currentRun?.id){taskId=currentRun?.id;attemptChoice='latest';}
 const open=new Map([...target.querySelectorAll('details[data-timeline-key]')].map(n=>[n.dataset.timelineKey,n.open]));
 const focused=document.activeElement?.closest('[data-timeline-key]')?.dataset.timelineKey;
 const scroll=target.scrollTop,refocusSelect=target.contains(document.activeElement)&&document.activeElement?.tagName==='SELECT';
 let normalized=withAttempts(events);
 const attempts=[...new Set(normalized.map(e=>e.data.attempt_id))];
 if(attemptChoice!=='latest'&&attemptChoice!=='all'&&!attempts.includes(attemptChoice))attemptChoice='latest';
 const chosen=attemptChoice==='latest'?attempts.at(-1):attemptChoice;
 if(chosen&&chosen!=='all')normalized=normalized.filter(e=>e.data.attempt_id===chosen);
 const view=buildWaterfall(normalized,currentRun),fragment=document.createDocumentFragment();
 const toolbar=node('div','','waterfall-toolbar');
 const label=node('label','执行轮次 '),select=node('select');select.setAttribute('aria-label','执行轮次');
 for(const [value,title] of [['latest','最新一次'],['all','全部历史'],...attempts.map((a,i)=>[a,`第 ${i+1} 次 · ${a==='legacy'?'历史未关联':a.slice(0,8)}`])]){const option=node('option',title);option.value=value;select.append(option);}select.value=attemptChoice;
 select.onchange=()=>{attemptChoice=select.value;renderTimeline(target,events,detailAgents,currentRun);};label.append(select);toolbar.append(label,node('small','点击步骤查看输入、输出与来源；时间轴显示实际事件区间。','muted'));const usage=renderUsageSummary(normalized,node),note=usage.querySelector('small'),scope=node('details','','usage-scope');scope.append(node('summary','统计口径'),note);usage.append(scope);fragment.append(toolbar,usage);
 const fmt=n=>`${n.toFixed(2)}s`;
 const clock=n=>fmt(Math.max(0,n-view.origin));
 const header=node('div','','waterfall-grid waterfall-axis');header.append(node('span','类型'),node('span','执行过程 / Agent'),node('span',`0s → ${fmt(view.duration)}`),node('span','开始于'),node('span','耗时'),node('span','费用'));fragment.append(header);
 const roles=new Map(detailAgents.map(a=>[a.id,a.role]));
 const labelsByKind={model:'模型',tool:'工具',wait:'等待',memory:'记忆',context:'压缩',check:'校验',event:'事件'};
 for(const row of view.rows){
  const detail=node('details','','event waterfall-entry '+row.kind);detail.dataset.timelineKey='step-'+row.id;detail.dataset.status=row.status;detail.dataset.kind=row.kind;detail.dataset.eventId=row.id;
  detail.open=open.get(detail.dataset.timelineKey)??row.status==='failed';
  const head=node('summary','','waterfall-grid');head.append(node('span',labelsByKind[row.kind],'waterfall-kind'));
  const name=node('span','','waterfall-name');name.append(node('strong',row.title),node('small',`${row.agentId==='main'?'主 Agent':roles.get(row.agentId)||row.agentId.slice(0,10)} · ${{recorded:'已记录',running:'进行中',completed:'已结束',failed:'失败',interrupted:row.incomplete?'未记录完整结束':'已中止 / 需处理',cancelled:'已停止'}[row.status]||labels[row.status]||row.status}`,'muted'));head.append(name);
  const track=node('span','','waterfall-track');track.setAttribute('aria-hidden','true');const mark=node('span','','waterfall-mark');
  const start=row.start??row.end,width=view.duration?100*(row.duration??0)/view.duration:0;mark.style.left=`${Math.max(0,Math.min(99.5,view.duration?100*(start-view.origin)/view.duration:0))}%`;mark.style.width=`${Math.max(.5,width)}%`;track.append(mark);head.append(track,node('time',clock(start),'waterfall-clock'),node('span',row.duration==null?'—':fmt(row.duration)+(row.incomplete?' *':''),'waterfall-duration'),node('span',row.usage?typeof row.usage.cost_usd==='number'?`$${row.usage.cost_usd.toFixed(4)}`:'未知':'—','waterfall-cost'));
  detail.append(head);
  const body=node('div','','waterfall-body');body.append(node('p',row.start==null?'未记录配对的开始时间，仅在时间轴标记事件位置。':`${clock(row.start)} → ${clock(row.end)} · ${row.timing||'已记录的起止事件区间'}${row.incomplete?'；缺少正常结束事件，按 Agent/任务结束边界截断。':''}`,'muted'));
  for(const e of row.events)appendEventBody(body,e);
  if(row.usage&&!row.events.some(e=>e.kind==='usage'))body.append(node('p',usageText(row.usage)));
  if(row.event.data.assembly_id){const jump=node('button','查看注入快照');jump.type='button';jump.onclick=()=>onAssembly?.(row.event.data.assembly_id);body.append(jump);}
  detail.append(body);fragment.append(detail);
 }
 if(!view.rows.length)fragment.append(node('p','暂无执行步骤，开始任务后显示。','muted'));
 fragment.append(node('p','耗时为事件起止区间；模型阶段从已记录的记忆组装完成边界起计；缺少该边界时可能包含调用前准备。等待审批单列；并行步骤会重叠，不能将各行耗时直接相加。— 表示缺少计时或计价证据。','waterfall-footnote muted'));
 target.replaceChildren(fragment);target.scrollTop=scroll;if(refocusSelect)select.focus({preventScroll:true});
 if(focused)[...target.querySelectorAll('[data-timeline-key]')].find(n=>n.dataset.timelineKey===focused)?.querySelector('summary')?.focus({preventScroll:true});
}

renderTimeline.reset=()=>{attemptChoice='latest';};
return renderTimeline;
}
