import test from 'node:test';
import assert from 'node:assert/strict';
import {buildTrace,buildChatWaterfall} from '../workbench/static/js/trace.mjs';
test('interleaved agent calls match results by agent and invocation',()=>{
 const rows=buildTrace([{kind:'tool.started',agent_id:'a',id:'same',created:1},{kind:'tool.started',agent_id:'b',id:'same',created:2},{kind:'tool.completed',agent_id:'a',id:'same',created:4},{kind:'tool.completed',agent_id:'b',id:'same',created:6,is_error:true}],'running');
 assert.equal(rows.length,2);assert.equal(rows[0].status,'completed');assert.equal(rows[0].end,4);assert.equal(rows[1].status,'failed');
});
test('new turns and terminated runs never leave old calls running',()=>{
 const events=[{kind:'tool.started',id:'x'},{kind:'request.started'},{kind:'tool.started',id:'x'},{kind:'tool.completed',id:'x'}];
 const rows=buildTrace(events,'running');assert.equal(rows[0].status,'interrupted');assert.equal(rows[2].status,'completed');
 assert.equal(buildTrace([{kind:'tool.started',id:'x'}],'failed')[0].status,'interrupted');
});
test('truncated history retains unmatched result without inventing a call',()=>{
 const rows=buildTrace([{kind:'tool.completed',id:'lost',is_error:true}]);assert.equal(rows.length,1);assert.equal(rows[0].event.kind,'tool.completed');assert.equal(rows[0].status,'failed');
});
test('chat model spans consume only observed starts and ends with numeric usage',()=>{
 const v=buildChatWaterfall([{kind:'request.started',created:1},{kind:'model.started',created:2},{kind:'message.started',created:3},{kind:'model.completed',created:6,total_tokens:12,cost_usd:.02}], 'idle');
 const model=v.rows.find(r=>r.kind==='model');assert.equal(model.duration,4);assert.equal(model.usage.cost_usd,.02);assert.equal(v.rows.filter(r=>r.kind==='model').length,1);
});
test('legacy response markers and missing timestamps never fabricate durations',()=>{
 const v=buildChatWaterfall([{kind:'message.started',created:3},{kind:'answer.started',created:4},{kind:'tool.completed',id:'lost'}],'idle');
 assert.equal(v.rows[0].duration,null);assert.equal(v.rows[0].status,'recorded');assert.equal(v.rows.at(-1).timeMissing,true);assert.equal(v.rows.at(-1).duration,null);
});
test('latest chat turns isolate repeated tool ids and all history keeps both',()=>{
 const events=[{kind:'request.started',created:1},{kind:'tool.started',id:'r',created:2},{kind:'request.started',created:10},{kind:'tool.started',id:'r',created:11},{kind:'tool.completed',id:'r',created:14}];
 const latest=buildChatWaterfall(events,'idle',20,'latest');assert.equal(latest.rows.filter(r=>r.kind==='tool').length,1);assert.equal(latest.rows.find(r=>r.kind==='tool').duration,3);assert.equal(latest.origin,10);
 const all=buildChatWaterfall(events,'idle',20);assert.equal(all.rows.filter(r=>r.kind==='tool').length,2);assert.equal(all.rows.find(r=>r.event.created===2).status,'interrupted');
});
test('truncated model completions keep cost with unknown duration',()=>{
 const v=buildChatWaterfall([{kind:'model.completed',created:5,total_tokens:20,cost_usd:.03}]);assert.equal(v.rows.length,1);assert.equal(v.rows[0].usage.cost_usd,.03);assert.equal(v.rows[0].duration,null);
});
test('failed chat request gets an explicit final check without copying the error body',()=>{
 const v=buildChatWaterfall([{kind:'request.started',created:1},{kind:'request.completed',created:5,status:'failed'}],'failed');assert.equal(v.rows.at(-1).kind,'check');assert.equal(v.rows.at(-1).status,'failed');
});
test('streaming model phases do not double count their response-start marker',()=>{
 const v=buildChatWaterfall([{kind:'request.started',created:1},{kind:'model.started',created:2},{kind:'message.started',created:3}],'running',5);
 assert.equal(v.rows.filter(r=>r.kind==='model').length,1);assert.equal(v.rows.find(r=>r.kind==='model').duration,null);
});
test('legacy planning runs without attempt ids never join across days',()=>{
 const events=[{kind:'status',created:1,status:'running'},{kind:'agent.started',created:2,agent_id:'main',status:'running'},{kind:'agent.completed',created:11,agent_id:'main',status:'cancelled'},{kind:'status',created:12,status:'cancelled'},
 {kind:'status',created:1000000,status:'running'},{kind:'agent.started',created:1000001,agent_id:'main',status:'running'},{kind:'agent.completed',created:1000098,agent_id:'main',status:'completed'},{kind:'status',created:1000099,status:'failed'}];
 const all=buildChatWaterfall(events,'attention',2000000);
 assert.equal(all.runs.length,2);assert.deepEqual(all.runs.map(r=>r.elapsed),[11,99]);assert.equal(all.duration,110);
 const latest=buildChatWaterfall(events,'attention',3000000,'latest');assert.equal(latest.runs.length,1);assert.equal(latest.runs[0].elapsed,99);assert.equal(latest.origin,1000000);
});
test('finished request timing is frozen even while another execution runs',()=>{
 const events=[{kind:'request.started',created:10},{kind:'model.started',created:11},{kind:'model.completed',created:19},{kind:'request.completed',created:20,status:'completed'},{kind:'request.started',created:1000},{kind:'model.started',created:1001}];
 const a=buildChatWaterfall(events,'running',2000),b=buildChatWaterfall(events,'running',2000000);
 assert.deepEqual(a.runs.map(r=>r.duration),b.runs.map(r=>r.duration));assert.equal(a.runs[0].elapsed,10);assert.equal(a.runs[1].elapsed,null);assert.equal(a.runs[1].live,true);
});
test('metadata appended after completion cannot extend a finished execution',()=>{
 const events=[{kind:'request.started',created:1},{kind:'request.completed',created:6,status:'completed'},{kind:'session.connected',created:900}];
 const v=buildChatWaterfall(events,'idle',99999);assert.equal(v.runs[0].elapsed,5);assert.equal(v.duration,5);
});
test('missing execution start or terminal timestamps do not produce a final runtime',()=>{
 assert.equal(buildChatWaterfall([{kind:'request.started'},{kind:'request.completed',created:6,status:'completed'}]).runs[0].elapsed,null);
 assert.equal(buildChatWaterfall([{kind:'request.started',created:1},{kind:'request.completed',status:'completed'}]).runs[0].elapsed,null);
});
test('a new running status starts another execution after a root ended without a task status',()=>{
 const v=buildChatWaterfall([{kind:'agent.started',created:1,agent_id:'main'},{kind:'agent.completed',created:5,agent_id:'main',status:'completed'},{kind:'status',created:500,status:'running'},{kind:'agent.started',created:501,agent_id:'main'},{kind:'agent.completed',created:510,agent_id:'main',status:'completed'}]);
 assert.deepEqual(v.runs.map(r=>r.elapsed),[4,10]);
});
test('a truncated old fragment cannot absorb a later legacy root start',()=>{
 const v=buildChatWaterfall([{kind:'tool.completed',created:1,id:'old'},{kind:'agent.started',created:1000,agent_id:'main'},{kind:'agent.completed',created:1010,agent_id:'main',status:'completed'}]);
 assert.equal(v.runs.length,2);assert.equal(v.runs[0].elapsed,null);assert.equal(v.runs[1].elapsed,10);
});

test('chat retains preparation before assembled memory and model response',()=>{
 const v=buildChatWaterfall([{kind:'request.started',created:0},{kind:'model.started',created:1},{kind:'memory.assembled',created:3},{kind:'model.completed',created:10,total_tokens:12},{kind:'request.completed',created:11,status:'completed'}],'idle');
 const prep=v.rows.find(r=>r.title==='调用前准备（含记忆组装）');
 assert.equal(prep.duration,2);assert.equal(v.rows.find(r=>r.kind==='model').duration,7);
 assert.equal(v.runs[0].elapsed,11);assert.equal(v.rows.filter(r=>r.usage).length,1);
});
