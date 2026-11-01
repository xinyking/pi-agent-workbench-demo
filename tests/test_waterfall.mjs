import test from 'node:test';
import assert from 'node:assert/strict';
import {buildWaterfall,withAttempts,failurePhase} from '../workbench/static/js/timeline.mjs';
const e=(id,kind,data={},created=id)=>({id,created,kind,data:{agent_id:'main',attempt_id:'a',...data}});
test('parallel tools share an axis and pair by agent, attempt and call id',()=>{
 const v=buildWaterfall([e(1,'tool.started',{id:'same',name:'read'}),e(2,'tool.started',{id:'same',agent_id:'child',name:'read'}),e(4,'tool.completed',{id:'same',agent_id:'child'}),e(6,'tool.completed',{id:'same'})],{status:'completed'},10);
 assert.equal(v.rows.length,2);assert.deepEqual(v.rows.map(r=>r.duration),[5,2]);assert.equal(v.duration,5);
 assert.ok(v.rows.reduce((n,r)=>n+r.duration,0)>v.duration);
});
test('model phase ends before tools; usage attaches once and no hidden reasoning is inferred',()=>{
 const v=buildWaterfall([e(1,'pi.turn_start'),e(2,'memory.assembled'),e(3,'message.started'),e(5,'message.completed',{text:'answer'}),e(6,'usage',{cost_usd:.02}),e(7,'tool.started',{id:'read',name:'read'}),e(9,'tool.completed',{id:'read'})],{status:'completed'});
 const model=v.rows.find(r=>r.kind==='model');assert.equal(model.start,2);assert.equal(model.duration,3);const prep=v.rows.find(r=>r.title==='调用前准备（含记忆组装）');assert.equal(prep.start,1);assert.equal(prep.end,2);assert.equal(prep.duration,1);assert.equal(prep.usage,undefined);assert.ok(v.rows.findIndex(r=>r.event.kind==='memory.assembled')<v.rows.indexOf(model));assert.equal(model.end,5);assert.equal(model.usage.cost_usd,.02);assert.equal(v.rows.filter(r=>r.kind==='model').length,1);
});
test('completed agent plus pending plan yields a separate failure step',()=>{
 const reason='计划仍有未完成步骤，请继续处理';
 const v=buildWaterfall([e(1,'agent.completed',{status:'completed'}),e(2,'error',{text:reason}),e(3,'status',{status:'failed',reason})],{status:'failed'});
 assert.equal(v.rows[0].status,'completed');assert.equal(v.rows.length,2);assert.equal(v.rows[1].title,'收尾校验失败');assert.equal(v.rows[1].duration,null);
 assert.equal(failurePhase(reason),'收尾校验失败');
});
test('missing starts and missing ends stay explicit; late results never complete a prior attempt',()=>{
 const v=buildWaterfall([e(1,'tool.started',{id:'r',name:'read'}),e(2,'agent.completed',{status:'failed'}),e(3,'tool.completed',{id:'r',attempt_id:'b'})],{status:'failed'});
 assert.equal(v.rows[0].status,'interrupted');assert.equal(v.rows[0].incomplete,true);assert.equal(v.rows.at(-1).duration,null);
});
test('approval and input waits pair by id and overlapping requests remain distinct',()=>{
 const v=buildWaterfall([e(1,'approval.requested',{id:'x'}),e(2,'input.requested',{id:'x'}),e(5,'approval.resolved',{id:'x',decision:'approved'}),e(8,'input.answered',{id:'x'})],{status:'completed'});
 assert.deepEqual(v.rows.map(r=>r.duration),[4,6]);
});
test('unscoped terminal events join the last main attempt, not a child identity',()=>{
 const rows=withAttempts([e(1,'agent.started'),e(2,'agent.started',{attempt_id:'b'}),e(3,'memory.updated',{agent_id:'child',attempt_id:'b'}),{id:4,created:4,kind:'status',data:{status:'failed'}}]);
 assert.equal(rows.at(-1).data.attempt_id,'b');
});
test('agent start markers remain instantaneous when the agent later completes',()=>{
 const v=buildWaterfall([e(1,'agent.started',{status:'running'}),e(3,'agent.completed',{status:'completed'})],{status:'completed'});
 assert.equal(v.rows[0].status,'recorded');assert.equal(v.rows[0].duration,null);assert.equal(v.rows[0].end,1);
});
test('compaction pairs its own timestamps and preserves failed results',()=>{
 const v=buildWaterfall([e(1,'context.started'),e(4,'context.failed',{error:'test'})],{status:'completed'});
 assert.equal(v.rows.length,1);assert.equal(v.rows[0].duration,3);assert.equal(v.rows[0].status,'failed');
});

test('assembly boundaries affect only matching agents and rows advance by start',()=>{
 const v=buildWaterfall([e(1,'pi.turn_start'),e(2,'pi.turn_start',{agent_id:'child'}),e(3,'memory.assembled',{agent_id:'child'}),e(4,'memory.assembled'),e(5,'message.completed',{text:'main'}),e(6,'message.completed',{agent_id:'child',text:'child'})],{status:'completed'});
 assert.equal(v.rows.find(r=>r.kind==='model'&&r.agentId==='main').start,4);
 assert.equal(v.rows.find(r=>r.kind==='model'&&r.agentId==='child').start,3);
 const starts=v.rows.map(r=>r.start??r.end);assert.deepEqual(starts,[...starts].sort((a,b)=>a-b));
});
