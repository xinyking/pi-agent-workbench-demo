import {createModelSettings} from './model-settings.mjs';
import {createMemorySettings} from './memory-settings.mjs';
const $=id=>document.getElementById(id);
export function createSettings({loadConfig,openDialog,showError,node,api}){
let activeSettingsTab='general';
const modelSettings=createModelSettings({api,node,showError});
const memorySettings=createMemorySettings({api,node,showError});
$('settings-dialog').addEventListener('close',()=>{memorySettings.close();modelSettings.close();});
function selectSettingsTab(name,focus=false){
 if(name!=='models')modelSettings.close();
 activeSettingsTab=name;for(const tab of document.querySelectorAll('[data-settings-tab]')){const selected=tab.dataset.settingsTab===name;tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;$('settings-panel-'+tab.dataset.settingsTab).hidden=!selected;if(selected&&focus)tab.focus();}
 showError('','settings-message');if(name==='usage')loadUsage();if(name==='models')modelSettings.load();if(name==='memory')memorySettings.load();
}
for(const tab of document.querySelectorAll('[data-settings-tab]')){
 tab.onclick=()=>selectSettingsTab(tab.dataset.settingsTab);
 tab.onkeydown=event=>{if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...document.querySelectorAll('[data-settings-tab]')],index=tabs.indexOf(tab);const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowDown'?1:-1)+tabs.length)%tabs.length;selectSettingsTab(tabs[next].dataset.settingsTab,true);};
}
for(const button of document.querySelectorAll('[data-settings-link]'))button.onclick=()=>selectSettingsTab(button.dataset.settingsLink,true);
$('settings-manage-agents').onclick=()=>{$('settings-dialog').close();$('agents-open').click();};
async function settings(section=activeSettingsTab){await loadConfig();selectSettingsTab(section);openDialog('settings-dialog');}

async function loadUsage(){try{const u=await api('/api/usage');$('usage').textContent=`${u.requests} 次模型请求 · ${u.total_tokens.toLocaleString()} Tokens（已记录）`;$('settings-usage-grid').replaceChildren();for(const [title,value] of [['请求次数',u.requests],['输入 Tokens',u.prompt_tokens],['输出 Tokens',u.completion_tokens],['总 Tokens',u.total_tokens]]){const cell=node('div');cell.append(node('span',title),node('strong',Number(value).toLocaleString()));$('settings-usage-grid').append(cell);}}catch{$('usage').textContent='用量暂时无法读取，请稍后重试。';$('settings-usage-grid').replaceChildren();}}

return {settings,loadUsage};
}
