import {createModelDiscovery} from './model-discovery.mjs';
import {modelOptions} from './model-choices.mjs';
import {enhanceSelect} from './workspace-picker.mjs';
const $=id=>document.getElementById(id);
export function createModelSettings({api,node,showError}){
 let data=null,generation=0;
 const discovery=createModelDiscovery({api,node,getRevision:()=>data?.revision});
 function reset(){ discovery.clear();$('pi-api-form').reset();$('pi-api-id').readOnly=false;$('pi-api-key').placeholder='填写密钥；本地无认证服务可填写 local';$('pi-api-editor-title').textContent='添加 API 连接';enhanceSelect($('pi-api-protocol'));}
 async function save(payload){if(!data)throw new Error('模型设置尚未加载，请稍后重试');data=await api('/api/settings/models',{...payload,revision:data.revision});render();document.dispatchEvent(new Event('workbench-models-changed'));}
 async function load(){const token=++generation;try{const result=await api('/api/settings/models');if(token!==generation)return;data=result;render();}catch(e){showError(e.message,'settings-message');}}
 function render(){
  const list=$('pi-api-list');list.replaceChildren();
  for(const p of data.providers){
   const card=node('article','','model-connection');card.append(node('strong',p.name),node('p',p.base_url,'muted'),node('small',`${p.models.length} 个模型 · ${p.api} · ${p.has_key?'已保存密钥':'未设置密钥'}`));const actions=node('div','','node-actions');
   const edit=node('button','编辑');edit.type='button';edit.onclick=()=>{reset();$('pi-api-id').value=p.id;$('pi-api-id').readOnly=true;$('pi-api-name').value=p.name;$('pi-api-base').value=p.base_url;$('pi-api-protocol').value=p.api;$('pi-api-models').value=p.models.join('\n');$('pi-api-window').value=p.context_window;$('pi-api-output').value=p.max_tokens;$('pi-api-reasoning').checked=p.reasoning;$('pi-api-vision').checked=p.vision;$('pi-api-key').placeholder='留空保留已保存密钥';$('pi-api-editor-title').textContent='编辑 API 连接';enhanceSelect($('pi-api-protocol'));$('pi-api-name').focus();};
   const remove=node('button','删除');remove.type='button';remove.onclick=async()=>{remove.disabled=true;try{await save({action:'delete',id:p.id});reset();showError('连接已删除；已有任务记录保留。','settings-message');}catch(e){showError(e.message,'settings-message');}finally{remove.disabled=false;}};actions.append(edit,remove);card.append(actions);list.append(card);
  }
  if(!data.providers.length)list.append(node('p','还没有工作台 API 连接。现有 Pi 登录与模型配置仍可使用。','muted'));
  modelOptions($('pi-default-model'),data,data.default_model,'沿用本机 Pi 默认模型');enhanceSelect($('pi-api-protocol'));
 }
 $('pi-api-reset').onclick=reset;
 $('pi-api-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{await save({action:'upsert',id:$('pi-api-id').value.trim(),name:$('pi-api-name').value.trim(),base_url:$('pi-api-base').value.trim(),api:$('pi-api-protocol').value,api_key:$('pi-api-key').value,models:$('pi-api-models').value.split('\n').map(x=>x.trim()).filter(Boolean),context_window:Number($('pi-api-window').value),max_tokens:Number($('pi-api-output').value),reasoning:$('pi-api-reasoning').checked,vision:$('pi-api-vision').checked});reset();showError('API 连接已保存；未发送模型请求。','settings-message');}catch(e){showError(e.message,'settings-message');}finally{b.disabled=false;}};
 $('pi-default-form').onsubmit=async event=>{event.preventDefault();event.submitter.disabled=true;try{await save({action:'default',model:$('pi-default-model').value});showError('默认模型已保存，新任务与新对话使用此模型。','settings-message');}catch(e){showError(e.message,'settings-message');}finally{event.submitter.disabled=false;}};
 return {load,close(){generation++;discovery.clear();$('pi-api-key').value='';}};
}
