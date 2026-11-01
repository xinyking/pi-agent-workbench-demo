const tools={workbench_document:'读取资料',workbench_delegate:'分派子任务',workbench_plan:'制定执行计划',workbench_ask_user:'请求补充信息',workbench_finish:'整理任务结果',structured_output:'整理阅读笔记与交付结果',read:'读取文件',grep:'检索内容',find:'查找文件',ls:'查看目录',write:'写入文件',edit:'修改文件',bash:'执行命令',workbench_create_task:'创建任务',workbench_create_agent:'创建 Agent',workbench_context:'回看对话上下文'};
export function buildProgress(chat){
 const events=chat.execution||[];let start=0;
 for(let i=0;i<events.length;i++)if(events[i].kind==='request.started')start=i;
 const history=[],agents=new Map();let steps=[],label='正在连接 Pi',phase='running';
 for(const e of events.slice(start)){
  let text='';
  if(e.kind==='request.started')text='准备请求';
  if(e.kind==='session.connected')text='已连接 Pi，等待模型响应';
  if(e.kind==='agent.started'){agents.set(e.agent_id,{role:e.role,status:'running'});text=e.agent_id==='main'?'主 Agent 开始执行':`${e.role||'子 Agent'} 开始执行`;}
  if(e.kind==='agent.status'&&agents.has(e.agent_id))agents.get(e.agent_id).status=e.status;
  if(e.kind==='agent.completed'){if(agents.has(e.agent_id))agents.get(e.agent_id).status=e.status;text=e.agent_id==='main'?'主 Agent 已结束':`${agents.get(e.agent_id)?.role||'子 Agent'} ${e.status==='completed'?'已完成':'已结束'}`;}
  if(e.kind==='message.started')text='正在组织回答';
  if(e.kind==='answer.started')text='正在生成回答';
  if(e.kind==='tool.started'){
   const a=e.arguments||{};text=tools[e.name]||`调用工具：${e.name}`;
   if(e.name==='workbench_document'){
    const d=(chat.documents||[]).find(d=>d.id===a.document_id);
    text=a.action==='list'?'查看附件目录':`读取${d?'《'+d.name+'》':'文档'} · 第 ${a.page||1} ${d?.kind==='pdf'||d?.name?.toLowerCase().endsWith('.pdf')?'页':'块'}`;
   }else if(a.path)text+=` · ${a.path}`;
  }
  if(e.kind==='tool.completed')text=e.is_error?`${tools[e.name]||e.name}遇到问题，等待 Agent 处理`:`${tools[e.name]||e.name}完成，正在继续`;
  if(e.kind==='file.changed')text='文件已更新'+(e.path?' · '+e.path:'');
  if(e.kind==='plan.updated'){steps=e.steps||[];const running=steps.filter(s=>s.status==='running');text=running.length?`正在执行：${running.map(s=>s.title).join('、')}`:'执行计划已更新';}
  if(text){label=text;const previous=history.at(-1);if(previous?.text!==text)history.push({text,created:e.created,error:Boolean(e.is_error)});}
 }
 if(chat.status==='attention'){label='需要你处理：补充信息、审批或检查任务问题';phase='attention';}
 else if(chat.status==='failed'){label='执行失败，请查看错误信息';phase='failed';}
 else if(chat.status==='interrupted'){label='执行已中断，可以继续';phase='interrupted';}
 else if(chat.status!=='running'){label=events.length?(chat.mode==='planning'?({'completed':'任务已完成','cancelled':'任务已停止','queued':'任务待开始'}[[...events].reverse().find(e=>e.kind==='status')?.status]||'本轮任务执行已结束'):'回答已完成'):'发送消息后将在这里显示实时进度';phase='idle';}
 return {label,phase,history:history.slice(-20),steps,completed:steps.filter(s=>s.status==='completed').length,activeAgents:[...agents.values()].filter(a=>a.status==='running').map(a=>a.role)};
}
