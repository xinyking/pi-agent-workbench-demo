import test from 'node:test';
import assert from 'node:assert/strict';
import { buildFlow } from '../workbench/static/flow.mjs';
const run={prompt:'找工作',status:'waiting_input'};
const root={id:'main',role:'主 Agent',task:'分析目标',status:'running',data:{attempt_id:'a'}};
const agents=[root,{id:'one',role:'task-scout',task:'调查',status:'waiting_input',parent_id:'main',data:{attempt_id:'a'}},{id:'two',role:'task-reviewer',task:'审查',status:'completed',parent_id:'main',data:{attempt_id:'a'}}];
const batch={kind:'workflow.batch_started',data:{attempt_id:'a',batch_id:'batch1',tasks:agents.slice(1).map(a=>({agent_id:a.id,agent:a.role,task:a.task}))}};
test('parallel nodes share a stage; pending input blocks completion',()=>{
 const flow=buildFlow(run,agents,[{id:'q',agent_id:'one',status:'pending',data:{question:'请提供简历',attempt_id:'a'}}],[batch]);
 assert.equal(flow.rows.find(r=>r.some(n=>n.id==='agent-one')).length,2);
 assert.ok(flow.edges.some(([a,b])=>a==='agent-one'&&b==='input-q'));
 assert.equal(flow.nodes.find(n=>n.id==='finish').status,'queued');
 assert.equal(flow.nodes.find(n=>n.id==='input-q').status,'waiting_input');
});
test('sequential delegation batches have a merge between them',()=>{
 const next={kind:'workflow.batch_started',data:{attempt_id:'a',batch_id:'batch2',tasks:[{agent_id:'three',agent:'task-worker',task:'实现'}]}};
 const flow=buildFlow(run,agents,[],[batch,next]);
 assert.ok(flow.edges.some(([a,b])=>a==='merge-0'&&b==='agent-three'));
 assert.ok(!flow.edges.some(([a,b])=>a==='main'&&b==='agent-three'));
});
test('old attempts are excluded; unstarted task has no invented execution',()=>{
 const flow=buildFlow({prompt:'test',status:'queued'},[],[],[]);
 assert.equal(flow.nodes.find(n=>n.id==='main').status,'queued');
 const old={kind:'workflow.batch_started',data:{...batch.data,attempt_id:'old'}};
 const graph=buildFlow(run,[root],[],[old]);
 assert.equal(graph.nodes.filter(n=>n.id.startsWith('agent-')).length,0);
});
test('Mermaid labels cannot introduce nodes or markup',()=>{
 const flow=buildFlow({prompt:'test',status:'completed'},[{...root,task:'<script>" ] --> bad[ x\n'}],[],[]);
 assert.ok(!flow.mermaid.includes('<script>'));
 assert.equal(flow.nodes.find(n=>n.id==='finish').status,'completed');
 assert.ok(flow.mermaid.startsWith('flowchart LR'));
});
test('a root question after delegation stays after that batch',()=>{
 const question={id:'late',agent_id:'main',status:'pending',created:20,data:{question:'补充目标城市',attempt_id:'a'}};
 const graph=buildFlow(run,agents,[question],[{...batch,id:10,created:10}]);
 assert.ok(graph.rows.findIndex(r=>r.some(n=>n.id==='input-late'))>graph.rows.findIndex(r=>r.some(n=>n.id==='agent-one')));
});
test('stage evidence is scoped by attempt and event boundaries',async()=>{
 const {eventsForNode}=await import('../workbench/static/flow.mjs');
 const scope={agentId:'main',attemptId:'a',fromId:10,toId:20};
 const events=[{id:11,data:{agent_id:'main',attempt_id:'a'}},{id:21,data:{agent_id:'main',attempt_id:'a'}},{id:12,data:{agent_id:'main',attempt_id:'old'}},{id:13,data:{agent_id:'one',attempt_id:'a'}}];
 assert.deepEqual(eventsForNode(scope,events).map(e=>e.id),[11]);
});

test('external task flow contains only observed reports and does not invent a Pi agent',()=>{
 const graph=buildFlow({provider_id:'external',prompt:'外部工作',status:'completed'},[],[],[
  {id:1,kind:'external.progress',data:{agent_id:'main',step:'读取结果',text:'实际材料'}},
  {id:2,kind:'external.progress',data:{agent_id:'main',step:'交付',text:'实际结果'}}
 ]);
 assert.deepEqual(graph.nodes.map(n=>n.title),['外部任务导入','读取结果','交付']);
 assert.equal(graph.nodes[1].status,'recorded');assert.equal(graph.nodes[2].status,'completed');
 assert.equal(graph.nodes[1].toId,2);
});
