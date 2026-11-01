/** Explicit selection and preview before task creation. No silent history inclusion. */
const $=id=>document.getElementById(id);
export function createSourceForm({api,node}){
 let generation=0,revision=0,loading=false,error='',preview=null,previewKey='',documents=[];
 const active=()=>Boolean($('executor-chat').value||$('executor-context').value.trim()||$('source-constraints').value.trim());
 function invalidate(){revision++;preview=null;previewKey='';$('source-preview-status').textContent=active()?'资料或要求发生变化，请更新上下文预览。':'';$('source-preview-text').textContent='';}
 function pages(value){
  if(!value.trim())return [];const result=new Set();
  for(const part of value.replaceAll('，',',').split(',')){
   const match=part.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);if(!match)throw new Error('页码使用 1-3,5 的格式，留空选择全部。');const start=Number(match[1]),end=Number(match[2]||match[1]);if(start<1||end<start||end-start>=500)throw new Error('页码范围无效，每份资料最多500页/块。');for(let p=start;p<=end;p++)result.add(p);
  }
  if(result.size>500)throw new Error('每份资料最多选择500页/块。');return [...result].sort((a,b)=>a-b);
 }
 function selection(){return {conversation_id:$('executor-chat').value||null,message_ids:[...$('source-messages').querySelectorAll('input:checked')].map(x=>Number(x.value)),documents:documents.filter(d=>$('source-doc-'+d.id)?.checked).map(d=>({id:d.id,pages:pages($('source-pages-'+d.id).value)})),context:$('executor-context').value,constraints:$('source-constraints').value,budget:Number($('source-budget').value)};}
 function request(){if(loading)throw new Error('资料正在加载，请稍候');if(error)throw new Error(error);return {prompt:$('prompt').value,model:$('model').value,provider_id:$('task-executor').value,source_selection:selection()};}
 async function loadConversation(){
  const token=++generation;loading=true;error='';documents=[];invalidate();$('source-messages').replaceChildren();$('source-documents').replaceChildren();const id=$('executor-chat').value;
  if(!id){loading=false;return;}
  try{
   const data=await api('/api/conversations/'+id);if(token!==generation)return;documents=data.documents;
   for(const m of data.messages){const label=node('label','','source-choice'),input=node('input');input.type='checkbox';input.value=m.id;input.id='source-message-'+m.id;input.disabled=m.status==='streaming';label.append(input,node('span',(m.role==='user'?'用户':'助手')+' · '+m.text.replace(/\s+/g,' ').slice(0,200)));$('source-messages').append(label);}
   if(!data.messages.length)$('source-messages').append(node('p','此对话还没有消息。','muted'));
   for(const d of documents){const row=node('div','','source-document'),label=node('label','','source-choice'),check=node('input');check.type='checkbox';check.id='source-doc-'+d.id;check.disabled=d.status!=='ready';label.append(check,node('span',d.name+' · '+d.pages+(d.kind==='.pdf'?' 页':' 块')+(check.disabled?' · 尚未就绪':'')));const range=node('label','选取页码 / 文本块'),input=node('input');input.id='source-pages-'+d.id;input.placeholder='留空全部，或填写 1-3,5';input.maxLength=1000;input.disabled=check.disabled;range.append(input);row.append(label,range);$('source-documents').append(row);}
   if(!documents.length)$('source-documents').append(node('p','此对话还没有附件，可先在对话中上传资料。','muted'));
  }catch(e){if(token===generation){error=e.message;$('source-preview-status').textContent=error;}}
  finally{if(token===generation)loading=false;}
 }
 $('new-dialog').addEventListener('close',()=>{revision++;generation++;loading=false;});
 $('executor-chat').addEventListener('change',loadConversation);
 const onEdit=event=>{if(!['start-now','workspace'].includes(event.target.id))invalidate();};
 $('new-form').addEventListener('input',onEdit);$('new-form').addEventListener('change',onEdit);
 $('source-preview').onclick=async()=>{
  const button=$('source-preview'),token=revision;button.disabled=true;
  try{const body=request(),key=JSON.stringify(body);$('source-preview-status').textContent='正在组装选中资料…';const result=await api('/api/context/preview',body);if(token!==revision||key!==JSON.stringify(request()))return;preview=result;previewKey=key;$('new-error').textContent='';$('new-error').hidden=true;const b=result.budget;$('source-preview-status').textContent=`${b.fits?'预算内':'超出预算，不能创建'} · 保守估算 ${b.used.toLocaleString()} / ${b.limit.toLocaleString()} · ${result.message_count} 条消息 · ${result.sources.length} 份文档。${b.context_window?'模型窗口 '+b.context_window+'，输出预留 '+b.output_reserve+'。':'模型窗口未知，按手工预算检查。'} 非精确Token计数；${result.excerpt?'下面仅为超限预览节选。':'下面是本次选中输入。'}`;$('source-preview-text').textContent=result.text;
  }catch(e){if(token===revision){$('source-preview-status').textContent=e.message;preview=null;}}
  finally{button.disabled=false;}
 };
 function payload(){if(!active())return {};const body=request();if(!preview||previewKey!==JSON.stringify(body))throw new Error('请先点击“预览选中上下文”，确认资料与预算后再创建。');if(!preview.budget.fits)throw new Error('选中输入超出预算，请减少资料或调整预算后重新预览。');return {source_selection:body.source_selection,preview_digest:preview.digest};}
 async function load(){await loadConversation();}
 function reset(){generation++;loading=false;error='';documents=[];$('executor-chat').value='';$('executor-context').value='';$('source-constraints').value='';$('source-budget').value='16000';$('source-messages').replaceChildren();$('source-documents').replaceChildren();invalidate();}
 return {load,payload,reset};
}
