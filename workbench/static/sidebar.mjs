// Keep navigation nodes alive so polling does not discard keyboard focus.
export function reconcileSidebar(target,items,create,update,emptyText){
 const scroll=target.scrollTop,focused=target.contains(document.activeElement)?document.activeElement:null;
 const existing=new Map([...target.children].filter(n=>n.dataset.sidebarKey).map(n=>[n.dataset.sidebarKey,n]));
 const wanted=new Set(items.map(item=>item.key));
 for(const child of [...target.children])if(!wanted.has(child.dataset.sidebarKey))child.remove();
 items.forEach((item,index)=>{
  const button=existing.get(item.key)||create(item);button.dataset.sidebarKey=item.key;update(button,item);
  if(target.children[index]!==button)target.insertBefore(button,target.children[index]||null);
 });
 if(!items.length){const empty=document.createElement('p');empty.className='sidebar-empty';empty.textContent=emptyText;target.append(empty);}
 if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
 target.scrollTop=scroll;
}
