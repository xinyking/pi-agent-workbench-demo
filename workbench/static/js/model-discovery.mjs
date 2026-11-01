/** Catalog fetching is explicit and read-only; imports merge into the unsaved form. */
const $=id=>document.getElementById(id);
export function createModelDiscovery({api,node,getRevision}){
 let generation=0,items=[],selection=new Set();
 const button=$('pi-model-fetch'),status=$('pi-model-status'),panel=$('pi-model-picker');
 function clear(){generation++;items=[];selection.clear();panel.hidden=true;$('pi-model-results').replaceChildren();status.textContent='';button.disabled=false;button.textContent='获取模型列表';}
 for(const id of ['pi-api-id','pi-api-base','pi-api-key','pi-api-protocol'])$(id).addEventListener(id==='pi-api-protocol'?'change':'input',clear);
 function render(){
  const query=$('pi-model-search').value.trim().toLowerCase(),list=$('pi-model-results');list.replaceChildren();let visible=0;
  for(const item of items){if(!(`${item.id} ${item.name}`).toLowerCase().includes(query))continue;visible++;const label=node('label','','model-discovery-item'),check=node('input');check.type='checkbox';check.value=item.id;check.checked=selection.has(item.id);check.onchange=()=>{if(check.checked)selection.add(item.id);else selection.delete(item.id);count();};const copy=node('span');copy.append(node('strong',item.id));if(item.name!==item.id)copy.append(node('small',item.name));label.append(check,copy);list.append(label);}
  if(!visible)list.append(node('p','没有匹配的模型，可修改搜索或手工填写。','muted'));count();
 }
 function count(){$('pi-model-selection-count').textContent=`已选 ${selection.size} 项 · 获取 ${items.length} 项`;}
 $('pi-model-search').oninput=render;
 $('pi-model-import').onclick=()=>{
  const previous=$('pi-api-models').value.split('\n').map(x=>x.trim()).filter(Boolean);const merged=[...new Set([...previous,...selection])];
  if(!selection.size){status.textContent='请先勾选要导入的模型。';return;}
  if(merged.length>50){status.textContent='每个连接最多50个模型，请减少选择；原有列表未改变。';return;}
  $('pi-api-models').value=merged.join('\n');status.textContent=`已将所选模型合并到下方列表，共 ${merged.length} 项；点击“保存连接”后生效。`;
 };
 button.onclick=async()=>{
  clear();const token=generation;button.disabled=true;button.textContent='正在获取…';status.textContent='正在读取服务的模型目录，不发送对话请求…';
  try{
   const result=await api('/api/settings/models/discover',{id:$('pi-api-id').value.trim(),revision:getRevision(),base_url:$('pi-api-base').value.trim(),api:$('pi-api-protocol').value,api_key:$('pi-api-key').value},{timeoutMs:18000});
   if(token!==generation)return;
   items=result.models;const existing=new Set($('pi-api-models').value.split('\n').map(x=>x.trim()));selection=new Set(items.filter(x=>existing.has(x.id)).map(x=>x.id));$('pi-model-search').value='';panel.hidden=!items.length;render();status.textContent=`读取到 ${items.length} 个模型ID。${result.warnings.join('；')}`;
  }catch(e){if(token===generation)status.textContent=e.message;}
  finally{if(token===generation){button.disabled=false;button.textContent='获取模型列表';}}
 };
 return {clear};
}
