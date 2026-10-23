/** Test-only deterministic provider: exercises Pi's real loop without HTTP/fees. */
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
export default function(pi: any) {
  let turn = 0;
  pi.registerProvider('workbench-fixture', {
    baseUrl: 'http://127.0.0.1/never-contacted', apiKey: 'offline-test', api: 'workbench-fixture-api',
    models: [{ id:'fixture',name:'Offline fixture',reasoning:false,input:['text'],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:(process.env.WORKBENCH_REAL_TEST_LONG==='1'||process.env.WORKBENCH_REAL_TEST_MEMORY==='1')?200000:32000,maxTokens:process.env.WORKBENCH_REAL_TEST_LONG==='1'?16000:2000 }],
    streamSimple(model: any, context: any) {
      const stream = createAssistantMessageEventStream();
      queueMicrotask(() => {
        const step = turn++;
        const first = step === 0;
        const child = process.env.WORKBENCH_CHILD === '1';
        const asking = process.env.WORKBENCH_REAL_TEST_ASK === '1';
        const writing = process.env.WORKBENCH_REAL_TEST_WRITE === '1';
        let content: any = first ? [{type:'toolCall',id:'fixture-call',name:child?'read':asking?'workbench_ask_user':writing?'write':'workbench_delegate',arguments:child?{path:'example.txt'}:asking?{question:'请提供简历或工作经历'}:writing?{path:'created.txt',content:'from real Pi'}:{tasks:[{agent:process.env.WORKBENCH_REAL_TEST_CUSTOM==='1'?'task-general':'task-scout',task:'读取 example.txt 并报告内容'},{agent:'task-reviewer',task:'独立读取 example.txt 并检查'}]}}] : !child && step===1 ? [{type:'toolCall',id:'fixture-finish',name:'workbench_finish',arguments:{summary:'已完成测试任务'}}] : [{type:'text',text:child?'子 Agent 已读取文件':'主 Agent 已收集子任务结果'}];
        if(process.env.WORKBENCH_MODE==='chat') {
          if(first && process.env.WORKBENCH_REAL_TEST_CREATE_TASK==='1') content=[{type:'toolCall',id:'create-task',name:'workbench_create_task',arguments:{prompt:'基于对话制定求职计划'}}];
          else if(first && process.env.WORKBENCH_REAL_TEST_CREATE_AGENT==='1') content=[{type:'toolCall',id:'create-agent',name:'workbench_create_agent',arguments:{name:'interview-mentor',description:'模拟面试',prompt:'根据真实经历模拟面试',tools:['read']}}];
          else content=[{type:'text',text:'普通问答；历史用户消息数：'+context.messages.filter((m:any)=>m.role==='user').length}];
        } else if(first && !child && process.env.WORKBENCH_REAL_TEST_CONTEXT==='1') {
          const transcript=JSON.stringify(context.messages);
          const match=transcript.match(/context_id[^a-f0-9]+([a-f0-9]{32})/);
          content=[{type:'toolCall',id:'context-read',name:'workbench_context',arguments:{context_id:match?.[1]||'missing'}}];
        }
        if(first && !child && process.env.WORKBENCH_REAL_TEST_STRUCTURED==='1')content=[{type:'toolCall',id:'structured-report',name:'structured_output',arguments:{headline:'求职计划',summary:'制定了四周行动计划',actionItems:['第一周完善简历','第二周模拟面试']}}];
        if(!child && process.env.WORKBENCH_MODE!=='chat' && process.env.WORKBENCH_REAL_TEST_PLAN==='1') {
          const steps=[{id:'research',title:'调查资料',task:'读取 example.txt',agent:'researcher',depends_on:[]},{id:'review',title:'独立核验',task:'核验 example.txt',agent:'critic',depends_on:['research']}];
          content=step===0?[{type:'toolCall',id:'declare-plan',name:'workbench_plan',arguments:{steps}}]:step===1||step===2?[{type:'toolCall',id:'planned-delegate-'+step,name:'workbench_delegate',arguments:{tasks:[{step_id:steps[step-1].id,agent:steps[step-1].agent,task:steps[step-1].task}]}}]:step===3?[{type:'toolCall',id:'plan-finish',name:'workbench_finish',arguments:{summary:'已完成计划全部步骤'}}]:[{type:'text',text:'计划执行完成'}];
        }
        const compacting=JSON.stringify(context).includes('context summarization assistant');
        const userMessages=context.messages.filter((m:any)=>m.role==='user');
        const lastUser=JSON.stringify(userMessages.at(-1)||'');
        if(process.env.WORKBENCH_REAL_TEST_LONG==='1') {
          if(compacting)content=[{type:'text',text:'## Goal\n目标杭州求职。\n## Constraints\n不考虑外包，保留用户硬约束。\n## Progress\n已讨论背景资料，下一步核对求职计划。'}];
          else content=[{type:'text',text:lastUser.includes('[seed-long]')?'讨论背景资料。'.repeat(7000):'核对约束：'+(JSON.stringify(context.messages).includes('不考虑外包')?'不考虑外包':'约束未找到')}];
        }
        const highUsage=!compacting && ((process.env.WORKBENCH_REAL_TEST_LONG==='1' && lastUser.includes('[compact-now]')) || (process.env.WORKBENCH_REAL_TEST_MEMORY==='1' && step===4));
        if(!child && process.env.WORKBENCH_REAL_TEST_PAPER==='1') {
          const userText=context.messages.filter((m:any)=>m.role==='user').map((m:any)=>typeof m.content==='string'?m.content:m.content.filter((p:any)=>p.type==='text').map((p:any)=>p.text).join('\n')).join('\n');
          const documentId=userText.match(/"documents":\s*\[\s*\{\s*"id":\s*"([a-f0-9]{32})"/)?.[1]||'missing';
          const documentReads=context.messages.filter((m:any)=>m.role==='toolResult'&&m.toolName==='workbench_document').length;
          if(documentReads===0)content=[{type:'toolCall',id:'paper-outline',name:'workbench_document',arguments:{action:'outline',document_id:documentId}}];
          else if(documentReads===1)content=[{type:'toolCall',id:'paper-search',name:'workbench_document',arguments:{action:'search',document_id:documentId,query:'120'}}];
          else if(documentReads===2)content=[{type:'toolCall',id:'paper-read',name:'workbench_document',arguments:{action:'read',document_id:documentId,page:1}}];
          else {
            const result=context.messages.findLast((m:any)=>m.role==='toolResult'&&m.toolName==='workbench_document');
            const source=JSON.parse(result?.content?.find((p:any)=>p.type==='text')?.text||'{}');
            content=[{type:'toolCall',id:'paper-notes',name:'structured_output',arguments:{headline:'论文阅读笔记',summary:'# 核心证据\n\n'+source.text+'\n\n来源：'+source.citation,actionItems:['核对实验限制与复现条件']}}];
          }
        }
        if(process.env.WORKBENCH_REAL_TEST_MEMORY==='1') {
          if(compacting) content=[{type:'text',text:'已完成工具阅读。继续检查交付。此摘要故意不重复用户约束。'}];
          else {
            const texts=context.messages.map((m:any)=>typeof m.content==='string'?m.content:(m.content||[]).filter((p:any)=>p.type==='text').map((p:any)=>p.text).join('\n'));
            const injection=texts.findLast((t:string)=>t.startsWith('当前任务记忆（'));
            const memory=injection?JSON.parse(injection.slice(injection.indexOf('\n')+1)):{};
            const readResult=context.messages.findLast((m:any)=>m.role==='toolResult'&&m.toolName==='read');
            let offloaded:any={};try{offloaded=JSON.parse(readResult?.content?.[0]?.text||'{}');}catch{}
            const recovered=context.messages.some((m:any)=>m.role==='toolResult'&&m.toolName==='workbench_memory'&&JSON.stringify(m.content).includes('原始长工具资料'));
            const retained=(memory.user_memory||[]).some((m:any)=>m.text==='禁止联网，使用中文');
            const noted=(memory.agent_notes||[]).some((m:any)=>m.text==='已检查原始工具资料');
            content=step===0?[{type:'toolCall',id:'memory-large-read',name:'read',arguments:{path:'example.txt'}}]
              :step===1?[{type:'toolCall',id:'memory-recover',name:'workbench_memory',arguments:{action:'read_artifact',artifact_id:offloaded.artifact_id||'missing',offset:0}}]
              :step===2?[{type:'toolCall',id:'memory-note',name:'workbench_memory',arguments:{action:'save',revision:memory.revision||1,kind:'progress',text:'已检查原始工具资料'}}]
              :step===3?[{type:'toolCall',id:'memory-finish',name:'workbench_finish',arguments:{summary:retained&&noted&&recovered?'MEMORY_OK':'MEMORY_MISSING'}}]
              :[{type:'text',text:retained&&noted&&recovered?'MEMORY_OK':'MEMORY_MISSING'}];
          }
        }
        if(process.env.WORKBENCH_REAL_TEST_PRIVATE_MEMORY==='1' && !child && !compacting) {
          const texts=context.messages.map((m:any)=>typeof m.content==='string'?m.content:(m.content||[]).filter((p:any)=>p.type==='text').map((p:any)=>p.text).join('\n'));
          const injection=texts.findLast((t:string)=>t.startsWith('当前任务记忆（'));
          const memory=injection?JSON.parse(injection.slice(injection.indexOf('\n')+1)):{};
          const sourceResult=context.messages.findLast((m:any)=>m.role==='toolResult'&&m.toolName==='workbench_memory');
          let source:any={};try{source=JSON.parse(sourceResult?.content?.[0]?.text||'{}');}catch{}
          const expected=process.env.WORKBENCH_REAL_TEST_MEMORY_DISABLED==='1';
          const preference=(memory.long_term_memory||[]).some((m:any)=>m.text==='以后回答都用中文');
          const constrained=(memory.user_memory||[]).some((m:any)=>m.text==='预算不超过200元'&&m.source.type==='extracted_user');
          if(expected)content=step===0?[{type:'toolCall',id:'disabled-read',name:'workbench_memory',arguments:{action:'read'}}]
            :step===1?[{type:'toolCall',id:'disabled-finish',name:'workbench_finish',arguments:{summary:!injection&&source.enabled===false?'MEMORY_DISABLED_OK':'MEMORY_DISABLED_BAD'}}]
            :[{type:'text',text:'已检查记忆开关'}];
          else content=step===0?[{type:'toolCall',id:'source-list',name:'workbench_memory',arguments:{action:'sources'}}]
            :step===1?[{type:'toolCall',id:'grounded-extraction',name:'workbench_memory',arguments:{action:'extract_constraint',source_id:source.sources?.[0]?.id||'missing',quote:'预算不超过200元'}}]
            :step===2?[{type:'toolCall',id:'private-finish',name:'workbench_finish',arguments:{summary:preference&&constrained?'PRIVATE_MEMORY_OK':'PRIVATE_MEMORY_MISSING'}}]
            :[{type:'text',text:'已检查跨任务记忆及原文来源'}];
        }
        if(process.env.WORKBENCH_REAL_TEST_SOURCES==='1'){
          const doc=process.env.WORKBENCH_SOURCE_DOCUMENT;const transcript=JSON.stringify(context.messages);
          if(transcript.includes('UNSELECTED_PRIVATE'))throw new Error('unselected source leaked');
          content=step===0?[{type:'toolCall',id:'selected-context',name:'workbench_context',arguments:{context_id:'selected'}}]
           :step===1?[{type:'toolCall',id:'selected-page',name:'workbench_document',arguments:{action:'read',document_id:doc,page:3}}]
           :step===2?[{type:'toolCall',id:'denied-page',name:'workbench_document',arguments:{action:'read',document_id:doc,page:2}}]
           :step===3?[{type:'toolCall',id:'source-complete',name:'workbench_finish',arguments:{summary:transcript.includes('没有被选择')&&transcript.includes('SELECTED_THIRD_PAGE')?'SOURCE_SCOPE_OK':'SOURCE_SCOPE_FAILED'}}]
           :[{type:'text',text:'已核验选中来源、固定快照和拒绝越界读取。'}];
        }
        const message:any = {role:'assistant',content,api:model.api,provider:model.provider,model:model.id,
          stopReason:content[0].type==='toolCall'?'toolUse':'stop',usage:{input:highUsage?170000:1,output:1,cacheRead:0,cacheWrite:0,totalTokens:highUsage?170001:2,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},timestamp:Date.now()};
        stream.push({type:'start',partial:message});
        if(content[0].type==='text'){stream.push({type:'text_start',contentIndex:0,partial:message});stream.push({type:'text_delta',contentIndex:0,delta:content[0].text,partial:message});stream.push({type:'text_end',contentIndex:0,content:content[0].text,partial:message});}
        stream.push({type:'done',reason:message.stopReason,message});
        stream.end();
      });
      return stream;
    },
  });
}
