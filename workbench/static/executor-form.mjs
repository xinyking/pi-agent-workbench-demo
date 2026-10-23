/** New-task executor controls; CLI credentials stay outside the browser. */
import {enhanceSelect} from './workspace-picker.mjs';
const $=id=>document.getElementById(id);
export function createExecutorForm({api,node}){
 function update(){const external=$('task-executor').value!=='pi';$('executor-options').hidden=!external;$('model').placeholder=external?'留空沿用所选 CLI 默认模型':'留空沿用 Pi 默认模型，或填写 provider/model-id';$('task-permission-hint').textContent=external?'默认只读；允许修改后由外部 CLI 控制工具权限，不使用 Pi 的逐次审批。可在下方选择消息与文档页并预览交接内容。':'未勾选时，任务将进入「待开始」。写文件和执行命令需要在详情页逐次审批。';}
 $('task-executor').onchange=update;
 async function load(){
  const [data,chats]=await Promise.all([api('/api/executors'),api('/api/conversations')]);const previous=$('task-executor').value||'pi';
  const select=$('task-executor');select.replaceChildren();for(const item of [{id:'pi',label:'Pi · 内置执行',available:true},...data.executors]){const option=node('option',item.label+(item.available?'':' · 不可用'));option.value=item.id;option.disabled=!item.available;select.append(option);}select.value=[...select.options].some(x=>x.value===previous&&!x.disabled)?previous:'pi';enhanceSelect(select);
  $('executor-availability').textContent=data.executors.map(x=>x.label+'：'+x.reason).join('；');
  const source=$('executor-chat'),chosen=source.value;source.replaceChildren(new Option('不引用聊天',''));for(const chat of chats.conversations)source.append(new Option(chat.title||'未命名对话',chat.id));if([...source.options].some(x=>x.value===chosen))source.value=chosen;enhanceSelect(source);enhanceSelect($('executor-mode'));update();
 }
 function payload(){return {provider_id:$('task-executor').value,executor_options:{mode:$('executor-mode').value,context:'',conversation_id:null}};}
 function reset(){$('executor-context').value='';$('executor-chat').value='';$('executor-mode').value='read-only';}
 return {load,payload,reset};
}
