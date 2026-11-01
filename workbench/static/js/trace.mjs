import {buildWaterfall} from './timeline.mjs';
// Project the observable conversation events; no synthetic model reasoning.
export function buildTrace(events=[], status='idle') {
 const rows=[], pending=new Map();let turn=0;
 for(const [index,e] of events.entries()){
  if(e.kind==='request.started')turn++;
  const agent=e.agent_id||'main',key=`${turn}:${agent}:${e.id}`;
  if(e.kind==='tool.completed'&&e.id&&pending.has(key)){
   const row=pending.get(key);row.status=e.is_error?'failed':'completed';row.end=e.created;row.result=e;pending.delete(key);continue;
  }
  const row={key:`${turn}:${index}`,agent,event:e,start:e.created,end:e.created,status:e.kind==='tool.started'?'running':e.is_error?'failed':'recorded'};
  rows.push(row);if(e.kind==='tool.started'&&e.id)pending.set(key,row);
  if(e.kind==='agent.completed')for(const call of pending.values())if(call.agent===agent&&call.status==='running')call.status='interrupted';
  if(e.kind==='request.started')for(const call of pending.values())if(call!==row&&call.status==='running')call.status='interrupted';
 }
 if(!['running','attention'].includes(status))for(const row of rows)if(row.status==='running')row.status='interrupted';
 return rows;
}

// A conversation is a set of executions, not a stopwatch from its creation.
function chatExecutions(events){
 const groups=[];let current=null;
 const terminal=new Set(['completed','failed','cancelled','interrupted','awaiting_confirmation']);
 const begin=e=>{current={key:`run-${e.created??groups.length}-${e.kind}`,events:[],hasRoot:false,provisional:e.kind==='request.started'||e.kind==='status'&&e.status==='running',ended:false,state:'unknown'};groups.push(current);};
 for(const event of events){
  const main=(event.agent_id||'main')==='main';
  const request=event.kind==='request.started';
  const root=event.kind==='agent.started'&&main;
  const restarting=event.kind==='status'&&main&&event.status==='running'&&(current?.ended||current?.agentFinishedAt!=null||current&&!current.hasRoot&&!current.provisional);
  if(!current||request||restarting||root&&(current.hasRoot||current.ended||!current.provisional))begin(event);
  if(request)current.key=`request-${event.created??groups.length}`;
  if(root){current.hasRoot=true;if(event.attempt_id)current.key=event.attempt_id;current.state='running';}
  current.events.push(event);
  if(request)current.state='running';
  if(main&&event.kind==='status')current.state=event.status;
  if(event.kind==='request.completed'||main&&event.kind==='status'&&terminal.has(event.status)){
   if(!current.ended)current.finishedAt=Number.isFinite(event.created)?event.created:null;
   current.ended=true;current.state=event.status||'completed';
  }
  if(main&&event.kind==='agent.completed'){
   current.agentFinishedAt=Number.isFinite(event.created)?event.created:null;
   current.state=event.status||'completed';
  }
 }
 return groups;
}

export function buildChatWaterfall(events=[],status='idle',_now=Date.now()/1000,attempt='all'){
 const groups=chatExecutions(events),attempts=groups.map(g=>g.key);
 const selected=attempt==='latest'||attempt!=='all'&&!attempts.includes(attempt)?attempts.at(-1):attempt;
 const selectedGroups=selected&&selected!=='all'?groups.filter(g=>g.key===selected):groups;
 const runs=selectedGroups.map(group=>{
  const wrapped=group.events.map((event,index)=>({id:`${event.created??index}:${event.kind}:${event.agent_id||'main'}:${event.id||''}`,created:Number.isFinite(event.created)?event.created:0,kind:event.kind,data:{...event,attempt_id:group.key,time_missing:!Number.isFinite(event.created)}}));
  const completions=new Set(wrapped.filter(e=>e.kind==='model.completed'||e.kind==='message.completed').map(e=>e.data.agent_id||'main'));
  const phaseStarts=new Set(wrapped.filter(e=>e.kind==='model.started').map(e=>e.data.agent_id||'main'));
  for(const e of wrapped)if(e.kind==='message.started'&&!completions.has(e.data.agent_id||'main')&&!phaseStarts.has(e.data.agent_id||'main'))e.kind='chat.message.started';
  for(const e of wrapped)if(e.kind==='session.connected'||e.kind==='file.changed')e.kind='chat.'+e.kind;
  const valid=wrapped.filter(e=>!e.data.time_missing),origin=valid.length?Math.min(...valid.map(e=>e.created)):0;
  const observedEnd=valid.length?Math.max(...valid.map(e=>e.created)):origin;
  // Even an open execution uses observed event times. Never charge browser idle time.
  const end=group.finishedAt??group.agentFinishedAt??observedEnd;
  const live=group===groups.at(-1)&&status==='running'&&!group.ended&&group.agentFinishedAt==null;
  const view=buildWaterfall(wrapped,{status:live?'running':'completed'},end);
  for(const row of view.rows){
   row.agent=row.agentId;row.key=row.id;row.timeMissing=row.event.data.time_missing;
   if(row.event.kind==='request.completed'){row.title='本轮请求结束';row.kind='check';row.status=row.event.data.status||'recorded';}
   if(row.event.kind==='chat.message.started')row.kind='model';
   if(row.timeMissing||row.events.some(e=>e.data.time_missing)||row.status==='running')row.duration=null;
   const usage=row.events.find(e=>e.kind==='model.completed'&&'total_tokens' in e.data)?.data;
   if(usage)row.usage=usage;
   if(usage?.stop_reason==='error')row.status='failed';
  }
  const hasStart=group.events.some(e=>Number.isFinite(e.created)&&(e.kind==='request.started'||e.kind==='agent.started'&&(e.agent_id||'main')==='main'||e.kind==='status'&&e.status==='running'));
  const closed=group.ended||group.agentFinishedAt!=null;
  const knownEnd=group.ended?group.finishedAt!=null:group.agentFinishedAt!=null;
  const elapsed=hasStart&&knownEnd?Math.max(0,end-origin):null;
  return {...view,origin,end,duration:Math.max(0,end-origin),elapsed,closed,live,number:groups.indexOf(group)+1,key:group.key,state:group.state};
 });
 return {runs,rows:runs.flatMap(r=>r.rows),events:runs.flatMap(r=>r.events),attempts,
  origin:runs[0]?.origin??0,end:runs.at(-1)?.end??0,duration:runs.reduce((n,r)=>n+r.duration,0)};
}
