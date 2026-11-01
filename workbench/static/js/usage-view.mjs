export function summarizeUsage(events){
 const rows=events.filter(e=>e.kind==='usage'),sum={requests:rows.length,input:0,output:0,cacheRead:0,cacheWrite:0,total:0,cost:0,known:0};
 const num=v=>Number.isFinite(v)&&v>=0?v:0;
 for(const {data:d} of rows){sum.input+=num(d.prompt_tokens);sum.output+=num(d.completion_tokens);sum.cacheRead+=num(d.cache_read_tokens);sum.cacheWrite+=num(d.cache_write_tokens);sum.total+=num(d.total_tokens);if(typeof d.cost_usd==='number'&&Number.isFinite(d.cost_usd)&&d.cost_usd>=0){sum.cost+=d.cost_usd;sum.known++;}}
 return sum;
}
export const money=value=>`US$${value.toFixed(6)}`;
export function usageText(d){
 return `输入 ${d.prompt_tokens??'未知'} · 输出 ${d.completion_tokens??'未知'} · 缓存读 ${d.cache_read_tokens??'未知'} / 写 ${d.cache_write_tokens??'未知'} Tokens · 合计 ${d.total_tokens??'未知'} Tokens · ${typeof d.cost_usd==='number'?money(d.cost_usd)+'（模型价格估算）':'费用未知'}`;
}
export function renderUsageSummary(events,node,{showCost=true}={}){
 const u=summarizeUsage(events),card=node('section','','memory-card task-usage');
 card.append(node('h4',showCost?'用量与费用 · 当前筛选':'用量统计'),node('p',`${u.requests} 条模型用量记录 · 合计 ${u.total.toLocaleString()} Tokens · 输入 ${u.input.toLocaleString()} / 输出 ${u.output.toLocaleString()} · 缓存读 ${u.cacheRead.toLocaleString()} / 写 ${u.cacheWrite.toLocaleString()} Tokens`));
 if(showCost)card.append(node('p',u.known?`已知费用小计 ${money(u.cost)} · ${u.requests-u.known} 条费用未知`:'费用未知 · 尚无有效计价记录'));
 card.append(node('small','仅统计已收到的用量事件；旧记录缺少缓存字段时不计入缓存小计。金额为模型价格估算，不代表供应商账单；未知价格不计作免费。','muted'));
 return card;
}

export function deepseekEstimateText(estimate){
 if(!estimate||estimate.minimum==null)return '未知';
 return `¥${Number(estimate.minimum).toFixed(6)}–${Number(estimate.maximum).toFixed(6)}${estimate.unknown_records?' + 未知':''}`;
}
export function taskEstimate(run,events){
 const official=run.deepseek_estimate;
 if(official?.minimum!=null)return {currency:'CNY',minimum:Number(official.minimum),maximum:Number(official.maximum),known:official.priced_records,unknown:official.unknown_records,official};
 const usage=summarizeUsage(events);
 return {currency:usage.known?'USD':null,minimum:usage.cost,maximum:usage.cost,known:usage.known,unknown:usage.requests-usage.known};
}
export function estimateText(estimate){
 if(!estimate.currency||!estimate.known)return '未知';
 const prefix=estimate.currency==='CNY'?'¥':'US$';
 const range=estimate.minimum===estimate.maximum?estimate.minimum.toFixed(6):`${estimate.minimum.toFixed(6)}–${estimate.maximum.toFixed(6)}`;
 return `${prefix}${range}${estimate.unknown?' + 未知':''}`;
}
export function totalEstimate(estimates){
 const currencies=new Set(estimates.filter(e=>e.known).map(e=>e.currency));
 const unknown=estimates.reduce((n,e)=>n+e.unknown,0),known=estimates.reduce((n,e)=>n+e.known,0);
 if(currencies.size>1)return {text:'多币种，见各任务',unknown,known};
 return {text:estimateText({currency:[...currencies][0],minimum:estimates.reduce((n,e)=>n+e.minimum,0),maximum:estimates.reduce((n,e)=>n+e.maximum,0),known,unknown}),unknown,known};
}
export function renderTaskEstimate(run,events,node){
 const estimate=taskEstimate(run,events),card=node('section','','memory-card');card.append(node('h4','估算金额'),node('p',estimateText(estimate)));
 card.append(node('p',`已计价 ${estimate.known} 条 · 未知 ${estimate.unknown} 条`));
 const details=node('details');details.append(node('summary','计价依据'));
 if(estimate.official){
  details.append(node('p',`人民币 · 价格快照 ${estimate.official.price_date}`),node('p',estimate.official.basis,'muted'));
  const link=node('a','官方计价规则');link.href=estimate.official.price_source;link.target='_blank';link.rel='noopener';details.append(link);
 }else details.append(node('p',estimate.currency?'美元 · 根据已有模型用量与单价估算':'尚无可用的计价信息','muted'));
 details.append(node('p','估算不代表实际账单；账户余额独立展示。','muted'));card.append(details);
 return card;
}
