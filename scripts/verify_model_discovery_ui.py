#!/usr/bin/env python3
"""Isolated model catalog selection, errors, credential scope, and stale-response UI."""
import json
import sys
import tempfile
import threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests/fixtures'))
from model_api import ModelAPI
from workbench.runtime import Runtime
from workbench.store import Store
from workbench.__main__ import WorkbenchServer,make_handler
from playwright.sync_api import sync_playwright,expect

def main():
 out=ROOT/'artifacts/model-discovery-ui';out.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp);workspace=root/'project';workspace.mkdir();store=Store(root/'data/state.sqlite3');runtime=Runtime(store);runtime.config.binary=str(ROOT/'tests/fixtures/fake_pi.py');runtime.config.version='fixture';runtime.config.add_workspace(str(workspace));catalog=ModelAPI().start();server=WorkbenchServer(('127.0.0.1',0),make_handler(store,runtime));threading.Thread(target=server.serve_forever,daemon=True).start();errors=[]
  try:
   with sync_playwright() as driver:
    browser=driver.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce');page.on('pageerror',lambda e:errors.append(str(e)));host=f'http://127.0.0.1:{server.server_port}';page.goto(host,wait_until='load');page.locator('#settings-open').click();page.locator('#settings-tab-models').click();expect(page.locator('#pi-api-list')).to_contain_text('还没有')
    for name,value in [('id','discovery-ui'),('name','本地模型目录'),('base',catalog.url),('key','catalog-fixture-key'),('models','manual-model')]:page.locator('#pi-api-'+name).fill(value)
    page.locator('#pi-model-fetch').click();expect(page.locator('#pi-model-status')).to_contain_text('读取到 4 个');expect(page.locator('#pi-api-models')).to_have_value('manual-model');assert not runtime.models.path.exists()
    page.locator('#pi-model-search').fill('fast');expect(page.locator('#pi-model-results input')).to_have_count(1);page.locator('#pi-model-results input[value=fast]').check();page.locator('#pi-model-import').click();expect(page.locator('#pi-api-models')).to_have_value('manual-model\nfast');page.locator('#pi-model-import').click();expect(page.locator('#pi-api-models')).to_have_value('manual-model\nfast');page.locator('#pi-api-form [type=submit]').click();expect(page.locator('#settings-message')).to_contain_text('API 连接已保存')
    page.locator('#pi-api-list').get_by_role('button',name='编辑',exact=True).click();expect(page.locator('#pi-api-key')).to_have_value('');page.locator('#pi-model-fetch').click();expect(page.locator('#pi-model-status')).to_contain_text('读取到 4 个');expect(page.locator('#pi-model-results input[value=fast]')).to_be_checked();page.locator('#pi-api-key').fill('')
    for width in (1280,1440,1920):
     page.set_viewport_size({'width':width,'height':1000});page.locator('#pi-model-picker').scroll_into_view_if_needed();assert page.locator('#settings-panel-models').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1');page.screenshot(path=str(out/f'catalog-{width}.png'),full_page=True)
    count=len(catalog.catalog_calls);page.locator('#pi-api-base').fill(catalog.url+'/missing');page.locator('#pi-model-fetch').click();expect(page.locator('#pi-model-status')).to_contain_text('重新填写密钥');assert len(catalog.catalog_calls)==count
    page.locator('#pi-api-key').fill('catalog-fixture-key');page.locator('#pi-model-fetch').click();expect(page.locator('#pi-model-status')).to_contain_text('未提供模型目录');expect(page.locator('#pi-api-models')).to_have_value('manual-model\nfast')
    # A late response from the old endpoint cannot repopulate a changed form.
    page.locator('#pi-api-base').fill(catalog.url);catalog.catalog_mode='slow';page.locator('#pi-model-fetch').click();expect(page.locator('#pi-model-fetch')).to_be_disabled();page.locator('#pi-api-base').fill(catalog.url+'/changed');page.wait_for_timeout(750);expect(page.locator('#pi-model-picker')).to_be_hidden();expect(page.locator('#pi-model-status')).to_have_text('');catalog.catalog_mode='normal'
    page.locator('[data-close=settings-dialog]').first.click();page.locator('#settings-open').click();page.locator('#settings-tab-models').click();expect(page.locator('#pi-api-key')).to_have_value('');expect(page.locator('#pi-model-picker')).to_be_hidden();assert not errors,errors;assert not catalog.calls
    (out/'acceptance.json').write_text(json.dumps({'passed':True,'page_errors':errors,'catalog_get_requests':len(catalog.catalog_calls),'model_generation_requests':len(catalog.calls),'manual_preserved':True,'saved_key_scope_checked':True,'stale_response_ignored':True,'paid_calls':0},ensure_ascii=False,indent=2));browser.close()
  finally:runtime.shutdown();server.shutdown();server.server_close();catalog.close();store.db.close()
if __name__=='__main__':main()
