import { marked } from './vendor/marked.mjs';
import temml from './vendor/temml.mjs';
// Tokenize before Markdown consumes TeX backslashes. Code tokens remain literal.
export function mathToken(source, block=false){
 const patterns=block?[
  /^\s*\$\$([\s\S]+?)\$\$(?:\n|$)/,
  /^\s*\\\[([\s\S]+?)\\\](?:\n|$)/,
  /^\s*\[([^\n]*\\[a-zA-Z]+[^\n]*)\][ \t]*(?:\n|$)/
 ]:[/^\\\(([\s\S]+?)\\\)/,/^\\\[([\s\S]+?)\\\]/,/^\$\$([\s\S]+?)\$\$/,/^\$(?!\s|\$)((?:\\.|[^$\n])+?)(?<!\s)\$(?!\d)/];
 for(const pattern of patterns){const match=pattern.exec(source);if(match)return {type:'math',raw:match[0],text:match[1].trim(),display:block||source.startsWith('\\[')||source.startsWith('$$')};}
}
marked.use({extensions:[
 {name:'math',level:'block',start:src=>src.search(/(?:^|\n)[ \t]*(?:\$\$|\\\[|\[[^\n]*\\[a-zA-Z])/),tokenizer(src){return mathToken(src,true);}},
 {name:'math',level:'inline',start:src=>src.search(/\$|\\[([]/),tokenizer(src){return mathToken(src);}}
]});
// Parse Markdown with Marked; build a small DOM allowlist instead of inserting HTML.
export function safeLink(raw){
 const value=String(raw||'').trim();
 if(/[\u0000-\u0020\u007f]/.test(value))return null;
 if(/^(https?:\/\/|mailto:)/i.test(value))return value;
 if(/^#[\w\u0080-\uffff-]+$/.test(value))return value;
 return null;
}
export function paperReference(raw){const m=/^paper:([a-f0-9]{32})#page=([1-9]\d*)$/.exec(raw||'');return m?{id:m[1],page:Number(m[2])}:null;}
const entities={amp:'&',lt:'<',gt:'>',quot:'"',apos:"'",nbsp:'\u00a0'};
const decode=value=>String(value||'').replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi,(all,key)=>{if(key[0]!=='#')return entities[key.toLowerCase()]||all;const n=parseInt(key.slice(key[1]?.toLowerCase()==='x'?2:1),key[1]?.toLowerCase()==='x'?16:10);return n>0&&n<=0x10ffff?String.fromCodePoint(n):all;});
export function markdownTokens(source){return marked.lexer(String(source||'').slice(0,300000),{...marked.defaults,gfm:true,breaks:false});}
export function renderMarkdown(source,{onPaper}={}){
 const create=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
 const fragment=document.createDocumentFragment();
 const walk=(tokens,parent,depth=0)=>{
  if(depth>40)return;
  for(const token of tokens||[]){let element;
   switch(token.type){
    case 'math':{
     element=create(token.display?'div':'span');element.className=token.display?'math-display':'math-inline';
     try{temml.render(token.text,element,{displayMode:token.display,throwOnError:true,trust:false,maxExpand:1000,maxSize:[20,20]});}
     catch{element.textContent=token.raw;element.className+=' math-error';element.title='公式未完整或语法不受支持，保留原文';}
     break;
    }
    case 'space':continue;
    case 'heading':element=create('h'+Math.min(6,token.depth));walk(token.tokens,element,depth+1);break;
    case 'paragraph':element=create('p');walk(token.tokens,element,depth+1);break;
    case 'text':if(token.tokens){walk(token.tokens,parent,depth+1);continue;}parent.append(document.createTextNode(decode(token.text)));continue;
    case 'escape':parent.append(document.createTextNode(decode(token.text)));continue;
    case 'html':parent.append(document.createTextNode(token.raw||token.text||''));continue;
    case 'strong':case 'em':case 'del':element=create(token.type);walk(token.tokens,element,depth+1);break;
    case 'codespan':element=create('code',decode(token.text));break;
    case 'br':element=create('br');break;
    case 'hr':element=create('hr');break;
    case 'blockquote':element=create('blockquote');walk(token.tokens,element,depth+1);break;
    case 'list':element=create(token.ordered?'ol':'ul');if(token.ordered&&token.start!==1)element.start=token.start;for(const item of token.items){const li=create('li');if(item.task){const box=create('input');box.type='checkbox';box.checked=item.checked;box.disabled=true;li.className='markdown-task';li.append(box);}walk(item.tokens,li,depth+1);element.append(li);}break;
    case 'code':{
     element=create('section');element.className='markdown-code';const head=create('div');head.className='code-toolbar';const language=(token.lang||'text').split(/\s/)[0];head.append(create('span',language));const copy=create('button','复制代码');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(token.text);copy.textContent='已复制';}catch{copy.textContent='复制失败，可选中文字复制';}};head.append(copy);const pre=create('pre'),code=create('code',token.text);code.dataset.language=language;pre.append(code);element.append(head,pre);break;
    }
    case 'table':element=create('div');element.className='markdown-table';{const table=create('table'),head=create('thead'),row=create('tr'),body=create('tbody');for(const cell of token.header){const th=create('th');walk(cell.tokens,th,depth+1);row.append(th);}head.append(row);for(const cells of token.rows){const tr=create('tr');for(const cell of cells){const td=create('td');walk(cell.tokens,td,depth+1);tr.append(td);}body.append(tr);}table.append(head,body);element.append(table);}break;
    case 'link':{
     const reference=paperReference(token.href),url=safeLink(token.href);
     if(reference&&onPaper){element=create('button');element.type='button';element.className='paper-reference';element.onclick=()=>onPaper(reference);}
     else if(url){element=create('a');element.href=url;element.target='_blank';element.rel='noopener noreferrer';}
     else{element=create('span');element.className='unavailable-link';}
     walk(token.tokens||[{type:'text',text:token.text}],element,depth+1);break;
    }
    case 'image':{
     const url=safeLink(token.href);element=create(url?'a':'span','图片：'+decode(token.text));if(url){element.href=url;element.target='_blank';element.rel='noopener noreferrer';}element.className='markdown-image-link';break;
    }
    default:element=create('span',token.text||token.raw||'');
   }
   if(element)parent.append(element);
  }
 };
 try{walk(markdownTokens(source),fragment);}catch{fragment.replaceChildren(document.createTextNode(String(source||'')));}
 return fragment;
}
