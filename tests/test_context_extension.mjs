import test from 'node:test';
import assert from 'node:assert/strict';
import extension from '../workbench/pi/context-manager.ts';
function harness(percent=85){
 const hooks=new Map(),commands=new Map(),events=[];let options;let calls=0;
 extension({on:(name,handler)=>hooks.set(name,handler),registerCommand:(name,value)=>commands.set(name,value)});
 const ctx={isIdle:()=>true,getContextUsage:()=>({tokens:percent*100,contextWindow:10000,percent}),ui:{notify:message=>events.push(JSON.parse(message.slice('__workbench_context_state__'.length)))},compact:value=>{calls++;options=value;}};
 return {hooks,commands,events,ctx,get options(){return options;},get calls(){return calls;}};
}
test('compaction waits for threshold, triggers once, and preserves key constraints',()=>{
 const h=harness();h.hooks.get('before_agent_start')();h.hooks.get('turn_end')({},h.ctx);
 assert.equal(h.calls,0);assert.equal(h.events.at(-1).phase,'requested');
 h.hooks.get('turn_end')({},h.ctx);assert.equal(h.calls,0);
 assert.ok(!h.events.some(e=>e.phase==='completed'));
 h.commands.get('workbench-compact').handler('',h.ctx);assert.match(h.options.customInstructions,/硬约束/);
 h.options.onComplete({summary:'保留目标与约束'});assert.ok(h.events.some(e=>e.phase==='completed'&&e.summary==='保留目标与约束'));
});
test('low usage does not compact; failed compaction is explicit',()=>{
 const low=harness(20);low.hooks.get('turn_end')({},low.ctx);assert.equal(low.calls,0);
 const high=harness();high.commands.get('workbench-compact').handler('',high.ctx);high.options.onError(new Error('provider unavailable'));
 assert.ok(high.events.some(e=>e.phase==='failed'&&e.error==='provider unavailable'));
 high.hooks.get('turn_end')({},high.ctx);assert.equal(high.calls,1);
 high.hooks.get('before_agent_start')();high.hooks.get('turn_end')({},high.ctx);assert.equal(high.calls,1);assert.equal(high.events.at(-1).phase,'requested');
});
test('explicit command starts compaction without claiming completion',async()=>{
 const h=harness(20);await h.commands.get('workbench-compact').handler('',h.ctx);
 assert.equal(h.calls,1);assert.equal(h.events.at(-1).phase,'started');
});
