import test from 'node:test';
import assert from 'node:assert/strict';
import {taskEvaluation} from '../workbench/static/js/evaluation.mjs';
test('completed does not imply delivery or human approval',()=>{
 const e=taskEvaluation({status:'completed'},[]);
 assert.equal(e.completed,true);assert.equal(e.delivery,false);assert.equal(e.confirmed,false);
});
test('runtime evidence preserves errors and user approval separately',()=>{
 const e=taskEvaluation({status:'interrupted'},[{kind:'task.reported'},{kind:'task.confirmed'},{kind:'tool.started'},{kind:'error'},{kind:'tool.completed',data:{is_error:true}}]);
 assert.equal(e.completed,false);assert.equal(e.delivery,true);assert.equal(e.confirmed,true);assert.equal(e.tools,1);assert.equal(e.errors,2);
});

test('saved verdicts distinguish stale and missing reviews',async()=>{
 const {reviewLabel}=await import('../workbench/static/js/evaluation.mjs');
 assert.equal(reviewLabel(null),'未评测');
 assert.equal(reviewLabel({verdict:'partial'}),'部分达标');
 assert.equal(reviewLabel({verdict:'incomplete'}),'未完成');
 assert.equal(reviewLabel({verdict:'passed',stale:true}),'待重评');
});

test('metrics separate observed requests, completed usage, tool outcomes and guards',async()=>{
 const {evaluationMetrics}=await import('../workbench/static/js/evaluation.mjs');
 const e=(id,kind,data={})=>({id,kind,data});
 const events=[e(1,'model.started',{agent_id:'a'}),e(2,'pi.message_start',{agent_id:'a',message:{role:'assistant'}}),e(3,'usage',{agent_id:'b',cost_usd:null}),e(4,'tool.started',{id:'x',agent_id:'a'}),e(5,'tool.completed',{id:'x',agent_id:'a',is_error:false}),e(6,'tool.started',{id:'x',agent_id:'b'}),e(7,'tool.completed',{id:'x',agent_id:'b',is_error:true,result:{content:[{type:'text',text:'工作台拒绝越界或受保护的路径'}]}}),e(8,'tool.started',{id:'pending'}),e(9,'error'),e(10,'pi.turn_start')];
 const m=evaluationMetrics([...events,events[0],events[6]]);
 assert.equal(m.requests,1);assert.equal(m.completedOnly,1);assert.equal(m.tools,3);assert.equal(m.toolResults,2);assert.equal(m.toolSuccess,1);assert.equal(m.toolErrors,1);assert.equal(m.pendingTools,1);assert.equal(m.blocked,1);assert.equal(m.errors,1);assert.equal(m.turns,1);assert.equal(m.usage.known,0);
});
test('missing or untyped evidence is not zero requests or successful tools',async()=>{
 const {evaluationMetrics}=await import('../workbench/static/js/evaluation.mjs');
 const m=evaluationMetrics([{kind:'tool.completed',data:{id:'x'}}]);
 assert.equal(m.requests,null);assert.equal(m.turns,null);assert.equal(m.toolSuccess,0);assert.equal(m.unknownResults,1);
});

test('normalized assistant starts count once alongside raw Pi starts',async()=>{
 const {evaluationMetrics}=await import('../workbench/static/js/evaluation.mjs');
 const m=evaluationMetrics([{kind:'message.started',data:{}},{kind:'pi.message_start',data:{message:{role:'assistant'}}},{kind:'usage',data:{cost_usd:0.1}}]);
 assert.equal(m.requests,1);assert.equal(m.completedOnly,0);assert.equal(m.usage.cost,0.1);
});
