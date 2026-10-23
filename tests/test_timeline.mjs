import test from 'node:test';
import assert from 'node:assert/strict';
import {buildTimeline} from '../workbench/static/timeline.mjs';
const event=(id,agent_id,kind,more={})=>({id,created:id,kind,data:{agent_id,attempt_id:'round',...more}});
test('interleaved agents merge within an attempt and pair tool results',()=>{
 const events=[event(1,'main','tool.started',{id:'delegate',name:'workbench_delegate'}),event(2,'child','agent.started',{role:'researcher'}),event(3,'child','tool.started',{id:'read',name:'read'}),event(4,'main','agent.status',{status:'running'}),event(5,'child','tool.completed',{id:'read',name:'read'}),event(6,'main','tool.completed',{id:'delegate',name:'workbench_delegate'}),event(7,'main','task.reported')];
 const groups=buildTimeline(events);
 assert.deepEqual(groups.map(g=>g.agentId),['main','child']);
 assert.deepEqual(groups.flatMap(g=>g.items.map(i=>i.start)),[1,4,7,2,3]);
 assert.equal(groups[0].items[0].end,6);assert.equal(groups[1].items[1].end,5);
 assert.equal(groups[1].role,'researcher');assert.equal(groups[0].calls,1);
});
test('different attempts never merge; missing end event does not claim success',()=>{
 const events=[event(1,'main','tool.started',{id:'same',name:'read'}),event(2,'main','agent.completed',{status:'failed'}),event(3,'main','tool.started',{attempt_id:'retry',id:'same',name:'read'}),event(4,'main','tool.completed',{attempt_id:'retry',id:'same',is_error:false})];
 const groups=buildTimeline(events,[],'completed');assert.equal(groups.length,2);
 assert.equal(groups[0].items[0].status,'interrupted');assert.equal(groups[1].items[0].status,'completed');
});
test('streamed text and tool updates do not become fake extra calls',()=>{
 const groups=buildTimeline([event(1,'main','message.delta',{text:'hello'}),event(2,'main','tool.started',{id:'r',name:'read'}),event(3,'main','tool.output',{id:'r',result:{content:[]}}),event(4,'main','tool.completed',{id:'r',is_error:true})]);
 assert.equal(groups.length,1);assert.equal(groups[0].calls,1);assert.equal(groups[0].items.length,1);assert.equal(groups[0].items[0].status,'failed');
});

test('model replies remain visible without counting them as tool calls',()=>{
 const groups=buildTimeline([event(1,'main','message.completed',{text:'读取项目说明'}),event(2,'main','tool.started',{id:'r',name:'read'}),event(3,'child','agent.started',{role:'reviewer'}),event(4,'main','message.completed',{text:'已核对结果'})]);
 assert.equal(groups.length,2);assert.equal(groups[0].calls,1);assert.deepEqual(groups[0].items.map(i=>i.event.kind),['message.completed','tool.started','message.completed']);
});
