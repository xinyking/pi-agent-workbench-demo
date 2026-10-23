/** Workbench integration only: Pi still owns its model/tool loop. */
import { Type } from 'typebox';
import * as fs from 'node:fs';
import * as path from 'node:path';
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';

function safePath(raw: string, cwd: string): boolean {
  let target = path.resolve(cwd, raw);
  const root = fs.realpathSync(cwd);
  let existing = target;
  while (!fs.existsSync(existing) && path.dirname(existing) !== existing) existing = path.dirname(existing);
  target = path.resolve(fs.realpathSync(existing), path.relative(existing, target));
  if (target !== root && !target.startsWith(root + path.sep)) return false;
  const parts = path.relative(root, target).split(path.sep);
  if (parts.some(p => ['.ssh', '.aws', '.codex', '.git', '.env'].includes(p) || p.startsWith('.env.') || p.startsWith('.workbench'))) return false;
  const data = process.env.WORKBENCH_DATA_DIR;
  return !data || (target !== data && !target.startsWith(data + path.sep));
}

export default function (pi: ExtensionAPI) {
  pi.registerCommand('workbench-status', { description: '检查任务工作台桥接', handler: async (_args, ctx) => { ctx.ui.notify('workbench bridge ready', 'info'); } });
  pi.on('tool_call', async (event, ctx) => {
    const name = event.toolName;
    if (['read', 'write', 'edit', 'grep', 'find', 'ls'].includes(name)) {
      const raw = (event.input as any).path;
      if (typeof raw === 'string' && !safePath(raw, ctx.cwd)) return { block: true, reason: '工作台拒绝越界或受保护的路径' };
    }
    if (!['write', 'edit', 'bash'].includes(name)) return;
    if (name === 'bash' && process.env.WORKBENCH_SHELL_MODE !== 'approval') return { block: true, reason: '工作台尚未启用逐次审批 Shell' };
    const allowed = await ctx.ui.confirm('工作台工具审批', JSON.stringify({ name, call_id: event.toolCallId, arguments: event.input, workspace: ctx.cwd,
      execution: name === 'bash' ? '当前 WSL 用户权限下执行；不是操作系统沙箱' : '审批后由 Pi 执行文件修改' }), { timeout: 600000 });
    if (!allowed) return { block: true, reason: '用户拒绝或审批已过期' };
  });
  pi.registerTool({
    name: 'workbench_ask_user', label: '请求补充信息',
    description: '缺少简历、目标、约束或需要用户作出选择时必须调用此工具，不要仅在普通回复中要求用户提供信息。任务会进入需处理，等待用户回答后继续。',
    parameters: Type.Object({ question: Type.String({ minLength: 1, maxLength: 8000 }), options: Type.Optional(Type.Array(Type.String(), { maxItems: 8 })) }),
    async execute(_id, params, signal, _update, ctx) {
      const answer = await ctx.ui.input('__workbench_ask__', JSON.stringify(params), { signal });
      if (answer === undefined) throw new Error('信息请求已取消');
      return { content: [{ type: 'text', text: answer }], details: {} };
    },
  });
  pi.registerTool({
    name: 'workbench_context', label: '读取原始对话上下文',
    description: '读取转交给当前任务的完整原始对话快照。提示词上下文被节选时用此工具查找更早约束。按 next_offset 分页，不可读取其他任务的快照。',
    parameters: Type.Object({ context_id: Type.String(), offset: Type.Optional(Type.Integer({minimum:0})) }),
    async execute(_id, params, signal, _update, ctx) {
      const value = await ctx.ui.input('__workbench_context__', JSON.stringify(params), {signal});
      return {content:[{type:'text',text:value || '上下文读取已取消'}],details:{}};
    },
  });
  pi.registerTool({
    name:'workbench_document',label:'读取上传文档',
    description:'list 列出附件与质量告警；outline 查看目录；search 使用 query 搜索关键词；read 按 page/offset 读取原文。未就绪附件不可读，但可以继续讨论其他资料。Word 使用文本块而非排版页码。论文阅读必须先读取，关键事实引用返回的 citation。遇 next_offset 继续同页读取；区分作者结论、推断和未验证内容。',
    parameters:Type.Object({action:Type.Union([Type.Literal('list'),Type.Literal('outline'),Type.Literal('search'),Type.Literal('read')]),document_id:Type.Optional(Type.String()),query:Type.Optional(Type.String({maxLength:200})),page:Type.Optional(Type.Integer({minimum:1})),offset:Type.Optional(Type.Integer({minimum:0}))}),
    async execute(_id,params,signal,_update,ctx){const value=await ctx.ui.input('__workbench_document__',JSON.stringify(params),{signal});return {content:[{type:'text',text:value||'附件读取已取消'}],details:{}};},
  });
  if (process.env.WORKBENCH_CHILD === '1') return;
  pi.registerTool({
    name: 'workbench_create_agent', label: '创建通用子 Agent',
    description: '用户明确要求创建专家或子 Agent 时，保存新的角色定义供下一次任务编排使用。先描述清楚职责与交付要求，使用新名称，不覆盖已有定义。',
    parameters: Type.Object({name:Type.String({pattern:'^[a-z][a-z0-9-]{1,47}$'}),description:Type.String({minLength:1,maxLength:500}),prompt:Type.String({minLength:1,maxLength:16000}),tools:Type.Array(Type.String(),{minItems:1}),model:Type.Optional(Type.String())}),
    async execute(_id, params, signal, _update, ctx) {
      const value = await ctx.ui.input('__workbench_create_agent__', JSON.stringify(params), {signal});
      return {content:[{type:'text',text:value || 'Agent 创建已取消'}],details:{}};
    },
  });
  if(process.env.WORKBENCH_MODE === 'chat') {
    pi.registerTool({
      name: 'workbench_create_task', label: '基于对话创建任务',
      description: '仅当用户明确要求基于前面内容创建或执行任务时调用。系统自动管理原始对话上下文并创建任务中心卡片。不要为普通问答创建任务。',
      parameters: Type.Object({prompt:Type.String({minLength:1,maxLength:8000}),workspace:Type.Optional(Type.String())}),
      async execute(_id, params, signal, _update, ctx) {
        const value=await ctx.ui.input('__workbench_create_task__',JSON.stringify(params),{signal});
        return {content:[{type:'text',text:value || '任务创建已取消'}],details:{}};
      },
    });
  }
  pi.registerTool({
    name: 'workbench_finish', label: '报告任务完成',
    description: '仅在用户目标已经完成、必要子任务结束且不存在等待用户补充的信息时调用，提交最终交付摘要。缺少资料时必须调用 workbench_ask_user。报告完成后向用户简要总结结果。',
    parameters: Type.Object({ summary: Type.String({ minLength: 1, maxLength: 8000 }) }),
    async execute(_id, params, _signal, _update, ctx) {
      ctx.ui.notify('__workbench_finish__' + JSON.stringify(params), 'info');
      return { content: [{ type: 'text', text: '完成报告已记录。请向用户总结交付结果。' }], details: {} };
    },
  });
  pi.registerTool({
    name:'workbench_plan',label:'提交任务编排计划',
    description:'复杂任务先提交计划。步骤必须有明确交付、执行角色与依赖，独立步骤可同批并行。保存计划不表示已执行；随后 workbench_delegate 的每项任务携带对应 step_id，实际结果自动更新步骤进度。',
    parameters:Type.Object({steps:Type.Array(Type.Object({id:Type.String(),title:Type.String(),task:Type.String(),agent:Type.String(),depends_on:Type.Optional(Type.Array(Type.String()))}),{minItems:1,maxItems:16})}),
    async execute(_id,params,signal,_update,ctx){const value=await ctx.ui.input('__workbench_plan__',JSON.stringify(params),{signal});return {content:[{type:'text',text:value||'计划提交已取消'}],details:{}};},
  });
  const names: string[] = JSON.parse(process.env.WORKBENCH_AGENTS || '[]');
  const item = Type.Object({ agent: Type.String({ description: '启用的 Agent 名称：' + names.join(', ') }), task: Type.String({ minLength: 1, maxLength: 8000 }), step_id: Type.Optional(Type.String()) });
  pi.registerTool({
    name: 'workbench_delegate', label: '工作台子 Agent',
    description: '将具体子任务交给独立 Pi 会话。根据可用 Agent 列表选择规划、研究、分析、写作、审查、求职或编码角色。存在计划时 step_id、agent、task 必须与计划步骤一致。tasks 并行执行，最多四项；有依赖的阶段分次调用。每个子 Agent 的过程会显示在任务详情中。',
    parameters: Type.Object({ tasks: Type.Array(item, { minItems: 1, maxItems: 4 }) }),
    async execute(_id, params, signal, _onUpdate, ctx) {
      const value = await ctx.ui.input('__workbench_delegate__', JSON.stringify(params), { signal });
      if (value === undefined) throw new Error('子任务已取消');
      return { content: [{ type: 'text', text: value }], details: {} };
    },
  });
}
