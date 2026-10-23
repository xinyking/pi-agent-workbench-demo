import test from 'node:test';
import assert from 'node:assert/strict';
import {markdownTokens,renderMarkdown,safeLink,paperReference,mathToken} from '../workbench/static/markdown.mjs';
class Node {
 constructor(tag,text=''){this.tag=tag;this.tagName=tag;this.textContent=text;this.children=[];this.dataset={};this.style={};this.attributes={};}
 setAttribute(name,value){this.attributes[name]=value;}
 appendChild(item){this.children.push(item);return item;}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;}
}
globalThis.document={compatMode:'CSS1Compat',createElementNS:(namespace,tag)=>new Node(tag),createElement:tag=>new Node(tag),createDocumentFragment:()=>new Node('fragment'),createTextNode:text=>new Node('text',text)};
const all=root=>[root,...root.children.flatMap(all)];
test('GFM structures render as real heading, list, quote, table and code nodes',()=>{
 const text='# 阅读笔记\n\n**贡献**与`指标`\n\n> 作者结论\n\n- [x] 已读\n- 待核查\n\n| 指标 | 数值 |\n| --- | --- |\n| n | 120 |\n\n```python\nprint("test")\n```';
 const nodes=all(renderMarkdown(text));
 for(const tag of ['h1','strong','code','blockquote','ul','table','thead','tbody','pre','input'])assert.ok(nodes.some(n=>n.tag===tag),tag);
 assert.ok(nodes.some(n=>n.tag==='code'&&n.textContent==='print("test")'));
});
test('raw HTML and unsafe links cannot create active markup',()=>{
 const nodes=all(renderMarkdown('<script>alert(1)</script>\n\n[x](javascript:alert%281%29)\n\n![track](https://example.com/pixel.png)'));
 assert.ok(!nodes.some(n=>['script','img','iframe','style'].includes(n.tag)));
 assert.ok(nodes.some(n=>n.tag==='text'&&n.textContent.includes('<script>')));
 assert.ok(!nodes.some(n=>n.href?.startsWith('javascript:')));
 assert.equal(safeLink('java\nscript:foo'),null);assert.equal(safeLink('data:text/html,test'),null);
});
test('source citations are scoped actions; entities and nested lists are preserved',()=>{
 const id='a'.repeat(32);let clicked;
 const nodes=all(renderMarkdown(`[来源](paper:${id}#page=3)\n\nA &amp; B\n\n1. 第一项\n   - 子项`,{onPaper:ref=>clicked=ref}));
 const link=nodes.find(n=>n.className==='paper-reference');assert.ok(link);link.onclick();assert.deepEqual(clicked,{id,page:3});
 assert.deepEqual(paperReference('paper:'+id+'#page=0'),null);
 assert.ok(nodes.some(n=>n.tag==='text'&&n.textContent==='A & B'));
 assert.ok(nodes.some(n=>n.tag==='ol'));assert.ok(nodes.some(n=>n.tag==='ul'));
});
test('streaming incomplete fences remain readable and close cleanly',()=>{
 assert.equal(markdownTokens('```txt\nfirst')[0].type,'code');
 assert.ok(all(renderMarkdown('```txt\nfirst\n```')).some(n=>n.tag==='code'&&n.textContent==='first'));
});

test('TeX delimiters and the user bracket example produce real MathML fractions',()=>{
 for(const text of [String.raw`[ \kappa = \frac{1}{R} ]`,String.raw`\[ \kappa = \frac{1}{R} \]`,String.raw`$$\kappa=\frac{1}{R}$$`,String.raw`曲率 $\kappa=\frac{1}{R}$`,String.raw`曲率 \(\kappa=\frac{1}{R}\)`]){
  const nodes=all(renderMarkdown(text));assert.ok(nodes.some(n=>n.tag==='math'),text);assert.ok(nodes.some(n=>n.tag==='mfrac'),text);assert.ok(nodes.some(n=>n.tag==='text'&&n.textContent==='κ'),text);
 }
});
test('math preserves code, ordinary brackets, currency, and incomplete streaming input',()=>{
 for(const text of [String.raw`\[\frac{1}`, '[普通说明]', '花费 $20 和 $30'])assert.ok(!all(renderMarkdown(text)).some(n=>n.tag==='math'),text);
 assert.ok(!all(renderMarkdown('`$x$`\n\n```tex\n\\[x\\]\n```')).some(n=>n.tag==='math'));
 assert.equal(mathToken(String.raw`\(x^2\)` ).text,'x^2');
 assert.ok(all(renderMarkdown('$x^2+y_1$')).some(n=>n.tag==='msup'));
 assert.ok(all(renderMarkdown(String.raw`$\unknownCommand{x}$`)).some(n=>n.className?.includes('math-error')));
 assert.ok(!all(renderMarkdown(String.raw`$\href{javascript:alert(1)}{click}$`)).some(n=>n.tag==='a'));
});
