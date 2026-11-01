export function initDeepseekBilling({api,node}){
 const $=id=>document.getElementById(id);let data=null,busy=false;
 function render(){
  const source=$('deepseek-billing-source').value,item=data?.sources.find(s=>s.id===source),target=$('deepseek-billing-content');target.replaceChildren();
  $('deepseek-billing-refresh').disabled=busy||!item;
  if(!item){target.append(node('p','未找到 DeepSeek 官方认证。可在模型设置添加 api.deepseek.com 连接，或配置 Pi 原生 DeepSeek API Key。','muted'));return;}
  const snapshot=item.snapshot;
  if(!snapshot){target.append(node('p','尚未查询；点击“查询官方余额”，只发送余额 GET 请求，不运行模型。','muted'));return;}
  target.append(node('p',`查询时间：${new Date(snapshot.queried_at).toLocaleString()} · ${snapshot.is_available?'账户有可用余额':'账户无可用余额'}`));
  for(const row of snapshot.balance_infos){
   target.append(node('p',`${row.currency} 可用余额 ${row.total_balance} · 充值余额 ${row.topped_up_balance} · 赠金 ${row.granted_balance}`));
   const delta=item.balance_change.find(d=>d.currency===row.currency);
   target.append(node('p',delta?`${row.currency} 两次查询间余额净减少 ${delta.net_decrease}（起点 ${new Date(item.previous_at).toLocaleString()}；含充值/赠金等影响，非任务扣费）`:'账户净变动：需两次间隔至少 60 秒的成功查询。','muted'));
  }
 }
 async function load(){
  try{const selected=$('deepseek-billing-source').value;data=await api('/api/billing/deepseek');$('deepseek-billing-source').replaceChildren(...data.sources.map(s=>{const option=node('option',s.name);option.value=s.id;return option;}));if(data.sources.some(s=>s.id===selected))$('deepseek-billing-source').value=selected;render();}
  catch(error){$('deepseek-billing-error').textContent=error.message;}
 }
 $('deepseek-billing-source').onchange=render;
 $('deepseek-billing-refresh').onclick=async()=>{
  if(busy)return;busy=true;$('deepseek-billing-error').textContent='';render();
  try{data=await api('/api/billing/deepseek/refresh',{source:$('deepseek-billing-source').value},{timeoutMs:12000});render();if(data.cached)$('deepseek-billing-error').textContent='60 秒内复用上次官方余额；查询时间未变化。';}
  catch(error){$('deepseek-billing-error').textContent=error.message+'；未覆盖上次成功查询。';}
  finally{busy=false;render();}
 };
 return {load};
}
