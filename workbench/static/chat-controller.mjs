import {createViewSession} from './view-session.mjs';
import {buildTrace} from './trace.mjs';
import {buildProgress} from './progress.mjs';
import {reconcileSidebar} from './sidebar.mjs';
import {enhanceSelect,enhanceWorkspace} from './workspace-picker.mjs';
const $=id=>document.getElementById(id);
export function createChatController({api,node,button,labels,kinds,showError,openDialog,markdownNode,downloadMarkdown,renderContextState,options,getConfig,getWorkspaceFilter,getSelectedTask,selectTask,refreshBoard,scheduleRefresh,renderWorkspaceSidebar,syncSidebarSelection}){
const session=createViewSession();
let currentConversation=null,chatSource=null,chatCursor=0,chatDrafts=new Map();
function showBoardView(){if(currentConversation)chatDrafts.set(currentConversation.id,$('chat-input').value);session.close();chatSource?.close();chatSource=null;currentConversation=null;$('chat-view').hidden=true;for(const cls of ['page-header','board'])document.querySelector('.'+cls).hidden=false;$('board-home').setAttribute('aria-current','page');$('board-home').classList.add('active');for(const b of $('conversation-list').children)b.setAttribute('aria-pressed','false');$('setup').hidden=Boolean(getConfig().pi?.available&&getConfig().workspaces.length);syncSidebarSelection();refreshBoard();}
function showChatView(){for(const cls of ['page-header','board','archive','search-tools','filter-info','notice'])document.querySelector('.'+cls).hidden=true;$('chat-view').hidden=false;$('board-home').removeAttribute('aria-current');$('board-home').classList.remove('active');renderWorkspaceSidebar();}
let conversationListVersion=0;
async function loadConversations(){
 const version=++conversationListVersion;
 try{const data=await api('/api/conversations');if(version!==conversationListVersion)return;
  reconcileSidebar($('conversation-list'),data.conversations.map(chat=>({...chat,key:chat.id})),()=>{const row=node('div','','conversation-item');const b=node('button','','conversation-open');b.type='button';b.append(node('span','','conversation-title'));const more=node('button','⋯','conversation-more');more.type='button';more.setAttribute('aria-label','管理对话');row.append(b,more);return row;},(b,chat)=>{
   const mode=chat.mode==='planning'?'任务规划':'普通对话',open=b.querySelector('.conversation-open'),more=b.querySelector('.conversation-more');open.querySelector('span').textContent=chat.title;open.title=chat.title+'\n'+mode;open.setAttribute('aria-label',chat.title+'，'+mode);open.onclick=()=>openConversation(chat.id).catch(e=>showError(e.message));more.title='重命名或删除：'+chat.title;more.onclick=()=>manageConversation(chat.id,chat.title);
  },'从新对话开始，记录会显示在这里');syncSidebarSelection();
 }catch(e){showError(e.message);}
}

async function newConversation(){
 const trigger=$('new-conversation');trigger.disabled=true;
 try{const chat=await api('/api/conversations',{mode:'chat',workspace:getWorkspaceFilter()||''});await openConversation(chat.id);}
 catch(e){showError(e.message);}finally{trigger.disabled=false;}
}
let managedConversation=null,confirmConversationDelete=false;
function manageConversation(id,title){managedConversation=id;confirmConversationDelete=false;$('conversation-manage-heading').textContent='管理对话';$('conversation-title-field').hidden=false;$('conversation-title-input').value=title;$('conversation-rename-save').hidden=false;$('conversation-delete').textContent='删除对话';showError('','conversation-manage-error');openDialog('conversation-manage-dialog');$('conversation-title-input').focus();$('conversation-title-input').select();}
$('conversation-manage-form').onsubmit=async event=>{
 event.preventDefault();if(confirmConversationDelete){$('conversation-delete').click();return;}const id=managedConversation,b=$('conversation-rename-save');b.disabled=true;
 try{const chat=await api(`/api/conversations/${id}/rename`,{title:$('conversation-title-input').value});if(currentConversation?.id===id){currentConversation.title=chat.title;$('chat-title').textContent=chat.title;}$('conversation-manage-dialog').close();await loadConversations();}
 catch(e){showError(e.message,'conversation-manage-error');}finally{b.disabled=false;}
};
$('conversation-delete').onclick=async()=>{
 if(!confirmConversationDelete){confirmConversationDelete=true;$('conversation-manage-heading').textContent='删除此对话？';$('conversation-title-field').hidden=true;$('conversation-title-input').required=false;$('conversation-rename-save').hidden=true;$('conversation-delete').textContent='确认删除';return;}
 const id=managedConversation,b=$('conversation-delete');b.disabled=true;
 try{await api(`/api/conversations/${id}/delete`,{});chatDrafts.delete(id);if(currentConversation?.id===id)showBoardView();$('conversation-manage-dialog').close();await loadConversations();}
 catch(e){showError(e.message,'conversation-manage-error');}finally{b.disabled=false;}
};
$('conversation-manage-dialog').addEventListener('close',()=>{$('conversation-title-input').required=true;});

async function openConversation(id){
 const token=session.begin(id);
 if(currentConversation)chatDrafts.set(currentConversation.id,$('chat-input').value);
 chatSource?.close();chatSource=null;chatCursor=0;const chat=await api('/api/conversations/'+id);if(!session.isCurrent(token))return;currentConversation=chat;chatCursor=chat.cursor||0;showChatView();selectChatView('conversation',false);$('chat-input').value=chatDrafts.get(id)||'';showError('','chat-error');options($('chat-workspace'),[['','未选择工作区'],...getConfig().workspaces.map(w=>[w,w])],chat.workspace);renderConversation();connectConversation();await loadConversations();$('chat-input').focus();
}
function renderConversation(){
 renderComposer();
 const chat=currentConversation;if(!chat)return;renderContextState($('chat-context'),chat.context);renderDocuments();$('chat-workspace').disabled=chat.mode==='planning'&&Boolean(chat.task_id);enhanceWorkspace($('chat-workspace'));$('chat-title').textContent=chat.title;$('chat-mode-label').textContent=chat.mode==='planning'?'任务规划':'普通对话';const messages=$('chat-messages'),nearBottom=messages.scrollHeight-messages.scrollTop-messages.clientHeight<100;
 messages.replaceChildren();for(const message of chat.messages){if(!message.text&&message.status==='completed')continue;const item=node('article','',message.role==='user'?'chat-message chat-user':'chat-message chat-assistant');item.append(node('small',message.role==='user'?'你':'Pi'),message.role==='user'?node('div',message.text):markdownNode(message.text||(message.status==='streaming'?'正在思考…':'')));if(message.role==='assistant'&&message.status==='completed'&&message.text){const actions=node('div','','message-actions');actions.append(button('复制',async()=>{await navigator.clipboard.writeText(message.text);}),button('下载 .md',()=>downloadMarkdown(message.text)));item.append(actions);}if(message.status==='interrupted')item.append(node('small','本次输出中断'));messages.append(item);}
 if(!chat.messages.length){const welcome=node('section','','chat-empty');welcome.append(node('span','✳','welcome-symbol'),node('h2','有什么想一起完成的？'),node('p',chat.mode==='planning'?'描述目标，让 Agent 制定计划、执行任务并追踪结果。':'从一个想法、一份资料，或一个待解决的问题开始。'));const suggestions=node('div','','chat-suggestions');for(const prompt of ['梳理这个项目的架构与改进方向','帮我分析资料并整理关键结论','制定一份可执行的任务计划'])suggestions.append(button(prompt,()=>{$('chat-input').value=prompt;chatDrafts.set(chat.id,prompt);updateComposerSend();$('chat-input').focus();}));welcome.append(suggestions);messages.append(welcome);}
 if(!messages.hidden){if(nearBottom)messages.scrollTop=messages.scrollHeight;updateChatLatest();}
 renderProgress(chat);$('chat-progress').hidden=document.querySelector('.chat-stage').dataset.view!=='conversation';$('chat-document-count').textContent=chat.documents?.length?' '+chat.documents.length:'';$('chat-document-empty').hidden=Boolean(chat.documents?.length);renderChatTrace(chat);
 $('chat-task-open').hidden=!chat.task_id;$('chat-cancel').hidden=!['running','attention'].includes(chat.status);updateComposerSend();$('chat-input').disabled=false;
 if(chat.task_id&&chat.mode==='planning')loadChatPending(chat.id,chat.task_id).catch(e=>showError(e.message,'chat-error'));else $('chat-pending').replaceChildren();
}
function renderProgress(chat){
 const state=buildProgress(chat),target=$('chat-progress'),wasOpen=target.querySelector('details')?.open;
 $('chat-status').textContent=state.label;$('chat-status').dataset.phase=state.phase;
 target.replaceChildren();if(!state.history.length&&!state.steps.length)return;
 if(state.steps.length){const track=node('ol','','progress-plan');for(const step of state.steps){const item=node('li','','progress-step');item.dataset.status=step.status;item.append(node('span',step.status==='completed'?'✓':step.status==='running'?'●':step.status==='failed'?'!':'○'),node('span',step.title));track.append(item);}target.append(node('small',`计划进度 ${state.completed} / ${state.steps.length}`),track);}
 if(state.activeAgents.length&&state.phase==='running')target.append(node('small','执行中：'+state.activeAgents.join('、'),'progress-agents'));
 const details=node('details'),summary=node('summary',`运行过程 · ${state.history.length} 条记录`),list=node('ol','','progress-history');details.open=Boolean(wasOpen);
 for(const entry of state.history){const li=node('li');if(entry.error)li.className='progress-error';const time=entry.created?new Date(entry.created*1000).toLocaleTimeString('zh-CN',{hour12:false}):'';li.append(node('time',time),node('span',entry.text));list.append(li);}details.append(summary,list);target.append(details);
}
const traceLabels={'request.started':'开始新一轮请求','session.connected':'Pi 会话已连接','agent.started':'Agent 开始执行','agent.status':'Agent 状态更新','agent.completed':'Agent 执行结束','message.started':'开始组织回答','answer.started':'开始生成回答','status':'任务状态更新',...kinds};
function renderChatTrace(chat){
 const panel=$('chat-trace');if(panel.hidden)return;
 const rows=buildTrace(chat.execution,chat.status),filter=$('trace-agent'),previous=filter.value;
 const roles=new Map([['main','主 Agent']]);for(const row of rows)roles.set(row.agent,row.event.role||roles.get(row.agent)||row.agent);
 options(filter,[['all','全部 Agent'],...[...roles]],previous);
 const type=$('trace-kind').value,visible=rows.filter(r=>(filter.value==='all'||r.agent===filter.value)&&(type==='all'||type==='tools'&&r.event.kind.startsWith('tool.')||type==='failed'&&['failed','interrupted'].includes(r.status)));
 const calls=rows.filter(r=>r.event.kind==='tool.started');$('trace-summary').textContent=`${roles.size} 个 Agent · ${calls.length} 次调用 · ${calls.filter(r=>r.status==='failed').length} 次失败`;
 const target=$('trace-entries'),opened=new Set([...target.querySelectorAll('details[open]')].map(n=>n.dataset.key)),scroll=target.scrollTop;target.replaceChildren();
 for(const row of visible){const e=row.event,item=node('details','','trace-entry');item.dataset.key=chat.id+':'+row.key;item.dataset.status=row.status;item.open=opened.has(item.dataset.key);const head=node('summary'),copy=node('div','','trace-entry-copy');copy.append(node('strong',e.name||traceLabels[e.kind]||e.kind),node('small',`${roles.get(row.agent)} · ${row.start?new Date(row.start*1000).toLocaleTimeString('zh-CN',{hour12:false}):'时间未记录'}`));head.append(node('span',row.status==='completed'?'✓':row.status==='failed'?'!':row.status==='running'?'◌':'·','trace-dot'),copy);if(e.kind==='tool.started')head.append(node('span',{completed:'完成',failed:'失败',running:'运行中',interrupted:'中断'}[row.status],'trace-badge'));item.append(head);const body=node('div','','trace-body');if(row.status==='completed'&&Number.isFinite(row.end-row.start))body.append(node('p',`耗时 ${Math.max(0,row.end-row.start).toFixed(2)} 秒`));if(e.status)body.append(node('p',labels[e.status]||e.status));body.append(node('pre',JSON.stringify(e,null,2)));if(row.result)body.append(node('pre',JSON.stringify(row.result,null,2)));item.append(body);target.append(item);}
 if(!visible.length)target.append(node('p',rows.length?'没有符合筛选条件的事件':'发送消息后，实际运行过程将显示在这里。','trace-description'));target.scrollTop=scroll;
}
const chatReadPositions=new Map();
function updateChatLatest(){
 const messages=$('chat-messages'),following=messages.scrollHeight-messages.scrollTop-messages.clientHeight<100;
 if(!messages.hidden&&currentConversation)chatReadPositions.set(currentConversation.id,{top:messages.scrollTop,following});
 $('chat-latest').hidden=messages.hidden||following||!currentConversation?.messages.length;
}
$('chat-messages').addEventListener('scroll',updateChatLatest);
$('chat-latest').onclick=()=>{const messages=$('chat-messages');messages.scrollTop=messages.scrollHeight;updateChatLatest();};
function selectChatView(view,preserve=true){
 if(preserve&&!$('chat-messages').hidden)updateChatLatest();
 const stage=document.querySelector('.chat-stage');stage.dataset.view=view;
 for(const tab of document.querySelectorAll('[data-chat-view]'))tab.setAttribute('aria-pressed',String(tab.dataset.chatView===view));
 $('chat-messages').hidden=view!=='conversation';$('chat-document-view').hidden=view!=='documents';
 const docs=$('chat-documents');if(view==='documents')$('chat-document-view').append(docs);else $('chat-pending').before(docs);
 $('chat-document-empty').hidden=Boolean(currentConversation?.documents?.length);
 $('chat-progress').hidden=view!=='conversation';
 $('chat-trace').hidden=view!=='trace';$('chat-trace-toggle').setAttribute('aria-expanded',String(view==='trace'));
 if(currentConversation)renderChatTrace(currentConversation);
 if(view==='conversation'){const saved=chatReadPositions.get(currentConversation?.id);$('chat-messages').scrollTop=saved?(saved.following?$('chat-messages').scrollHeight:saved.top):$('chat-messages').scrollHeight;}
 updateChatLatest();
}
for(const tab of document.querySelectorAll('[data-chat-view]'))tab.onclick=()=>selectChatView(tab.dataset.chatView);
$('chat-add-document').onclick=()=>$('paper-upload').click();
function toggleChatTrace(open){selectChatView(open?'trace':'conversation');}
$('chat-trace-toggle').onclick=()=>toggleChatTrace($('chat-trace').hidden);
$('chat-trace-close').onclick=()=>{toggleChatTrace(false);document.querySelector('[data-chat-view=conversation]').focus();};
$('trace-agent').onchange=$('trace-kind').onchange=()=>{if(currentConversation)renderChatTrace(currentConversation);};
async function loadChatPending(chatId,taskId){const data=await api('/api/runs/'+taskId);if(currentConversation?.id!==chatId)return;const target=$('chat-pending');target.replaceChildren();for(const input of (data.inputs||[]).filter(i=>i.status==='pending')){const section=node('section','','chat-question');section.append(node('strong','需要补充信息'),node('p',input.data.question));section.append(button('回答这个问题',()=>selectTask(taskId),true));target.append(section);}if(data.approvals?.some(a=>a.status==='pending'))target.append(button('处理工具审批',()=>selectTask(taskId),true));}
function connectConversation(){if(!currentConversation||chatSource)return;const id=currentConversation.id,token=session.token,stream=new EventSource(`/api/conversations/${id}/events?after=${chatCursor}`);chatSource=stream;const release=session.own(()=>stream.close());stream.onmessage=event=>{if(currentConversation?.id!==id||chatSource!==stream||!session.isCurrent(token))return;const record=JSON.parse(event.data);if(record.id<=chatCursor)return;chatCursor=record.id;const d=record.data;if(record.kind==='message.delta'){const message=currentConversation.messages.find(m=>m.id===d.id);if(message){message.text+=d.delta;message.status=d.status;}}if(record.kind==='message'){const index=currentConversation.messages.findIndex(m=>m.id===d.id);if(index<0)currentConversation.messages.push(d);else currentConversation.messages[index]={...currentConversation.messages[index],...d};}if(record.kind==='execution'){currentConversation.execution=[...(currentConversation.execution||[]),{...d,created:record.created}].slice(-150);}if(record.kind.startsWith('context.'))currentConversation.context=d;if(record.kind==='status')currentConversation.status=d.status;if(record.kind==='title.changed'){currentConversation.title=d.title;loadConversations();}if(record.kind==='mode.changed'){currentConversation.mode=d.mode;currentConversation.workspace=d.workspace;options($('chat-workspace'),[['','未选择工作区'],...getConfig().workspaces.map(w=>[w,w])],d.workspace);}if(record.kind==='task.created'){currentConversation.task_id=d.task_id;scheduleRefresh();}if(record.kind==='task.status')scheduleRefresh();if(record.kind==='error')showError(d.text,'chat-error');renderConversation();};stream.addEventListener('caught-up',()=>{if(chatSource!==stream||!session.isCurrent(token))return;stream.close();release();if(chatSource===stream)chatSource=null;loadConversations();});stream.onerror=()=>{if(chatSource===stream&&session.isCurrent(token)&&currentConversation?.id===id)$('chat-status').textContent='连接中断，正在重连…';};}
let composerModeBusy=false;
function updateComposerSend(){const chat=currentConversation;$('chat-send').disabled=!chat||composerModeBusy||chat.status==='running'||!$('chat-input').value.trim();}
function resizeComposer(){const input=$('chat-input');input.style.height='auto';input.style.height=Math.min(220,Math.max(62,input.scrollHeight))+'px';}
function renderComposer(){
 const chat=currentConversation;if(!chat)return;
 $('chat-composer-mode').textContent=chat.mode==='planning'?'任务规划':'普通对话';
 const busy=chat.status==='running'||composerModeBusy,summary=$('chat-mode-picker').querySelector('summary');summary.setAttribute('aria-disabled',String(busy));
 for(const b of document.querySelectorAll('[data-chat-mode]')){b.setAttribute('aria-pressed',String(b.dataset.chatMode===chat.mode));b.disabled=busy;}
 $('chat-input').placeholder=chat.mode==='planning'?'描述目标，让 Agent 帮你推进…':'和 Pi 一起工作…';
 resizeComposer();
}
for(const b of document.querySelectorAll('[data-chat-mode]'))b.onclick=async()=>{
 const chat=currentConversation;if(!chat||composerModeBusy)return;
 $('chat-mode-picker').open=false;if(chat.mode===b.dataset.chatMode)return;
 const id=chat.id;composerModeBusy=true;renderComposer();updateComposerSend();
 try{const updated=await api(`/api/conversations/${id}/mode`,{mode:b.dataset.chatMode});if(currentConversation?.id!==id)return;currentConversation=updated;chatCursor=Math.max(chatCursor,updated.cursor||0);options($('chat-workspace'),[['','未选择工作区'],...getConfig().workspaces.map(w=>[w,w])],updated.workspace);renderConversation();showError('','chat-error');await loadConversations();}
 catch(e){if(currentConversation?.id===id)showError(e.message,'chat-error');}
 finally{composerModeBusy=false;renderComposer();updateComposerSend();if(currentConversation?.id===id)$('chat-input').focus();}
};
$('chat-mode-picker').querySelector('summary').onclick=event=>{if(currentConversation?.status==='running'||composerModeBusy)event.preventDefault();};
$('chat-mode-picker').addEventListener('keydown',event=>{if(event.key==='Escape'){$('chat-mode-picker').open=false;$('chat-mode-picker').querySelector('summary').focus();}});
document.addEventListener('pointerdown',event=>{if(!$('chat-mode-picker').contains(event.target))$('chat-mode-picker').open=false;});
$('chat-input').oninput=()=>{resizeComposer();updateComposerSend();};
$('chat-form').onsubmit=async event=>{event.preventDefault();if(!currentConversation||$('chat-send').disabled)return;const id=currentConversation.id,token=session.token;const b=$('chat-send');b.disabled=true;try{await api(`/api/conversations/${id}/messages`,{prompt:$('chat-input').value,workspace:$('chat-workspace').value});if(!session.isCurrent(token))return;$('chat-input').value='';chatDrafts.delete(id);const updated=await api('/api/conversations/'+id);if(!session.isCurrent(token))return;currentConversation=updated;chatCursor=Math.max(chatCursor,currentConversation.cursor||0);renderConversation();connectConversation();await loadConversations();}catch(e){showError(e.message,'chat-error');}finally{if(currentConversation?.id===id)renderConversation();}};
$('chat-task-open').onclick=()=>{if(currentConversation?.task_id)selectTask(currentConversation.task_id).catch(e=>showError(e.message,'chat-error'));};
$('chat-cancel').onclick=async()=>{try{await api(`/api/conversations/${currentConversation.id}/cancel`,{});connectConversation();}catch(e){showError(e.message,'chat-error');}};
$('chat-back').onclick=showBoardView;
$('chat-input').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey))$('chat-form').requestSubmit($('chat-send'));};
let pollTimer=setInterval(async()=>{if(!currentConversation)return;const id=currentConversation.id,token=session.token;try{const chat=await api('/api/conversations/'+id);if(currentConversation?.id!==id||!session.isCurrent(token)||(chat.cursor||0)<chatCursor)return;currentConversation=chat;chatCursor=Math.max(chatCursor,chat.cursor||0);renderConversation();if(chat.status==='running')connectConversation();}catch(e){if(currentConversation?.id!==id||!session.isCurrent(token))return;if(e.status===404){chatDrafts.delete(id);showBoardView();await loadConversations();showError('此对话已被删除，已返回任务中心');}else showError(e.message,'chat-error');}},5000);

const documentLabels={ready:'可阅读',processing:'解析中',needs_parser:'需要 PDF 解析器',no_text:'未提取到文字',failed:'解析失败',interrupted:'解析中断'};
function renderDocuments(){const target=$('chat-documents');target.replaceChildren();for(const doc of currentConversation?.documents||[]){const item=node('section','','document-card');const copy=node('div');copy.append(node('strong',doc.name),node('small',`${documentLabels[doc.status]||doc.status} · ${(doc.size/1024).toFixed(0)} KB${doc.pages?' · '+doc.pages+(doc.kind==='.pdf'?' 页':' 块'):''}`));if(doc.parser&&doc.parser!=='legacy')copy.append(node('small','解析：'+doc.parser));for(const warning of doc.warnings||[])copy.append(node('span',warning,'document-warning'));if(doc.status!=='ready')copy.append(node('small','此文件暂不可读；仍可发送消息讨论其他资料。'));if(doc.error)copy.append(node('span',doc.error,'document-error'));const actions=node('div','','document-actions');actions.append(button('原文件',()=>{const a=node('a');a.href=`/api/conversations/${currentConversation.id}/documents/${doc.id}/download`;a.download=doc.name;a.click();}));if(doc.status==='ready')actions.append(button('查看原文',()=>openPaper({id:doc.id,page:1})));if(['needs_parser','failed','interrupted','no_text'].includes(doc.status)&&['.pdf','.docx'].includes(doc.kind))actions.append(button('重新解析',async()=>{await api(`/api/conversations/${currentConversation.id}/documents/${doc.id}/retry`,{});await reloadDocuments();}));actions.append(button('移除',async()=>{await api(`/api/conversations/${currentConversation.id}/documents/${doc.id}/archive`,{});await reloadDocuments();}));item.append(node('span',doc.kind.slice(1).toUpperCase(),'document-type'),copy,actions);target.append(item);}}
async function reloadDocuments(){if(!currentConversation)return;const id=currentConversation.id;const data=await api('/api/conversations/'+id);if(currentConversation?.id!==id)return;currentConversation=data;chatCursor=Math.max(chatCursor,data.cursor||0);renderConversation();}
$('paper-upload-open').onclick=()=>$('paper-upload').click();
$('paper-upload').onchange=async event=>{const input=event.target,files=[...input.files];if(!currentConversation||!files.length)return;const id=currentConversation.id;const upload=$('paper-upload-open');upload.disabled=true;try{for(const file of files){if(file.size>10*1024*1024)throw new Error('每个附件最多 10 MiB：'+file.name);upload.setAttribute('aria-busy','true');upload.title='上传中…';const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('读取附件失败'));reader.readAsDataURL(file);});await api(`/api/conversations/${id}/documents`,{name:file.name,content_base64:content});}await reloadDocuments();showError('','chat-error');}catch(e){showError(e.message,'chat-error');await reloadDocuments();}finally{upload.disabled=false;upload.removeAttribute('aria-busy');upload.title='上传论文或资料';input.value='';}};
let paperState=null;
async function openPaper(reference){const scope=$('detail-dialog').open&&getSelectedTask()?`/api/runs/${getSelectedTask()}`:currentConversation?`/api/conversations/${currentConversation.id}`:null;if(!scope)throw new Error('请先打开附件所属对话或任务');paperState={...reference,scope,offset:0};showError('','paper-error');$('paper-search-results').replaceChildren();$('paper-search-query').value='';await loadPaperPage();const state=paperState;const outline=await api(`${scope}/documents/${reference.id}/outline`);if(paperState!==state)return;options($('paper-section'),outline.entries.map(e=>[String(e.page),`${outline.unit==='page'?'页':'块'} ${e.page} · ${e.title}`]),String(state.page));state.pageOrder=outline.entries.map(e=>e.page);updatePaperButtons();openDialog('paper-dialog');}
function updatePaperButtons(){if(!paperState)return;const order=paperState.pageOrder,index=order?.indexOf(paperState.page);$('paper-prev').disabled=order?index<=0:paperState.page<=1;$('paper-next').disabled=order?index>=order.length-1:paperState.page>=paperState.pages;}
async function loadPaperPage(append=false){const state=paperState;if(!state)return;const data=await api(`${state.scope}/documents/${state.id}?page=${state.page}&offset=${state.offset}`);if(paperState!==state)return;state.pages=data.pages;state.nextOffset=data.next_offset;$('paper-section').value=String(data.page);enhanceSelect($('paper-section'));$('paper-title').textContent=data.name;$('paper-page').textContent=`${data.unit==='page'?'第':'文本块'} ${data.page} / ${data.pages}${data.unit==='page'?' 页':''}${data.parser&&data.parser!=='legacy'?' · '+data.parser:''}${data.scope==='selected_snapshot'?' · 已选快照':''}`;$('paper-text').textContent=(append?$('paper-text').textContent:'')+data.text;updatePaperButtons();$('paper-more').hidden=state.nextOffset===null;}
for(const [id,delta] of [['paper-prev',-1],['paper-next',1]])$(id).onclick=async()=>{if(!paperState)return;paperState.page=paperState.pageOrder?.[paperState.pageOrder.indexOf(paperState.page)+delta]??(paperState.page+delta);paperState.offset=0;try{await loadPaperPage();}catch(e){showError(e.message,'paper-error');}};
$('paper-section').onchange=async()=>{if(!paperState)return;paperState.page=Number($('paper-section').value);paperState.offset=0;try{await loadPaperPage();}catch(e){showError(e.message,'paper-error');}};
$('paper-search-form').onsubmit=async event=>{event.preventDefault();const state=paperState;if(!state)return;try{const result=await api(`${state.scope}/documents/${state.id}/search?q=${encodeURIComponent($('paper-search-query').value)}`);if(paperState!==state)return;const target=$('paper-search-results');target.replaceChildren();for(const match of result.matches)target.append(button(`${match.page} · ${match.excerpt}`,async()=>{state.page=match.page;state.offset=match.offset;await loadPaperPage();}));if(!result.matches.length)target.append(node('p','没有找到原文匹配。'));}catch(e){showError(e.message,'paper-error');}};
$('paper-more').onclick=async()=>{if(paperState?.nextOffset==null)return;paperState.offset=paperState.nextOffset;try{await loadPaperPage(true);}catch(e){showError(e.message,'paper-error');}};

return {newConversation,showBoardView,openConversation,openPaper,loadConversations,get current(){return currentConversation;},dispose(){session.close();chatSource?.close();chatSource=null;clearInterval(pollTimer);conversationListVersion++;currentConversation=null;}};
}
