import test from 'node:test';
import assert from 'node:assert/strict';
import {buildTrace} from '../workbench/static/trace.mjs';
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
