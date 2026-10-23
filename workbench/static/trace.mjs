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
