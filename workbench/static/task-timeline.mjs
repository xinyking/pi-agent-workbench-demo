import {buildTimeline} from './timeline.mjs';

export function createTimelineRenderer({node,labels,kinds,renderOutput}){
function eventTitle(event){const d=event.data;return event.kind==='status'?(labels[d.status]||d.status):({'external.progress':'外部步骤进度','message.completed':'模型回复','agent.started':'开始执行','agent.completed':'Agent 结束','agent.status':'Agent 状态','workflow.batch_started':'并行子任务开始','workflow.batch_completed':'子任务汇总','input.requested':'请求补充信息','input.answered':'用户已补充信息','task.archived':'已保存总结与文件归档','archive.failed':'保存归档失败','task.needs_completion':'待核对并收尾','task.reported':'提交完成报告','task.completed':'任务已完成','executor.session':'外部会话已连接','executor.result':'外部交付（工具自报）'}[event.kind]||kinds[event.kind]||event.kind);}
function appendEventBody(target,event){
 const d=event.data;
 if(d.is_error)target.append(node('p','工具执行失败','system-message'));
 if(event.kind==='output.structured')target.append(renderOutput(d.output));
 for(const key of ['question','answer','text','summary','error','task','reason','summary_path','directory'])if(d[key])target.append(node('p',String(d[key])));
 if(d.arguments)target.append(node('h4','调用参数'),node('pre',Object.entries(d.arguments).map(([k,v])=>`${k}: ${typeof v==='string'?v:JSON.stringify(v)}`).join('\n')));
 if(d.result){const content=d.result.content?.filter(p=>p.type==='text').map(p=>p.text).join('\n');target.append(node('h4','工具结果'),node('pre',content||JSON.stringify(d.result,null,2)));}
 if(d.diff)target.append(node('pre',d.diff,'diff'));
 const raw=node('details','','raw-event');raw.append(node('summary','原始事件'),node('pre',JSON.stringify(d,null,2)));target.append(raw);
}
function renderTimeline(target,events,detailAgents,currentRun){
 const nearBottom=target.scrollHeight-target.scrollTop-target.clientHeight<70,scroll=target.scrollTop;
 const open=new Map([...target.querySelectorAll('details[data-timeline-key]')].map(n=>[n.dataset.timelineKey,n.open]));
 const focused=document.activeElement?.parentElement?.dataset?.timelineKey;
 if(currentRun?.provider_id==='external')events=events.filter(e=>!['message.completed','plan.updated'].includes(e.kind)).map(e=>({...e,data:{...e.data,agent_id:'main',attempt_id:'external'}}));
 const managed=['codex','claude'].includes(currentRun?.provider_id);
 if(managed){let attempt=events.find(e=>e.kind==='agent.started')?.data.attempt_id||'external';events=events.map(e=>{if(e.data.attempt_id)attempt=e.data.attempt_id;return {...e,data:{...e.data,agent_id:'main',attempt_id:e.data.attempt_id||attempt}};});}
 const groups=buildTimeline(events,detailAgents,currentRun?.status),fragment=document.createDocumentFragment();
 const time=value=>new Date(value*1000).toLocaleTimeString('zh-CN',{hour12:false});
 for(const [index,group] of groups.entries()){
  const block=node('details','','timeline-agent');block.dataset.timelineKey='agent-'+group.id;block.open=open.get(block.dataset.timelineKey)??false;
  const summary=node('summary');summary.append(node('strong',currentRun?.provider_id==='external'?currentRun.model:managed?(currentRun.provider_id==='codex'?'Codex CLI':'Claude Code'):group.role),node('span',currentRun?.provider_id==='external'?`${group.items.filter(i=>i.event.kind==='external.progress').length} 次进度 · ${group.calls} 条工具结果`:`${group.items.filter(i=>i.event.kind==='message.completed').length} 轮模型回复 · ${group.calls} 次工具调用`,'timeline-count'),node('time',time(group.start)+' – '+time(group.end)));block.append(summary);
  const body=node('div','','timeline-agent-body');let number=0,round=0;
  for(const item of group.items){const detail=node('details','','event timeline-entry');detail.dataset.timelineKey='entry-'+item.id;detail.open=open.get(detail.dataset.timelineKey)??false;
   const title=item.isCall?`工具 ${++number} · ${item.event.data.name}${Object.values(item.event.data.arguments||{}).find(v=>typeof v==='string')?' · '+Object.values(item.event.data.arguments).find(v=>typeof v==='string').replace(/\s+/g,' ').slice(0,70):''}`:item.event.kind==='message.completed'?`模型第 ${++round} 轮 · ${item.event.data.text?item.event.data.text.replace(/\s+/g,' ').slice(0,90):'未输出文字，查看后续工具调用或结束状态'}`:(item.event.kind==='external.progress'?'步骤 · '+item.event.data.step:eventTitle(item.event))+(item.event.data.name?' · '+item.event.data.name:'');
   const head=node('summary');head.append(node('time',time(item.start)),node('span',title));
   if(item.isCall){const state={running:'调用中',completed:item.event.data.source?'结果已回传':`完成 · ${Math.max(0,item.end-item.start).toFixed(1)}s`,failed:'失败',interrupted:'未记录结束结果'}[item.status];head.append(node('small',state,'timeline-call-state '+item.status));}
   detail.append(head);appendEventBody(detail,item.event);if(item.result)appendEventBody(detail,item.result);else if(item.partial)appendEventBody(detail,item.partial);body.append(detail);
  }
  block.append(body);fragment.append(block);
 }
 if(!groups.length)fragment.append(node('p','暂无执行记录，开始任务后会按时间显示。','muted'));
 target.replaceChildren(fragment);target.scrollTop=nearBottom?target.scrollHeight:scroll;
 if(focused)[...target.querySelectorAll('details[data-timeline-key]')].find(n=>n.dataset.timelineKey===focused)?.querySelector('summary')?.focus({preventScroll:true});
}

return renderTimeline;
}
