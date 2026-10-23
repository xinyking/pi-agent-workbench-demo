// Keep the select as the form/API source of truth; enhance its presentation.
const instances=new WeakMap();
export function enhanceWorkspace(select){
 if(instances.has(select)){instances.get(select).refresh();return;}
 const wrapper=document.createElement('div');wrapper.className='workspace-picker';
 const trigger=document.createElement('button');trigger.type='button';trigger.className='workspace-trigger';trigger.setAttribute('role','combobox');trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-label',select.getAttribute('aria-label')||'选择工作区');
 const panel=document.createElement('div');panel.className='workspace-menu';panel.hidden=true;panel.id=select.id+'-menu';trigger.setAttribute('aria-controls',panel.id);
 const search=document.createElement('input');search.type='search';search.className='workspace-search';search.placeholder='搜索项目名称或路径';search.setAttribute('aria-label','搜索工作区');
 const list=document.createElement('div');list.setAttribute('role','listbox');list.className='workspace-options';list.id=select.id+'-options';
 const error=document.createElement('span');error.className='workspace-validation';error.hidden=true;error.setAttribute('role','alert');
 panel.append(search,list);wrapper.append(trigger,panel,error);select.after(wrapper);select.hidden=true;
 const required=select.required;select.required=false;let items=[];let highlighted=0;
 function close(){panel.hidden=true;trigger.setAttribute('aria-expanded','false');}
 function refresh(){trigger.disabled=select.disabled;const option=select.selectedOptions[0];const value=option?.value||'';trigger.replaceChildren();const icon=document.createElement('span');icon.className='workspace-symbol';icon.textContent=select.id==='chat-workspace'||value?'▱':'＋';const copy=document.createElement('span');copy.className='workspace-trigger-copy';const name=document.createElement('strong'),path=document.createElement('small');name.textContent=value?value.split('/').filter(Boolean).at(-1):'选择工作区';path.textContent=value||(required?'请选择项目目录':'普通对话可暂不选择');copy.append(name,path);const arrow=document.createElement('span');arrow.textContent='⌄';arrow.className='workspace-chevron';trigger.append(icon,copy,arrow);trigger.title=value||'选择一个已配置的 WSL 项目';error.hidden=true;trigger.removeAttribute('aria-invalid');}
 function render(){list.replaceChildren();items=[];const query=search.value.toLowerCase();for(const option of select.options){if(query&&!`${option.value} ${option.textContent}`.toLowerCase().includes(query))continue;const b=document.createElement('button');b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',String(option.selected));const name=document.createElement('strong'),path=document.createElement('small');name.textContent=option.value?option.value.split('/').filter(Boolean).at(-1):'暂不选择';path.textContent=option.value||'适用于普通对话';b.append(name,path);b.onclick=()=>{select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));refresh();close();trigger.focus();};list.append(b);items.push(b);}highlighted=-1;if(!items.length){const empty=document.createElement('p');empty.textContent='没有匹配的工作区，请先在设置中添加。';list.append(empty);}}
 trigger.onclick=event=>{event.preventDefault();event.stopPropagation();panel.hidden=!panel.hidden;trigger.setAttribute('aria-expanded',String(!panel.hidden));if(!panel.hidden){search.value='';render();search.focus();}};
 search.oninput=render;
 wrapper.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();trigger.focus();}if(event.key==='Tab')close();if(event.key==='Enter'&&event.target===search&&!panel.hidden){event.preventDefault();items[Math.max(0,highlighted)]?.click();}if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();if(panel.hidden){panel.hidden=false;trigger.setAttribute('aria-expanded','true');render();}else highlighted=highlighted<0?(event.key==='ArrowDown'?0:items.length-1):(highlighted+(event.key==='ArrowDown'?1:-1)+items.length)%Math.max(1,items.length);items[highlighted]?.focus();}});
 document.addEventListener('pointerdown',event=>{if(!wrapper.contains(event.target))close();});
 select.addEventListener('change',refresh);
 if(required)select.form?.addEventListener('submit',event=>{if(!select.value){event.preventDefault();event.stopImmediatePropagation();error.textContent='请选择一个工作区';error.hidden=false;trigger.setAttribute('aria-invalid','true');trigger.focus();}},true);
 instances.set(select,{refresh});refresh();
}

// Enhance native selects while preserving their value/change contract.
const selectInstances=new WeakMap();
export function enhanceSelect(select){
 if(!('showPopover' in HTMLElement.prototype))return;
 if(selectInstances.has(select)){selectInstances.get(select).refresh();return;}
 const trigger=document.createElement('button');trigger.type='button';trigger.className='select-trigger';trigger.dataset.selectFor=select.id;
 trigger.setAttribute('role','combobox');trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');
 const label=select.getAttribute('aria-label')||select.closest('label')?.childNodes[0]?.textContent?.trim()||'选择选项';trigger.setAttribute('aria-label',label);
 const menu=document.createElement('div');menu.className='select-menu';menu.id=select.id+'-menu';menu.setAttribute('popover','auto');menu.setAttribute('role','listbox');menu.setAttribute('aria-label',label);
 trigger.setAttribute('aria-controls',menu.id);select.after(trigger);(select.closest('dialog')||document.body).append(menu);select.hidden=true;
 let opened=false,active=0,signature='',buttons=[],typed='',typingTimer;
 function place(){const box=trigger.getBoundingClientRect(),width=Math.min(Math.max(box.width,200),innerWidth-24);menu.style.width=width+'px';menu.style.left=Math.max(12,Math.min(box.left,innerWidth-width-12))+'px';const roomBelow=innerHeight-box.bottom-12,roomAbove=box.top-12;const below=roomBelow>=Math.min(menu.scrollHeight,260)||roomBelow>=roomAbove;menu.style.maxHeight=Math.max(70,Math.min(300,below?roomBelow:roomAbove))+'px';menu.style.top=(below?box.bottom+6:Math.max(12,box.top-Math.min(menu.scrollHeight,300)-6))+'px';}
 function mark(index){active=index;buttons.forEach((b,i)=>b.classList.toggle('is-active',i===active));if(buttons[active]){trigger.setAttribute('aria-activedescendant',buttons[active].id);buttons[active].scrollIntoView({block:'nearest'});}else trigger.removeAttribute('aria-activedescendant');}
 function close(){if(opened)menu.hidePopover();}
 function choose(option){select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));refresh();close();trigger.focus();}
 function render(){
  const previous=buttons[active]?.dataset.value;menu.replaceChildren();buttons=[];
  for(const option of select.options){const b=document.createElement('button');b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',String(option.selected));b.disabled=option.disabled;b.dataset.value=option.value;b.title=option.textContent;b.id=select.id+'-choice-'+buttons.length;const text=document.createElement('span');text.textContent=option.textContent;const check=document.createElement('span');check.className='select-check';check.textContent=option.selected?'✓':'';b.append(text,check);b.tabIndex=-1;b.onclick=()=>choose(option);b.onpointermove=()=>mark(buttons.indexOf(b));buttons.push(b);menu.append(b);}
  if(!buttons.length){const empty=document.createElement('p');empty.textContent='暂无可选项';menu.append(empty);}
  const found=buttons.findIndex(b=>b.dataset.value===previous&&!b.disabled);mark(found>=0?found:Math.max(0,buttons.findIndex(b=>b.getAttribute('aria-selected')==='true'&&!b.disabled)));
 }
 function refresh(){
  trigger.disabled=select.disabled||!select.options.length;
  const option=select.selectedOptions[0];trigger.replaceChildren();const text=document.createElement('span');text.className='select-value';text.textContent=option?.textContent||'暂无可选项';const arrow=document.createElement('span');arrow.className='select-chevron';arrow.setAttribute('aria-hidden','true');arrow.textContent='⌄';trigger.append(text,arrow);trigger.title=option?.textContent||label;
  const next=JSON.stringify([...select.options].map(o=>[o.value,o.textContent,o.disabled,o.selected]));if(next!==signature){signature=next;if(opened){render();place();}}
  if(trigger.disabled)close();
 }
 function open(){if(trigger.disabled)return;opened=true;trigger.setAttribute('aria-expanded','true');render();menu.showPopover();place();mark(active);trigger.focus();}
 trigger.onclick=e=>{e.preventDefault();opened?close():open();};
 trigger.onkeydown=e=>{
  if(e.key==='Tab'){close();return;}
  if(e.key==='Escape'){if(opened){e.preventDefault();e.stopPropagation();close();}return;}
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
   e.preventDefault();if(!opened){open();return;}
   const indices=buttons.map((b,i)=>b.disabled?-1:i).filter(i=>i>=0);if(!indices.length)return;
   let next;if(e.key==='Home')next=indices[0];else if(e.key==='End')next=indices.at(-1);else next=indices[(indices.indexOf(active)+(e.key==='ArrowDown'?1:-1)+indices.length)%indices.length];mark(next);return;
  }
  if(e.key==='Enter'||e.key===' '){e.preventDefault();if(opened)buttons[active]?.click();else open();return;}
  if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!e.altKey){e.preventDefault();typed+=e.key.toLocaleLowerCase();clearTimeout(typingTimer);typingTimer=setTimeout(()=>typed='',700);if(!opened)open();const index=buttons.findIndex(b=>!b.disabled&&b.textContent.toLocaleLowerCase().startsWith(typed));if(index>=0)mark(index);}
 };
 menu.addEventListener('toggle',e=>{opened=e.newState==='open';trigger.setAttribute('aria-expanded',String(opened));if(!opened)trigger.removeAttribute('aria-activedescendant');});
 const dialog=select.closest('dialog'),reposition=()=>{if(opened)place();};
 dialog?.addEventListener('close',close);
 select.addEventListener('change',refresh);
 const observer=new MutationObserver(refresh);observer.observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','label']});
 addEventListener('resize',reposition);document.addEventListener('scroll',reposition,true);
 function dispose(){close();clearTimeout(typingTimer);observer.disconnect();dialog?.removeEventListener('close',close);select.removeEventListener('change',refresh);removeEventListener('resize',reposition);document.removeEventListener('scroll',reposition,true);trigger.remove();menu.remove();select.hidden=false;selectInstances.delete(select);}
 selectInstances.set(select,{refresh,dispose});refresh();
}
export function disposeSelect(select){selectInstances.get(select)?.dispose();}
