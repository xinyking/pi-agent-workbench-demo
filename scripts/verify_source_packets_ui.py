#!/usr/bin/env python3
"""Isolated source selection and citation navigation with real PDF/DOCX/MD parsing."""
import base64
import json
import os
from functools import partial
from unittest.mock import patch
import sys
import tempfile
import threading
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from fixtures.document_samples import pdf_bytes,docx_bytes
from workbench.pi_client import PiClient
from workbench.runtime import Runtime
from workbench.store import Store
from workbench.__main__ import WorkbenchServer,make_handler
from playwright.sync_api import sync_playwright,expect

def main():
 out=ROOT/'artifacts/source-packets-ui';out.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ):
  os.environ['PI_CODING_AGENT_DIR']=str(Path(temp)/'isolated-pi')
  root=Path(temp);workspace=root/'project';workspace.mkdir();store=Store(root/'data/state.sqlite3');runtime=Runtime(store);runtime.config.require_pi();runtime.client_factory=partial(PiClient,extra_extensions=[ROOT/'tests/fixtures/offline_provider.ts']);runtime.models.save({'action':'default','revision':0,'model':'workbench-fixture/fixture'});runtime.config.add_workspace(str(workspace));runtime.executors.binaries={'codex':str(ROOT/'tests/fixtures/fake_executor.py'),'claude':str(ROOT/'tests/fixtures/fake_executor.py')};chat=runtime.conversations.create('chat',str(workspace))
  with store.lock,store.db:
   message=store.db.execute('INSERT INTO conversation_messages(conversation_id,role,text,status,created) VALUES(?,?,?,?,?)',(chat['id'],'user','USER_SELECTED 中文交付','completed',time.time())).lastrowid
   store.db.execute('INSERT INTO conversation_messages(conversation_id,role,text,status,created) VALUES(?,?,?,?,?)',(chat['id'],'user','UNSELECTED_PRIVATE_MESSAGE','completed',time.time()))
  docs=[]
  for name,body in [('paper.pdf',pdf_bytes(('SELECTED_FIRST_PAGE','UNSELECTED_PRIVATE_PAGE_TWO','SELECTED_THIRD_PAGE'))),('notes.docx',docx_bytes()),('guide.md',b'# Selected\nMARKDOWN_SOURCE')]:docs.append(runtime.documents.upload(chat['id'],name,base64.b64encode(body).decode()))
  deadline=time.monotonic()+15
  while any(d['status']=='processing' for d in runtime.documents.list(chat['id'])) and time.monotonic()<deadline:time.sleep(.05)
  assert all(d['status']=='ready' for d in runtime.documents.list(chat['id']))
  os.environ['WORKBENCH_REAL_TEST_SOURCES']='1';os.environ['WORKBENCH_SOURCE_DOCUMENT']=docs[0]['id']
  server=WorkbenchServer(('127.0.0.1',0),make_handler(store,runtime));threading.Thread(target=server.serve_forever,daemon=True).start();errors=[]
  try:
   with sync_playwright() as driver:
    browser=driver.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce');page.on('pageerror',lambda e:errors.append(str(e)));host=f'http://127.0.0.1:{server.server_port}'
    def choose(id,value):page.locator(f'[data-select-for="{id}"]').click();page.locator(f'#{id}-menu [data-value="{value}"]').click()
    page.goto(host,wait_until='load');page.locator('#new-task').click();page.locator('#prompt').fill('核查三种文档并引用来源');page.locator('#source-settings').evaluate('(e)=>e.open=true');choose('executor-chat',chat['id']);expect(page.locator('#source-documents .source-document')).to_have_count(3)
    page.locator('#source-messages').evaluate('(e)=>e.parentElement.open=true');page.locator('#source-message-'+str(message)).check();page.locator('#source-documents').evaluate('(e)=>e.parentElement.open=true')
    for doc in docs:page.locator('#source-doc-'+doc['id']).check()
    page.locator('#source-pages-'+docs[0]['id']).fill('1,3');page.locator('#source-constraints').fill('必须使用中文，引用所选原文');page.locator('#source-budget').fill('1024');page.locator('#source-preview').click();expect(page.locator('#source-preview-status')).to_contain_text('超出预算');page.locator('#new-form [type=submit]').click();expect(page.locator('#new-error')).to_contain_text('超出预算');assert not store.runs()
    page.locator('#source-budget').fill('16000');page.locator('#source-preview').click();expect(page.locator('#source-preview-status')).to_contain_text('预算内');expect(page.locator('#new-error')).to_be_hidden();expect(page.locator('#source-preview-text')).to_contain_text('SELECTED_FIRST_PAGE');expect(page.locator('#source-preview-text')).not_to_contain_text('UNSELECTED_PRIVATE_PAGE_TWO');expect(page.locator('#source-preview-text')).not_to_contain_text('UNSELECTED_PRIVATE_MESSAGE')
    for width in (1280,1440,1920):
     page.set_viewport_size({'width':width,'height':1000});page.locator('#source-preview').scroll_into_view_if_needed();assert page.locator('#new-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1');page.screenshot(path=str(out/f'preview-{width}.png'),full_page=True)
    page.locator('#new-form [type=submit]').click();expect(page.locator('#detail-dialog')).to_be_visible();page.locator('#tab-context').click();expect(page.locator('#task-context-content')).to_contain_text('创建时的已选资料');page.locator('.source-citations').filter(has_text='paper.pdf').get_by_role('button',name='页 1',exact=True).click();expect(page.locator('#paper-text')).to_contain_text('SELECTED_FIRST_PAGE');page.locator('#paper-next').click();expect(page.locator('#paper-text')).to_contain_text('SELECTED_THIRD_PAGE');expect(page.locator('#paper-page')).to_contain_text('3 / 3');expect(page.locator('#paper-next')).to_be_disabled();page.locator('#paper-prev').click();expect(page.locator('#paper-text')).to_contain_text('SELECTED_FIRST_PAGE');page.locator('[data-close=paper-dialog]').click();page.screenshot(path=str(out/'task-sources.png'),full_page=True)
    first=store.runs()[0]['id'];page.locator('#detail-actions').get_by_role('button',name='开始任务',exact=True).click();expect(page.locator('#detail-actions')).to_contain_text('已完成',timeout=20000);assert store.run(first)['summary']=='SOURCE_SCOPE_OK';page.locator('#tab-records').click();page.screenshot(path=str(out/'pi-execution.png'),full_page=True);runtime.documents._save_pages(docs[0]['id'],['CHANGED_SOURCE']);assert runtime.sources.tool(first,{'action':'read','document_id':docs[0]['id'],'page':3})['text'].strip()=='SELECTED_THIRD_PAGE'
    # Same selected snapshot reaches the external execution input.
    page.locator('[data-close=detail-dialog]').click();page.locator('#new-task').click();choose('task-executor','codex');page.locator('#prompt').fill('外部资料交接');page.locator('#source-settings').evaluate('(e)=>e.open=true');choose('executor-chat',chat['id']);expect(page.locator('#source-documents .source-document')).to_have_count(3);page.locator('#source-documents').evaluate('(e)=>e.parentElement.open=true');page.locator('#source-doc-'+docs[2]['id']).check();page.locator('#source-preview').click();expect(page.locator('#source-preview-status')).to_contain_text('预算内');page.locator('#start-now').check();page.locator('#new-form [type=submit]').click();expect(page.locator('#detail-actions')).to_contain_text('已完成',timeout=15000)
    external=next(r for r in store.runs() if r['provider_id']=='codex');assert runtime.executors.overview(external['id'])['packet']['selected_sources']['documents'][0]['id']==docs[2]['id'];assert not errors,errors
    (out/'acceptance.json').write_text(json.dumps({'passed':True,'formats':['pdf','docx','md'],'page_errors':errors,'over_budget_blocked':True,'unselected_excluded':True,'noncontiguous_navigation':True,'snapshot_stable_after_edit':True,'external_handoff':True,'online_model_calls':0,'actual_pi':runtime.config.version,'pi_scope_tools_checked':True},indent=2));browser.close()
  finally:runtime.shutdown();server.shutdown();server.server_close();store.db.close()
if __name__=='__main__':main()
