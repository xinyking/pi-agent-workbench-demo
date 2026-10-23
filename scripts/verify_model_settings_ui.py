#!/usr/bin/env python3
"""Isolated desktop settings + real Pi routing to a local deterministic API."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests/fixtures'))
from model_api import ModelAPI
from workbench.runtime import Runtime
from workbench.store import Store
from workbench.__main__ import WorkbenchServer,make_handler
from playwright.sync_api import sync_playwright,expect

def main():
 out=ROOT/'artifacts/model-settings-ui';out.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'PI_CODING_AGENT_DIR':str(Path(temp)/'isolated-pi')}):
  root=Path(temp);workspace=root/'project';workspace.mkdir();store=Store(root/'data/state.sqlite3');runtime=Runtime(store);runtime.config.add_workspace(str(workspace));api=ModelAPI().start();server=WorkbenchServer(('127.0.0.1',0),make_handler(store,runtime));threading.Thread(target=server.serve_forever,daemon=True).start();errors=[]
  try:
   with sync_playwright() as driver:
    browser=driver.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce');page.on('pageerror',lambda e:errors.append(str(e)));host=f'http://127.0.0.1:{server.server_port}'
    def choose(id,value):
     page.locator(f'[data-select-for="{id}"]').click();page.locator(f'#{id}-menu [data-value="{value}"]').click()
    page.goto(host,wait_until='load');page.locator('#settings-open').click();page.locator('#settings-tab-models').click();expect(page.locator('#pi-api-list')).to_contain_text('还没有')
    for id,value in [('id','local-ui'),('name','本地路由验证'),('base',api.url),('key','fixture-key-no-live-secret'),('models','main\nfast\nreview')]:page.locator('#pi-api-'+id).fill(value)
    page.locator('#pi-api-form [type=submit]').click();expect(page.locator('#settings-message')).to_contain_text('API 连接已保存');expect(page.locator('#pi-api-key')).to_have_value('');expect(page.locator('#pi-api-list')).to_contain_text('已保存密钥')
    public=page.request.get(host+'/api/settings/models').text();assert 'fixture-key-no-live-secret' not in public
    choose('pi-default-model','wbapi-local-ui/main');page.locator('#pi-default-form [type=submit]').click();expect(page.locator('#settings-message')).to_contain_text('默认模型已保存')
    page.locator('#pi-api-list').get_by_role('button',name='编辑',exact=True).click();expect(page.locator('#pi-api-key')).to_have_value('');expect(page.locator('#pi-api-key')).to_have_attribute('placeholder','留空保留已保存密钥')
    for width in (1280,1440,1920):
     page.set_viewport_size({'width':width,'height':1000});page.locator('#settings-panel-models').evaluate('(e)=>e.scrollTop=0');assert page.locator('#settings-panel-models').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1');page.screenshot(path=str(out/f'settings-{width}.png'),full_page=True)
    page.locator('#pi-api-form').scroll_into_view_if_needed();page.screenshot(path=str(out/'api-form.png'),full_page=True)
    page.locator('[data-close=settings-dialog]').first.click();page.locator('#agents-open').click()
    choose('agent-llm-task-scout','wbapi-local-ui/fast');expect(page.locator('#agents-message')).to_contain_text('Agent 模型已保存');choose('agent-llm-task-reviewer','wbapi-local-ui/review');expect(page.locator('#agents-message')).to_contain_text('Agent 模型已保存');page.screenshot(path=str(out/'agent-models.png'),full_page=True)
    page.locator('[data-close=agents-dialog]').first.click();page.locator('#new-task').click();expect(page.locator('#model-choice-menu')).to_have_count(1);page.locator('#prompt').fill('请委派两个专家核验，并报告完成');page.locator('#start-now').check();page.locator('#new-form [type=submit]').click();expect(page.locator('#detail-actions')).to_contain_text('已完成',timeout=30000);page.locator('#tab-records').click();expect(page.locator('#messages')).to_contain_text('模型 main 已完成本地核验');assert store.runs()[0]['summary']=='本地模型路由与交付验证完成';page.screenshot(path=str(out/'routed-task.png'),full_page=True)
    assert set(api.calls)=={'main','fast','review'},api.calls
    assert not errors,errors
    # Reopen and reload settings to exercise persistence and menu lifecycle.
    page.locator('[data-close=detail-dialog]').click();page.reload(wait_until='load');page.locator('#settings-open').click();page.locator('#settings-tab-models').click();expect(page.locator('#pi-default-model')).to_have_value('wbapi-local-ui/main');expect(page.locator('#pi-api-key')).to_have_value('');expect(page.locator('#pi-default-model-menu')).to_have_count(1)
    record={'passed':True,'page_errors':errors,'actual_pi':runtime.config.version,'local_models_called':sorted(set(api.calls)),'key_not_returned':True,'paid_calls':0,'viewports':[1280,1440,1920]};(out/'acceptance.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));browser.close()
  finally:runtime.shutdown();server.shutdown();server.server_close();api.close();store.db.close()
if __name__=='__main__':main()
