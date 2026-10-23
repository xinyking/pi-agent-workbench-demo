import test from 'node:test';
import assert from 'node:assert/strict';
import {buildProgress} from '../workbench/static/progress.mjs';
test('progress follows actual document calls and streamed answer events',()=>{
 const chat={status:'running',mode:'chat',documents:[{id:'paper',name:'论文.pdf'}],execution:[{kind:'request.started'},{kind:'session.connected'},{kind:'tool.started',name:'workbench_document',arguments:{action:'read',document_id:'paper',page:3}}]};
 assert.equal(buildProgress(chat).label,'读取《论文.pdf》 · 第 3 页');
 chat.execution.push({kind:'answer.started'});assert.equal(buildProgress(chat).label,'正在生成回答');
 chat.status='idle';assert.equal(buildProgress(chat).label,'回答已完成');
});
test('parallel child activity never hides required user action or invents plan progress',()=>{
 const chat={mode:'planning',status:'attention',execution:[{kind:'agent.started',agent_id:'child',role:'researcher'},{kind:'tool.started',agent_id:'child',name:'read'},{kind:'plan.updated',steps:[{id:'a',title:'阅读',status:'completed'},{id:'b',title:'笔记',status:'running'}]}]};
 const state=buildProgress(chat);assert.equal(state.phase,'attention');assert.match(state.label,/需要你处理/);assert.equal(state.completed,1);assert.equal(state.steps.length,2);
 chat.status='running';assert.match(buildProgress(chat).label,/笔记/);
});
test('new turn resets visible history and stopped/error states never claim success',()=>{
 const chat={status:'failed',execution:[{kind:'tool.started',name:'read'},{kind:'request.started'},{kind:'message.started'}]};
 assert.equal(buildProgress(chat).history.length,2);assert.match(buildProgress(chat).label,/失败/);
 chat.status='interrupted';assert.match(buildProgress(chat).label,/中断/);
});
