// One group per agent and execution attempt; interleaving does not restart an agent.
// Matching tool results remain attached to the original call.
export function buildTimeline(events,agents=[],runStatus='running'){
 const entries=[],calls=new Map(),roles=new Map(agents.map(a=>[a.id,a.role])),ends=new Map();
 const ignored=new Set(['message.user','message.started','message.delta','usage']);
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
