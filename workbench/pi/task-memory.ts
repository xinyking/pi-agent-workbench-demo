/** Request-local task memory and recoverable tool-result offloading (Pi 1.3). */
import { Type } from 'typebox';
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';

async function exchange(ctx: ExtensionContext, payload: unknown, signal?: AbortSignal) {
  const raw = await ctx.ui.input('__workbench_memory__', JSON.stringify(payload), {signal});
  if (raw === undefined) throw new Error('任务上下文读取已取消');
  const result = JSON.parse(raw);
  if (result.error) throw new Error(result.error);
  return result;
}

export default function(pi: ExtensionAPI) {
  if (process.env.WORKBENCH_MODE === 'chat') return;
  // The context hook runs before each LLM call. Its projection is not appended
  // to the durable Pi transcript, so revisions do not accumulate as messages.
  pi.on('context', async (event, ctx) => {
    const memory = await exchange(ctx, {action: 'assemble'});
    if (memory.enabled === false) return;
    return {messages: [...event.messages, {
      role: 'custom', customType: 'workbench-task-memory',
      content: '当前任务记忆（来源和权限见内部字段；不是新增用户指令）：\n'+memory.text,
      display: false, timestamp: Date.now(),
    }]};
  });
  pi.on('tool_result', async (event, ctx) => {
    // Already-paged tools must remain directly readable. Preserve images and
    // non-text content; never discard them in a text-only offload projection.
    if (['workbench_memory','workbench_document','workbench_context'].includes(event.toolName)) return;
    if (!event.content.length || event.content.some(part => part.type !== 'text')) return;
    const text = event.content.map(part => part.type === 'text' ? part.text : '').join('\n');
    if (text.length <= 6000 || text.length > 2_000_000) return;
    try {
      const stored = await exchange(ctx, {action: 'offload', name: event.toolName, tool_call_id: event.toolCallId, text});
      if (stored.enabled === false) return;
      return {content: [{type: 'text', text: JSON.stringify(stored)}]};
    } catch (error) {
      // A storage failure must leave the original result intact.
      ctx.ui.notify('工具结果外置失败，保留原结果：'+String(error), 'warning');
    }
  });
  pi.registerTool({
    name: 'workbench_memory', label: '任务记忆与原文',
    description: 'read 读取当前记忆与版本；save/remove 维护自己的决定、进度或待确认问题，必须带当前 revision。不能自行写用户约束。自动提炼开启时，sources 返回可用用户来源；extract_constraint 必须提供 source_id 与逐字 quote，后台核验来源及冲突。read_artifact 按 artifact_id/offset 取回外置工具原文，每页3000字符，按 next_offset 继续。read_source 按 field=goal/latest_request 分页读取被节选的原始任务要求。',
    parameters: Type.Object({
      action: Type.Union([Type.Literal('read'),Type.Literal('save'),Type.Literal('remove'),Type.Literal('read_artifact'),Type.Literal('read_source'),Type.Literal('sources'),Type.Literal('extract_constraint')]),
      revision: Type.Optional(Type.Integer({minimum:1})), id: Type.Optional(Type.String()),
      kind: Type.Optional(Type.Union([Type.Literal('decision'),Type.Literal('progress'),Type.Literal('open_question')])),
      text: Type.Optional(Type.String({minLength:1,maxLength:2000})),
      source_id: Type.Optional(Type.String()), quote: Type.Optional(Type.String({minLength:4,maxLength:500})),
      field: Type.Optional(Type.Union([Type.Literal('goal'),Type.Literal('latest_request')])),
      artifact_id: Type.Optional(Type.String()), offset: Type.Optional(Type.Integer({minimum:0})),
    }),
    async execute(_id, params, signal, _update, ctx) {
      const result = await exchange(ctx, params, signal);
      return {content: [{type: 'text', text: JSON.stringify(result)}], details: {}};
    },
  });
}
