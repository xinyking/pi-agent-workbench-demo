import test from 'node:test';
import assert from 'node:assert/strict';
import {reconcileSidebar} from '../workbench/static/sidebar.mjs';
class Element{
 constructor(){this.children=[];this.dataset={};this.scrollTop=0;}
 contains(node){return this===node||this.children.some(c=>c.contains(node));}
 get isConnected(){return Boolean(this.parent);}
 remove(){if(this.parent)this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}
 insertBefore(node,before){node.remove();const index=before?this.children.indexOf(before):this.children.length;this.children.splice(index,0,node);node.parent=this;}
 append(node){this.insertBefore(node,null);}
 focus(){document.activeElement=this;}
}
globalThis.document={createElement:()=>new Element(),activeElement:null};
const update=(target,items)=>reconcileSidebar(target,items,()=>new Element(),(node,item)=>{node.textContent=item.title;},'暂无对话');
test('sidebar refresh keeps focused button identity and scroll, including reordered history',()=>{
 const target=new Element(),a={key:'a',title:'论文'},b={key:'b',title:'计划'};
 update(target,[a,b]);const original=target.children[1];original.focus();target.scrollTop=83;
 update(target,[b,{...a,title:'论文更新'}]);
 assert.equal(target.children[0],original);assert.equal(document.activeElement,original);assert.equal(target.scrollTop,83);assert.equal(target.children[1].textContent,'论文更新');
});
test('empty and removed history entries leave no stale navigation targets',()=>{
 const target=new Element();update(target,[]);assert.equal(target.children.length,1);assert.equal(target.children[0].textContent,'暂无对话');
 update(target,[{key:'a',title:'论文'}]);assert.equal(target.children.length,1);assert.equal(target.children[0].dataset.sidebarKey,'a');
 update(target,[]);assert.equal(target.children.length,1);assert.equal(target.children[0].dataset.sidebarKey,undefined);
});
