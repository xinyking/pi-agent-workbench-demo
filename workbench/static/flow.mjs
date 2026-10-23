// Graph layout follows observed batches/questions; it never invents a completed stage.
export function eventsForNode(node, events) {
  if (!node || node.kind === 'start') return [];
  return events.filter(e => (e.data.agent_id || 'main') === node.agentId &&
    (!node.attemptId || e.data.attempt_id === node.attemptId) &&
    (node.fromId == null || e.id >= node.fromId) &&
    (node.toId == null || e.id < node.toId));
}
export function buildFlow(run, agents = [], inputs = [], events = []) {
  if(run.provider_id==='external'){
    const reports=events.filter(e=>e.kind==='external.progress');
    const nodes=[{id:'start',kind:'start',title:'外部任务导入',text:run.prompt,status:'recorded',agentId:'main'},...reports.map((e,i)=>({id:'external-'+e.id,title:e.data.step,text:e.data.text,status:i===reports.length-1?run.status:'recorded',agentId:'main',fromId:e.id,toId:reports[i+1]?.id}))];
    const edges=nodes.slice(1).map((n,i)=>[nodes[i].id,n.id]);
    const mermaid=['flowchart LR',...nodes.map((n,i)=>`  n${i}["${n.title.replace(/["<>\[\]{}|`\\\r\n]/g,' ').slice(0,100)}"]`),...nodes.slice(1).map((_,i)=>`  n${i} --> n${i+1}`)].join('\n');
    return {nodes,edges,rows:nodes.map(n=>[n]),mermaid};
  }
  const root = agents.find(a => a.id === 'main');
  const attemptId = root?.data?.attempt_id;
  const current = events.filter(e => !attemptId || e.data.attempt_id === attemptId);
  const nodes = [], edges = [], rows = [];
  const add = (items, previous = []) => {
    items.forEach(item => {item.attemptId = attemptId;});
    rows.push(items); nodes.push(...items);
    for (const from of previous) for (const to of items) edges.push([from.id, to.id]);
    return items;
  };
  let tail = add([{id:'start', kind:'start', title:'任务创建', text:run.prompt, status:'completed', agentId:'main'}]);
  if (!root) {
    tail = add([{id:'main', title:'主 Agent · 等待启动', text:'启动后展示实际执行路径', status:'queued', agentId:'main'}], tail);
  } else {
    const batches = current.filter(e => e.kind === 'workflow.batch_started');
    const requests = inputs.filter(r => !attemptId || r.data.attempt_id === attemptId);
    const requestEvent = r => events.find(e => e.kind === 'input.requested' && e.data.id === r.id);
    const batchItems = batches.map(e => ({kind:'batch',order:e.created||e.id||0,event:e,id:e.data.batch_id,tasks:e.data.tasks}));
    if (!batches.length) {
      const children = agents.filter(a=>a.parent_id&&(!attemptId||a.data.attempt_id===attemptId));
      if(children.length)batchItems.push({kind:'batch',order:0,id:'legacy',tasks:children.map(a=>({agent_id:a.id,agent:a.role,task:a.task}))});
    }
    const milestones = [...batchItems,...requests.filter(r=>r.agent_id==='main').map(r=>({kind:'question',request:r,event:requestEvent(r),order:r.created||requestEvent(r)?.created||requestEvent(r)?.id||0}))].sort((a,b)=>a.order-b.order);
    tail = add([{id:'main', title:'主 Agent · 分析与调度', text:root.task, status:milestones.length?'completed':root.status, agentId:'main',toId:milestones[0]?.event?.id}], tail);
    const addQuestion = (request, previous) => {
      const parent = previous.find(n => n.agentId === request.agent_id);
      const answered = events.find(e=>e.kind==='input.answered'&&e.data.id===request.id);
      const question = [{id:'input-'+request.id, title:request.status==='pending'?'等待补充信息':'补充信息 · '+(request.status==='answered'?'已回答':'已取消'), text:request.data.question, status:request.status==='pending'?'waiting_input':request.status==='answered'?'completed':'cancelled', agentId:request.agent_id, requestId:request.id, fromId:requestEvent(request)?.id,toId:answered?answered.id+1:undefined}];
      add(question, parent ? [parent] : previous);
      return [...previous.filter(n => n !== parent), ...question];
    };
    milestones.forEach((milestone,i)=>{
      if(milestone.kind==='question'){
        tail=addQuestion(milestone.request,tail);
        const answered=events.find(e=>e.kind==='input.answered'&&e.data.id===milestone.request.id);
        if(milestone.request.status==='answered')tail=add([{id:'resume-'+milestone.request.id,title:'主 Agent · 继续执行',text:'使用补充信息继续原任务',status:i<milestones.length-1?'completed':root.status,agentId:'main',fromId:answered?.id,toId:milestones[i+1]?.event?.id}],tail);
        return;
      }
      const group=milestone;
      tail=add(group.tasks.map(task=>{
        const agent=agents.find(a=>a.id===task.agent_id);
        return {id:'agent-'+task.agent_id,title:task.agent,text:task.task,status:agent?.status||'queued',agentId:task.agent_id,batchId:group.id};
      }),tail);
      for(const request of requests.filter(r=>group.tasks.some(t=>t.agent_id===r.agent_id)))tail=addQuestion(request,tail);
      const completed=current.find(e=>e.kind==='workflow.batch_completed'&&e.data.batch_id===group.id);
      const settled=Boolean(completed)||group.tasks.every(t=>agents.find(a=>a.id===t.agent_id)?.status==='completed');
      tail=add([{id:'merge-'+i,title:'主 Agent · 汇总'+(batchItems.length>1?` ${i+1}`:''),text:settled?'收集子任务结果，决定下一步':'等待当前阶段返回结果',status:i<milestones.length-1?'completed':settled?root.status:'queued',agentId:'main',batchId:group.id,fromId:completed?.id??Number.MAX_SAFE_INTEGER,toId:milestones[i+1]?.event?.id}],tail);
    });
  }
  const report=current.find(e=>e.kind==='task.reported');
  add([{id:'finish',title:run.status==='completed'?'任务已完成':'任务交付',text:run.status==='completed'?'主 Agent 已明确报告完成':'尚未报告完成',status:run.status==='completed'?'completed':run.status==='cancelled'?'cancelled':'queued',agentId:'main',fromId:report?.id??Number.MAX_SAFE_INTEGER}],tail);
  const quote=value=>String(value).replace(/["\[\]{}<>|`\\\r\n]/g,' ').slice(0,100);
  const ids=new Map(nodes.map((n,i)=>[n.id,'n'+i]));
  const mermaid=['flowchart LR',...nodes.map(n=>`  ${ids.get(n.id)}["${quote(n.title)} · ${quote(n.status)}"]`),...edges.map(([a,b])=>`  ${ids.get(a)} --> ${ids.get(b)}`)].join('\n');
  return {nodes,edges,rows,mermaid};
}
