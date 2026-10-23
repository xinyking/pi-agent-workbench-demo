import {createViewSession} from './view-session.mjs';
import {reconcileSidebar} from './sidebar.mjs';
const $=id=>document.getElementById(id);
export function createBoardController({api,node,labels,showError,getConfig,getConversation,selectTask,showBoardView,onTasks,loadUsage}){
const session=createViewSession();session.begin('board');
let tasks=[],workspaceFilter='',searchQuery='',boardTimer=null,refreshBusy=false,refreshAgain=false;
function scheduleRefresh(){clearTimeout(boardTimer);boardTimer=setTimeout(refreshBoard,180);}
function workspaceName(path){return path?path.replace(/\/$/,'').split('/').pop():'未设置工作区';}
function updatedLabel(timestamp){const delta=Math.max(0,Math.floor((Date.now()/1000-timestamp)/60));if(delta<1)return '刚刚更新';if(delta<60)return `${delta} 分钟前`;if(delta<1440)return `${Math.floor(delta/60)} 小时前`;return new Date(timestamp*1000).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});}
function card(task){
 const c=node('article','','task');c.dataset.status=task.status;c.tabIndex=0;c.setAttribute('aria-label',`${labels[task.status]}：${task.prompt}`);c.onclick=()=>selectTask(task.id).catch(e=>showError(e.message));c.onkeydown=e=>{if(e.target===c&&(e.key==='Enter'||e.key===' ')){e.preventDefault();c.click();}};
 c.append(node('h3',task.prompt));const path=node('span',workspaceName(task.workspace),'workspace-path');path.title=task.workspace||'旧版任务未设置工作区';c.append(path);
 let summary=task.current_step||task.summary;
 if(task.bucket==='attention')summary=task.reason||summary;
 if(task.bucket==='done')summary='';
 if(summary){const cls='card-summary'+(task.bucket==='attention'?' is-attention':'')+(task.status==='waiting_approval'?' is-approval':task.status==='waiting_input'?' is-input':'');c.append(node('div',summary,cls));}
 if(task.plan?.length){const complete=task.plan.filter(step=>step.status==='completed').length;const progress=node('div','','card-progress'),label=node('div','','progress-label'),bar=node('progress');label.append(node('span','进度'),node('span',`${complete}/${task.plan.length}`));bar.max=task.plan.length;bar.value=complete;bar.setAttribute('aria-label',`${task.prompt}，已完成 ${complete}/${task.plan.length} 步`);progress.append(label,bar);c.append(progress);}
 const footer=node('div','','card-footer');if(task.agent_count>0)footer.append(node('span',`子 Agent ${task.agents_completed||0}/${task.agent_count}`,'file-count'));if(task.file_change_count>0)footer.append(node('span',`${task.file_change_count} 个文件`,'file-count'));const time=node('time',updatedLabel(task.updated||task.created));time.dateTime=new Date((task.updated||task.created)*1000).toISOString();time.title=new Date((task.updated||task.created)*1000).toLocaleString();footer.append(time);c.append(footer,node('span',labels[task.status]||task.status,'card-status'));return c;
}
function filteredTasks(){const query=searchQuery.trim().toLocaleLowerCase();return tasks.filter(task=>(!workspaceFilter||task.workspace===workspaceFilter)&&(!query||[task.prompt,task.workspace,task.reason,task.current_step,task.summary,task.model].filter(Boolean).join(' ').toLocaleLowerCase().includes(query)));}
function renderBoard(){
 const visible=filteredTasks();document.querySelector('.page-title h1').textContent=workspaceFilter?workspaceName(workspaceFilter):'任务中心';document.querySelector('.page-title p').textContent=workspaceFilter?'当前工作区的任务与执行进度':'从任务看板观察 Agent 协作与完整执行过程';
 for(const bucket of ['pending','running','attention','done']){const lane=$('lane-'+bucket),scroll=lane.scrollTop;lane.replaceChildren();const matching=visible.filter(task=>task.bucket===bucket);$('count-'+bucket).textContent=matching.length;if(!matching.length)lane.append(node('div',workspaceFilter||searchQuery?'无匹配任务':'暂无任务','lane-empty'));for(const task of matching)lane.append(card(task));lane.scrollTop=scroll;}
 document.querySelector('.archive').hidden=true;$('archive').replaceChildren();
 $('filter-info').hidden=!workspaceFilter&&!searchQuery;$('filter-info').textContent=[workspaceFilter?'工作区：'+workspaceName(workspaceFilter):'',searchQuery?'搜索：'+searchQuery:'',`共 ${visible.length} 项任务`].filter(Boolean).join(' · ');renderWorkspaceSidebar();if(getConversation())$('filter-info').hidden=true;
}
function renderWorkspaceSidebar(){
 const paths=[...new Set([...getConfig().workspaces,...tasks.map(t=>t.workspace).filter(Boolean)])];
 reconcileSidebar($('sidebar-workspaces'),paths.map(path=>({key:path,path})),()=>{
  const b=node('button','','workspace-item');b.type='button';
  const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('class','icon');icon.setAttribute('aria-hidden','true');const shape=document.createElementNS('http://www.w3.org/2000/svg','path');shape.setAttribute('d','M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z');icon.append(shape);b.append(icon,node('span','','workspace-item-name'));return b;
 },(b,{path})=>{
  const count=tasks.filter(t=>t.workspace===path).length;
  b.title=`${path}\n${count} 项任务 · 点击查看项目看板`;b.setAttribute('aria-label',workspaceName(path)+'，查看工作区任务');b.querySelector('span').textContent=workspaceName(path);
  b.onclick=()=>{workspaceFilter=path;searchQuery='';$('task-search').value='';showBoardView();renderBoard();};
 },'添加项目目录，在这里查看对应任务');
 syncSidebarSelection();
}
function syncSidebarSelection(){
 const board=!getConversation()&&!workspaceFilter;$('board-home').classList.toggle('active',board);if(board)$('board-home').setAttribute('aria-current','page');else $('board-home').removeAttribute('aria-current');
 for(const b of $('sidebar-workspaces').querySelectorAll('button')){const selected=!getConversation()&&workspaceFilter===b.dataset.sidebarKey;b.setAttribute('aria-pressed',String(selected));if(selected)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}
 for(const row of $('conversation-list').querySelectorAll('.conversation-item')){const b=row.querySelector('.conversation-open'),selected=getConversation()?.id===row.dataset.sidebarKey;row.classList.toggle('is-current',selected);b.setAttribute('aria-pressed',String(selected));if(selected)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');}
}
function setConnection(connected){const n=$('connection');n.dataset.state=connected?'connected':'reconnecting';n.textContent=connected?'工作台已连接':'正在重连…';n.title=connected?'看板事件流连接正常；Pi 运行环境和用量可在设置中查看':'暂时无法连接工作台，正在自动重试';}

async function refreshBoard(){
 if(refreshBusy){refreshAgain=true;return;}refreshBusy=true;
 try{const token=session.token;const data=await api('/api/board');if(!session.isCurrent(token))return;tasks=data.tasks.map(task=>task.status==='cancelled'?{...task,bucket:'pending'}:task);renderBoard();showError('');await onTasks(tasks);}
 catch(error){showError(error.message);}finally{refreshBusy=false;if(refreshAgain){refreshAgain=false;scheduleRefresh();}}
}
$('board-home').onclick=()=>{showBoardView();workspaceFilter='';searchQuery='';$('task-search').value='';renderBoard();};
$('search-toggle').onclick=()=>{const opened=$('search-tools').hidden;$('search-tools').hidden=!opened;$('search-toggle').setAttribute('aria-expanded',String(opened));if(opened)$('task-search').focus();};
$('task-search').oninput=()=>{searchQuery=$('task-search').value;renderBoard();};
$('task-search').onkeydown=event=>{if(event.key==='Escape'){searchQuery='';$('task-search').value='';renderBoard();}};
$('clear-filters').onclick=()=>{workspaceFilter='';searchQuery='';$('task-search').value='';renderBoard();};
function clearFilters(){workspaceFilter='';searchQuery='';$('task-search').value='';renderBoard();}
let boardStream=null,pollTimer=null;
function connectBoard(){
 if(boardStream)return;
 const token=session.token,stream=new EventSource('/api/board/events');boardStream=stream;session.own(()=>stream.close());stream.onmessage=()=>{if(boardStream!==stream||!session.isCurrent(token))return;setConnection(true);scheduleRefresh();loadUsage();};stream.onerror=()=>{if(boardStream===stream&&session.isCurrent(token))setConnection(false);};
}
function start(){if(pollTimer)return;if(!session.token)session.begin('board');refreshBoard();pollTimer=setInterval(refreshBoard,10000);if(document.readyState==='complete')connectBoard();else window.addEventListener('load',connectBoard,{once:true});}
function dispose(){session.close();clearInterval(pollTimer);clearTimeout(boardTimer);pollTimer=null;boardStream?.close();boardStream=null;window.removeEventListener('load',connectBoard);}
return {start,dispose,refreshBoard,scheduleRefresh,renderBoard,renderWorkspaceSidebar,syncSidebarSelection,setConnection,clearFilters,get tasks(){return tasks;},get workspaceFilter(){return workspaceFilter;}};
}
