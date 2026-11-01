import test from 'node:test';
import assert from 'node:assert/strict';
import {summarizeUsage,usageText} from '../workbench/static/js/usage-view.mjs';
import {buildTimeline} from '../workbench/static/js/timeline.mjs';

test('unknown costs stay separate from valid subtotals and cache does not double count input',()=>{
 const events=[{kind:'usage',data:{prompt_tokens:2,completion_tokens:3,cache_read_tokens:5,cache_write_tokens:7,total_tokens:17,cost_usd:.02}}, {kind:'usage',data:{total_tokens:1}}, {kind:'tool.completed',data:{cost_usd:99}}];
 assert.deepEqual(summarizeUsage(events),{requests:2,input:2,output:3,cacheRead:5,cacheWrite:7,total:18,cost:.02,known:1});
 assert.match(usageText(events[1].data),/费用未知/);
 assert.match(usageText(events[1].data),/缓存读 未知/);
});
test('usage remains visible grouped with its agent and execution attempt',()=>{
 const events=[{id:1,kind:'usage',created:1,data:{agent_id:'child',attempt_id:'round',cost_usd:null}}];
 const groups=buildTimeline(events);
 assert.equal(groups[0].agentId,'child');
 assert.equal(groups[0].attempt,'round');
 assert.equal(groups[0].items[0].event.kind,'usage');
});

test('DeepSeek current-price range keeps CNY and unknown coverage explicit',async()=>{
 const {deepseekEstimateText}=await import('../workbench/static/js/usage-view.mjs');
 assert.equal(deepseekEstimateText(null),'未知');
 assert.equal(deepseekEstimateText({minimum:'0.00885',maximum:'0.01770',unknown_records:1}),'¥0.008850–0.017700 + 未知');
});

test('single estimate prefers CNY without adding duplicate USD and keeps currencies separate',async()=>{
 const {taskEstimate,estimateText,totalEstimate}=await import('../workbench/static/js/usage-view.mjs');
 const events=[{kind:'usage',data:{cost_usd:10}}];
 const cny=taskEstimate({deepseek_estimate:{minimum:'1',maximum:'2',priced_records:1,unknown_records:0}},events);
 assert.equal(estimateText(cny),'¥1.000000–2.000000');
 const usd=taskEstimate({},events);assert.equal(estimateText(usd),'US$10.000000');
 assert.equal(totalEstimate([cny,cny]).text,'¥2.000000–4.000000');
 assert.equal(totalEstimate([cny,usd]).text,'多币种，见各任务');
});
