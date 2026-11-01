export function renderSourcePacket(target,data,{node,button,openPaper,showError}){
 if(!data)return;const card=node('section','','memory-card source-packet');const b=data.budget;
 card.append(node('h4','创建时的已选资料'),node('p',`固定快照 · SHA-256 ${data.digest}`,'muted'),node('p',`保守估算 ${b.used} / ${b.limit}；${data.packet.messages.length} 条消息，${data.packet.documents.length} 份文档。非精确Token数。`,'muted'));
 for(const d of data.packet.documents){const row=node('div','','source-citations');row.append(node('strong',d.name));for(const p of d.pages)row.append(button((d.kind==='.pdf'?'页 ':'块 ')+p.page,()=>openPaper({id:d.id,page:p.page}).catch(e=>showError(e.message,'detail-error'))));card.append(row);}
 const raw=node('details');raw.append(node('summary','查看实际选中输入'),node('pre',data.text));card.append(raw);target.append(card);
}
