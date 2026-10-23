import {createSourceForm} from './source-form.mjs';
import {createModelInput,modelOptions} from './model-choices.mjs';
import {disposeSelect} from './workspace-picker.mjs';
import {createExecutorForm} from './executor-form.mjs';
import {createBoardController} from './board-controller.mjs';
import {createTaskDetailController} from './task-detail-controller.mjs';
import {createChatController} from './chat-controller.mjs';
import {api} from './api.mjs';
import {initPreferences} from './preferences.mjs';
import {createSettings} from './settings.mjs';
import { enhanceSelect } from './workspace-picker.mjs';
import { renderMarkdown } from './markdown.mjs';
import { enhanceWorkspace } from './workspace-picker.mjs';
'use strict';
const $ = id => document.getElementById(id);
initPreferences();
const labels = {queued:'待开始',running:'执行中',stopping:'正在停止',waiting_approval:'等待审批',waiting_input:'等待补充信息',awaiting_confirmation:'需处理',completed:'已完成',cancelled:'已停止',failed:'执行失败',interrupted:'运行中断',recorded:'已记录'};
const active = new Set(['running','stopping','waiting_approval','waiting_input']);
const kinds = {'model.started':'模型请求开始','model.completed':'模型请求结束','tool.started':'工具调用','tool.completed':'工具结果','file.changed':'文件变化','command.output':'命令输出','approval.requested':'请求审批','approval.resolved':'审批结果','plan.updated':'执行计划','usage':'本次用量','task.confirmed':'用户验收','error':'错误','context.usage':'上下文使用量','context.started':'上下文压缩开始','context.completed':'上下文压缩完成','context.failed':'上下文压缩失败','output.structured':'结构化交付','agent.definition_created':'创建 Agent','pi.diagnostic':'Pi 诊断','tool.output':'工具实时输出'};
const markdownCache=new WeakMap();
function setMarkdown(target,text){if(markdownCache.get(target)===text)return;markdownCache.set(target,text);target.classList.add('markdown-body');target.replaceChildren(renderMarkdown(text,{onPaper:reference=>openPaper(reference).catch(e=>showError(e.message,chat?.current?'chat-error':'detail-error'))}));}
function markdownNode(text){const n=node('div');setMarkdown(n,text);return n;}
const terminal = new Set(['completed','cancelled','failed','interrupted','awaiting_confirmation']);
function node(tag,text='',cls=''){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
function showError(message,target='error'){const n=$(target);n.textContent=message;n.hidden=!message;}
function button(text,fn,primary=false){const b=node('button',text,primary?'primary':'');b.type='button';b.onclick=async event=>{event.stopPropagation();b.disabled=true;try{await fn();}catch(error){showError(error.message,$('agents-dialog').open?'agents-message':$('detail-dialog').open?'detail-error':'error');}finally{b.disabled=false;}};return b;}
function openDialog(id){if(!$(id).open)$(id).showModal();}
let config={workspaces:[],pi:{},roles:[]},board,detail,chat;
const refreshBoard=()=>board.refreshBoard(),scheduleRefresh=()=>board.scheduleRefresh();
const renderWorkspaceSidebar=()=>board.renderWorkspaceSidebar();
const selectTask=id=>detail.selectTask(id),loadDetailState=()=>detail.loadDetailState(),connectDetail=()=>detail.connectDetail();
const openPaper=reference=>chat.openPaper(reference);
board=createBoardController({api,node,labels,showError,getConfig:()=>config,getConversation:()=>chat?.current,selectTask,showBoardView:()=>chat.showBoardView(),onTasks:tasks=>detail.updateTasks(tasks),loadUsage:()=>loadUsage()});
detail=createTaskDetailController({api,node,button,labels,active,terminal,kinds,showError,openDialog,markdownNode,setMarkdown,renderOutput,renderContextState,downloadMarkdown,refreshBoard,scheduleRefresh,setConnection:connected=>board.setConnection(connected),getTasks:()=>board.tasks,openPaper});
chat=createChatController({api,node,button,labels,kinds,showError,openDialog,markdownNode,downloadMarkdown,renderContextState,options,getConfig:()=>config,getWorkspaceFilter:()=>board.workspaceFilter,getSelectedTask:()=>detail.selected,selectTask,refreshBoard,scheduleRefresh,renderWorkspaceSidebar,syncSidebarSelection:()=>board.syncSidebarSelection()});
function options(select,values,previous){select.replaceChildren();for(const [value,label] of values){const option=node('option',label);option.value=value;select.append(option);}if(values.some(([value])=>value===previous))select.value=previous;if(['workspace','conversation-workspace','chat-workspace'].includes(select.id))enhanceWorkspace(select);else enhanceSelect(select);}
async function loadConfig(){config=await api('/api/config');options($('workspace'),config.workspaces.map(w=>[w,w]),board.workspaceFilter||$('workspace').value);$('workspace-list').replaceChildren(...config.workspaces.map(w=>node('div',w,'settings-workspace-row')));if(!config.workspaces.length)$('workspace-list').append(node('p','还没有工作区，添加一个项目目录后即可开始。','muted'));$('settings-runtime-summary').textContent=config.pi?.available?'Pi '+config.pi.version:'尚未连接';$('settings-workspace-count').textContent=String(config.workspaces.length);$('settings-agent-count').textContent=String((config.roles||[]).length);$('settings-shell-summary').textContent=config.shell_mode==='approval'?'每次执行前请求审批':'当前禁用本地 Shell 命令';$('shell-mode').value=config.shell_mode;enhanceSelect($('shell-mode'));$('setup').hidden=Boolean(config.pi?.available&&config.workspaces.length);$('pi-status').textContent=config.pi?.available?`Pi ${config.pi.version} · ${config.pi.binary}`:'未检测到 Pi；请在 WSL 安装和配置，或用 WORKBENCH_PI 指定可执行路径';$('role-list').replaceChildren(...(config.roles||[]).map(role=>node('span',role,'workspace-path')));if(!config.roles?.length)$('role-list').append(node('p','暂未启用角色，可在 Agent 库中管理。','muted'));renderWorkspaceSidebar();}
const {settings,loadUsage}=createSettings({loadConfig,openDialog,showError,node,api});
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
const taskModel=createModelInput({input:$('model'),api}),agentModel=createModelInput({input:$('agent-model'),api,inherit:'继承任务模型'});
const executorForm=createExecutorForm({api,node});
const sourceForm=createSourceForm({api,node});
function showTaskModel(){taskModel.show($('task-executor').value==='pi');}
$('task-executor').addEventListener('change',showTaskModel);
document.addEventListener('workbench-models-changed',()=>Promise.all([taskModel.load(),agentModel.load()]).catch(e=>showError(e.message)));
$('new-task').onclick=async()=>{try{await loadConfig();await executorForm.load();await taskModel.load();await sourceForm.load();showTaskModel();showError('','new-error');openDialog('new-dialog');if(board.workspaceFilter)$('workspace').value=board.workspaceFilter;$('prompt').focus();}catch(e){showError(e.message);}};
$('new-conversation').onclick=()=>chat.newConversation();
$('workspace-add').onclick=async()=>{try{await settings('workspaces');$('workspace-path').focus();}catch(e){showError(e.message);}};
$('sidebar-toggle').onclick=()=>{const collapsed=document.body.classList.toggle('sidebar-collapsed');$('sidebar-toggle').setAttribute('aria-expanded',String(!collapsed));$('sidebar-toggle').setAttribute('aria-label',collapsed?'展开侧栏':'收起侧栏');$('sidebar-toggle').title=collapsed?'展开侧栏':'收起侧栏';};
$('settings-open').onclick=()=>settings().catch(e=>showError(e.message));$('setup-open').onclick=$('settings-open').onclick;$('refresh').onclick=refreshBoard;
$('new-form').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{const result=await api('/api/runs',{prompt:$('prompt').value,workspace:$('workspace').value,model:$('model').value,...executorForm.payload(),...sourceForm.payload()});if($('start-now').checked)await api(`/api/runs/${result.id}/start`,{});$('prompt').value='';executorForm.reset();sourceForm.reset();$('new-dialog').close();await refreshBoard();await selectTask(result.id);}catch(e){showError(e.message,'new-error');}finally{b.disabled=false;}};
$('prompt').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))$('new-form').requestSubmit($('new-form').querySelector('[type=submit]'));};
for(const [form,fn] of [['workspace-form',async()=>{await api('/api/workspaces',{path:$('workspace-path').value});$('workspace-path').value='';await loadConfig();return '工作区已添加';}],['shell-form',async()=>{await api('/api/settings',{shell_mode:$('shell-mode').value});await loadConfig();return '命令设置已保存（新启动的会话生效）';}]])$(form).onsubmit=async event=>{event.preventDefault();event.submitter.disabled=true;try{showError(await fn(),'settings-message');}catch(e){showError(e.message,'settings-message');}finally{event.submitter.disabled=false;}};
let libraryAgents=[];
const roleTools=['read','grep','find','ls','write','edit','bash'];
for(const tool of roleTools){const label=node('label',tool,'check'),input=node('input');input.type='checkbox';input.value=tool;input.checked=['read','grep','find','ls'].includes(tool);label.prepend(input);$('agent-tools').append(label);}
function resetAgentForm(){ $('agent-form').reset();$('agent-model').value='';agentModel.sync();$('agent-name').readOnly=false;$('agent-editor-title').textContent='添加 Agent';for(const input of $('agent-tools').querySelectorAll('input'))input.checked=['read','grep','find','ls'].includes(input.value);}
async function loadAgentLibrary(){
 const [data,modelData]=await Promise.all([api('/api/agents'),api('/api/settings/models')]);await agentModel.load();
 for(const select of $('agents-library').querySelectorAll('select'))disposeSelect(select);libraryAgents=data.agents;const target=$('agents-library');target.replaceChildren();
 for(const agent of libraryAgents){const card=node('article','','library-agent');const heading=node('div','','library-agent-head');heading.append(node('h3',agent.name),node('span',agent.enabled?'已启用':'已停用','status'));card.append(heading,node('p',agent.description),node('small',`${agent.source==='builtin'?'内置':'自定义'} · ${agent.tools.join(' / ')}`));const modelLabel=node('label','执行模型');const modelSelect=node('select');modelSelect.id='agent-llm-'+agent.name;modelLabel.append(modelSelect);card.append(modelLabel);target.append(card);modelOptions(modelSelect,modelData,agent.model||'');modelSelect.onchange=async()=>{const previous=agent.model||'';modelSelect.disabled=true;try{await api('/api/agents/model',{name:agent.name,model:modelSelect.value});agent.model=modelSelect.value;showError('Agent 模型已保存，下一次任务执行生效。','agents-message');}catch(e){modelSelect.value=previous;showError(e.message,'agents-message');}finally{modelSelect.disabled=false;enhanceSelect(modelSelect);}};const actions=node('div','','node-actions');actions.append(button(agent.enabled?'停用':'启用',async()=>{await api('/api/agents/toggle',{name:agent.name,enabled:!agent.enabled});await loadAgentLibrary();await loadConfig();}),button(agent.source==='builtin'?'复制并编辑':'编辑',()=>{resetAgentForm();$('agent-name').value=agent.source==='builtin'?agent.name+'-custom':agent.name;$('agent-name').readOnly=agent.source!=='builtin';$('agent-description').value=agent.description;$('agent-prompt').value=agent.prompt;$('agent-model').value=agent.model||'';agentModel.sync();for(const input of $('agent-tools').querySelectorAll('input'))input.checked=agent.tools.includes(input.value);$('agent-editor-title').textContent=agent.source==='builtin'?'复制内置 Agent':'编辑 Agent';$('agent-name').focus();}),button('导出 .md',()=>{const lines=['---','name: '+agent.name,'description: '+JSON.stringify(agent.description),'tools: '+agent.tools.join(', '),...(agent.model?['model: '+JSON.stringify(agent.model)]:[]),'---','',agent.prompt];const blob=new Blob([lines.join('\n')],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download=agent.name+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}));
 if(agent.source==='custom'){const remove=button('删除',async()=>{await api('/api/agents/delete',{name:agent.name});await loadAgentLibrary();await loadConfig();});actions.append(remove);}card.append(actions);target.append(card);}
}
$('agents-open').onclick=async()=>{try{await loadAgentLibrary();openDialog('agents-dialog');}catch(e){showError(e.message);}};
$('agent-reset').onclick=resetAgentForm;
$('agent-form').onsubmit=async event=>{event.preventDefault();event.submitter.disabled=true;try{const tools=[...$('agent-tools').querySelectorAll('input:checked')].map(i=>i.value);await api('/api/agents',{name:$('agent-name').value,description:$('agent-description').value,prompt:$('agent-prompt').value,tools,model:$('agent-model').value});await loadAgentLibrary();await loadConfig();showError('Agent 已保存，下一次执行可委派给它。','agents-message');resetAgentForm();}catch(e){showError(e.message,'agents-message');}finally{event.submitter.disabled=false;}};
$('agent-import-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;if(file.size>80000){showError('文件过大，请使用 20000 字符以内的 Agent 定义。','agents-message');return;}$('agent-markdown').value=await file.text();};
$('agent-import-form').onsubmit=async event=>{event.preventDefault();event.submitter.disabled=true;try{await api('/api/agents',{markdown:$('agent-markdown').value});await loadAgentLibrary();await loadConfig();showError('Pi Agent 已导入。','agents-message');$('agent-markdown').value='';$('agent-import-file').value='';}catch(e){showError(e.message,'agents-message');}finally{event.submitter.disabled=false;}};

function renderOutput(output){const container=node('section','','structured-output');container.append(node('h3',output.headline||'交付结果'),markdownNode(output.summary||''));const list=node('ol');for(const item of output.actionItems||[])list.append(node('li',item));container.append(list);const download=(format)=>{const text=format==='json'?JSON.stringify(output,null,2):'# '+(output.headline||'交付结果')+'\n\n'+(output.summary||'')+'\n\n'+(output.actionItems||[]).map((v,i)=>`${i+1}. ${v}`).join('\n');const url=URL.createObjectURL(new Blob([text],{type:format==='json'?'application/json':'text/markdown;charset=utf-8'})),a=node('a');a.href=url;a.download='result.'+(format==='json'?'json':'md');a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};const actions=node('div','','node-actions');actions.append(button('下载 Markdown',()=>download('md')),button('下载 JSON',()=>download('json')));container.append(actions);return container;}

function renderContextState(target,data){target.replaceChildren();if(!data)return;const usage=data.usage;const label={requested:'上下文使用量较高，将在本次执行结束后压缩',started:'正在压缩上下文',completed:'上下文已压缩，原始记录保留',failed:'压缩失败，保留原始会话',usage:'上下文使用量'}[data.phase]||'上下文';target.append(node('span',label));if(usage?.percent!=null){const bar=node('progress');bar.max=100;bar.value=usage.percent;bar.setAttribute('aria-label','上下文使用率');target.append(bar,node('span',`${usage.percent.toFixed(1)}% · ${usage.tokens??'未知'} / ${usage.contextWindow} tokens`));}if(data.error)target.append(node('span',data.error));}

function downloadMarkdown(text){const blob=new Blob([text],{type:'text/markdown;charset=utf-8'}),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='reading-notes.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
for(const id of ['trace-agent','trace-kind','shell-mode','paper-section'])enhanceSelect($(id));

Promise.all([loadConfig(),refreshBoard(),loadUsage(),chat.loadConversations()]).catch(e=>showError(e.message));
board.start();
window.addEventListener('pagehide',()=>{board.dispose();detail.dispose();chat.dispose();},{once:true});
