// One group per agent and execution attempt; interleaving does not restart an agent.
// Matching tool results remain attached to the original call.
export function buildTimeline(events,agents=[],runStatus='running'){
 const entries=[],calls=new Map(),roles=new Map(agents.map(a=>[a.id,a.role])),ends=new Map();
 const ignored=new Set(['message.user','message.started','message.delta']);
 const identity=e=>`${e.data.agent_id||'main'}:${e.data.attempt_id||''}`;
 for(const event of events){
  const d=event.data;
  if(event.kind==='agent.started')roles.set(d.agent_id||'main',d.role);
  if(event.kind==='agent.completed')ends.set(identity(event),d.status);
  if(ignored.has(event.kind)||event.kind.startsWith('pi.')&&event.kind!=='pi.diagnostic')continue;
  const key=identity(event)+':'+d.id;
  if(['tool.completed','tool.output'].includes(event.kind)&&d.id&&calls.has(key)){
   const call=calls.get(key);
   if(event.kind==='tool.completed'){call.result=event;call.end=event.created;call.status=d.is_error?'failed':'completed';}
   else call.partial=event;
   continue;
  }
  const item={id:String(event.id),event,agentId:d.agent_id||'main',attempt:d.attempt_id||'',start:event.created,end:event.created,status:event.kind==='tool.started'?'running':d.is_error?'failed':'recorded',isCall:event.kind==='tool.started'};
  entries.push(item);if(item.isCall&&d.id)calls.set(key,item);
 }
 const groups=[],byAgent=new Map();
 for(const item of entries){
  if(item.status==='running'&&(ends.has(identity(item.event))||['completed','cancelled','failed','interrupted'].includes(runStatus)))item.status='interrupted';
  const groupKey=identity(item.event);
  let group=byAgent.get(groupKey);
  if(!group){group={id:item.id,agentId:item.agentId,attempt:item.attempt,role:item.agentId==='main'?'主 Agent':roles.get(item.agentId)||'子 Agent',start:item.start,end:item.end,items:[],calls:0};groups.push(group);byAgent.set(groupKey,group);}
  group.items.push(item);group.end=Math.max(group.end,item.end);if(item.isCall)group.calls++;
 }
 return groups;
}

// Project persisted events onto one wall-clock axis; never invent missing starts.
const terminal=new Set(['completed','failed','cancelled','interrupted','awaiting_confirmation']);
export function failurePhase(reason=''){
 if(reason.startsWith('计划仍有未完成步骤')||reason.startsWith('本次执行存在失败的子任务'))return '收尾校验失败';
 if(reason.includes('归档')||reason.startsWith('交付已产生，但保存工作区总结失败'))return '归档未完成';
 if(reason.startsWith('待核对并收尾'))return '待收尾';
 return '执行未完成';
}
export function withAttempts(events){
 const initial=events.find(e=>e.data?.attempt_id)?.data.attempt_id||'legacy';let attempt=initial;
 return events.map(e=>{const d=e.data||{};if(d.attempt_id&&(d.agent_id||'main')==='main')attempt=d.attempt_id;return {...e,data:{...d,attempt_id:d.attempt_id||attempt}};});
}
export function buildWaterfall(events,run={},now=Date.now()/1000){
 const rows=[],calls=new Map(),models=new Map(),lastModels=new Map(),waits=new Map(),contexts=new Map();
 const normalized=withAttempts(events),endpoints=new Map();
 const key=e=>`${e.data.attempt_id}:${e.data.agent_id||'main'}`;
 const add=(e,kind,title,start=null)=>{const row={id:String(e.id),event:e,events:[e],kind,title,start,end:e.created,agentId:e.data.agent_id||'main',attempt:e.data.attempt_id,status:'recorded'};rows.push(row);return row;};
 const complete=(row,e)=>{row.events.push(e);row.end=e.created;row.status=e.data.is_error||e.data.status==='failed'?'failed':'completed';};
 for(const e of normalized){
  const d=e.data,k=key(e),name=e.kind;
  if(name==='agent.completed')endpoints.set(k,e.created);
  if(name==='pi.turn_start'||name==='model.started'||name==='message.started'){
   // Keep the observed turn boundary until an assembly completion refines it.
   if(!models.has(k)){const row=add(e,'model',name==='message.started'?'模型回复接收':'模型响应阶段',e.created);row.status='running';row.timing=name==='message.started'?'仅回复接收区间，未记录请求发出时间':'模型阶段区间，包含调用前准备';models.set(k,row);}
   continue;
  }
  if(name==='memory.assembled'){
   // turn_start precedes the context hook; this boundary is not an HTTP send timestamp.
   const model=models.get(k);
   if(model&&e.created>=model.start){
    if(e.created>model.start&&!model.preparation){
     const prep=add({...model.event,id:`${model.id}:preparation`},'memory','调用前准备（含记忆组装）',model.start);
     prep.isPreparation=true;prep.end=e.created;prep.status='completed';prep.events.push(e);prep.timing='回合开始至记忆组装完成，包含准备工作；不是单独的记忆组装耗时';model.preparation=prep;
    }else if(model.preparation){model.preparation.end=e.created;model.preparation.events.push(e);}
    model.start=e.created;model.timing='记忆组装完成至模型回复结束；请求发出时刻未单独记录';
   }
  }
  if(name==='message.completed'||name==='model.completed'){
   const row=models.get(k);if(row){complete(row,e);models.delete(k);lastModels.set(k,row);}
   else if(d.text||name==='model.completed'||'total_tokens' in d){const prev=lastModels.get(k);if(!prev||name==='model.completed'||prev.events.at(-1)?.data.text!==d.text){lastModels.set(k,add(e,'model','模型回复'));}}
   continue;
  }
  if(name==='usage'){
   const model=lastModels.get(k);
   if(model&&!model.usage){model.usage=d;model.events.push(e);if(d.stop_reason==='error')model.status='failed';if(d.stop_reason==='aborted')model.status='interrupted';}
   else{const row=add(e,'model','模型用量与费用');row.usage=d;}
   continue;
  }
  if(name==='tool.started'){const row=add(e,'tool',d.name||'工具调用',e.created);row.status='running';calls.set(`${k}:${d.id}`,row);continue;}
  if(name==='tool.completed'||name==='tool.output'){
   const row=calls.get(`${k}:${d.id}`);
   if(row){if(name==='tool.completed'){complete(row,e);calls.delete(`${k}:${d.id}`);}else row.partial=e;}
   else if(name==='tool.completed'){const orphan=add(e,'tool',d.name||'工具结果');orphan.status=d.is_error?'failed':'completed';}
   continue;
  }
  if(name==='approval.requested'||name==='input.requested'){
   const row=add(e,'wait',name==='approval.requested'?'等待工具审批':'等待补充信息',e.created);row.status='running';waits.set(`${k}:${name.split('.')[0]}:${d.id}`,row);continue;
  }
  if(name==='approval.resolved'||name==='input.answered'){
   const wait=waits.get(`${k}:${name.split('.')[0]}:${d.id}`);if(wait){complete(wait,e);if(d.decision==='denied'||d.decision==='expired')wait.status='failed';waits.delete(`${k}:${name.split('.')[0]}:${d.id}`);}else add(e,'wait',name==='approval.resolved'?'审批结果':'补充信息已回答');continue;
  }
  if(name==='context.started'){const row=add(e,'context','上下文压缩',e.created);row.status='running';contexts.set(k,row);continue;}
  if(name==='context.completed'||name==='context.failed'){
   const row=contexts.get(k);if(row){complete(row,e);if(name==='context.failed')row.status='failed';contexts.delete(k);continue;}
  }
  if(name==='message.delta'||name==='message.user'||name==='agent.status'||name==='context.usage'||name==='session.connected'||name.startsWith('pi.')||name==='file.changed')continue;
  if(name==='error'||name==='archive.failed'||name==='task.needs_completion'){
   const reason=d.text||d.error||d.reason||'';const row=add(e,'check',name==='archive.failed'?'归档失败':failurePhase(reason));row.status='failed';row.reason=reason;continue;
  }
  if(name==='status'){
   if(!['failed','interrupted','cancelled'].includes(d.status))continue;
   const previous=rows.at(-1);if(previous?.reason&&previous.reason===d.reason){previous.events.push(e);continue;}
   const row=add(e,'check',d.status==='cancelled'?'任务已停止':failurePhase(d.reason));row.status=d.status;row.reason=d.reason;continue;
  }
  const titles={'memory.assembled':'调用前组装记忆','memory.updated':'记忆已更新','memory.offloaded':'工具原文已外置','context.started':'上下文压缩开始','context.completed':'上下文压缩完成','context.failed':'上下文压缩失败','agent.started':'开始执行','agent.completed':'Agent 执行结束','workflow.batch_started':'并行委派开始','workflow.batch_completed':'并行结果汇总','plan.updated':'执行计划已更新','task.reported':'提交完成报告','task.completed':'任务已完成','task.archived':'交付已归档','output.structured':'结构化交付','external.progress':d.step||'外部执行进度','executor.session':'外部会话已连接','executor.result':'外部工具交付'};
  const kind=name.startsWith('memory.')?'memory':name.startsWith('context.')?'context':name.startsWith('task.')||name==='output.structured'?'check':'event';
  const row=add(e,kind,titles[name]||name);if(name==='agent.completed'&&d.status)row.status=d.status;if(name==='context.failed'||name==='workflow.batch_completed'&&d.success===false)row.status='failed';
 }
 const lastTime=normalized.at(-1)?.created??now;
 for(const row of rows){
  if(row.status==='running'){
   const endpoint=endpoints.get(`${row.attempt}:${row.agentId}`);
   if(endpoint!=null||terminal.has(run.status)){row.end=endpoint??lastTime;row.status='interrupted';row.incomplete=true;}
   else row.end=Math.max(row.start,now);
  }
  row.duration=row.start==null?null:Math.max(0,row.end-row.start);
 }
 const origin=normalized.length?Math.min(...normalized.map(e=>e.created)):now;
 const end=Math.max(origin,...rows.map(r=>r.end),lastTime);
 rows.sort((a,b)=>Number(Boolean(a.event.data.time_missing))-Number(Boolean(b.event.data.time_missing))||(a.start??a.end)-(b.start??b.end)||(a.kind==='memory'&&b.kind==='model'?-1:a.kind==='model'&&b.kind==='memory'?1:0));
 return {rows,origin,end,duration:end-origin,events:normalized};
}
