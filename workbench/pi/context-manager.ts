/** Adapted from Pi 1.3.0 examples/extensions/trigger-compact.ts (MIT).
 * Original copyright (c) 2025 Mario Zechner. See vendor/LICENSE.pi.
 * Changes: relative context threshold, RPC telemetry, preserved constraints,
 * one automatic attempt per prompt, and a command for explicit compaction.
 */
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';
export default function(pi: ExtensionAPI) {
  let attempted=false;
  let compacting=false;
  const publish=(ctx:ExtensionContext, data:unknown)=>ctx.ui.notify('__workbench_context_state__'+JSON.stringify(data),'info');
  const compact=(ctx:ExtensionContext)=>{
    if(compacting)return;
    if(!ctx.isIdle()){publish(ctx,{phase:'failed',error:'请等待当前任务结束后再手动压缩'});return;}
    compacting=true;attempted=true;
    publish(ctx,{phase:'started',usage:ctx.getContextUsage()||null});
    ctx.compact({
      customInstructions:'使用中文。必须保留用户最新目标、硬约束、已确认事实、决定、资料来源、context_id、当前计划与任务状态、未解决问题、必要文件路径和下一步。明确区分已完成与计划。不能把推断变成用户要求，不能擅自消除矛盾。',
      onComplete:(result)=>{compacting=false;publish(ctx,{phase:'completed',usage:ctx.getContextUsage()||null,summary:result.summary});},
      onError:(error)=>{compacting=false;publish(ctx,{phase:'failed',error:error.message});},
    });
  };
  pi.on('before_agent_start',()=>{attempted=false;});
  pi.on('turn_end',(_event,ctx)=>{
    const usage=ctx.getContextUsage();
    publish(ctx,{phase:'usage',usage:usage||null});
    if(!attempted && usage?.percent!=null && usage.percent>=80){
      attempted=true;
      publish(ctx,{phase:'requested',usage});
    }
  });
  pi.registerCommand('workbench-compact',{description:'压缩上下文，保留目标、约束、决定与未解决问题',handler:async(_args,ctx)=>compact(ctx)});
}
