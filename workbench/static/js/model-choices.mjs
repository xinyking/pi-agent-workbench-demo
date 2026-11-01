/** Shared model choices; native Pi IDs remain available through manual entry. */
import {enhanceSelect,disposeSelect} from './workspace-picker.mjs';
export function modelOptions(select,data,current='',inherit='继承任务模型'){
 select.replaceChildren(new Option(inherit,''));
 for(const provider of data.providers)for(const id of provider.models)select.append(new Option(provider.name+' · '+id,'wbapi-'+provider.id+'/'+id));
 if(current&&![...select.options].some(o=>o.value===current))select.append(new Option('Pi / 自定义 · '+current,current));
 select.value=current;enhanceSelect(select);
}
export function createModelInput({input,api,inherit='使用默认模型'}){
 const select=document.createElement('select');select.id=input.id+'-choice';input.before(select);let custom=false;
 async function load(){const data=await api('/api/settings/models');modelOptions(select,data,input.value,inherit);select.append(new Option('手工填写 Pi 模型标识','__manual__'));enhanceSelect(select);input.hidden=true;custom=false;}
 select.onchange=()=>{custom=select.value==='__manual__';input.hidden=!custom;if(custom){input.value='';input.focus();}else input.value=select.value;};
 function sync(){if(![...select.options].some(o=>o.value===input.value)&&input.value)select.add(new Option('Pi / 自定义 · '+input.value,input.value));select.value=input.value;enhanceSelect(select);input.hidden=true;}
 return {load,sync,show(enabled){select.nextElementSibling?.matches('.select-trigger')&&(select.nextElementSibling.hidden=!enabled);if(!enabled)input.hidden=false;else input.hidden=!custom;},dispose(){disposeSelect(select);select.remove();}};
}
