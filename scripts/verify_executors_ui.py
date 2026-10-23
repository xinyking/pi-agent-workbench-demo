#!/usr/bin/env python3
"""Browser acceptance of managed CLI task creation, context and result display."""
import json
import sys
import tempfile
import threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from workbench.runtime import Runtime
from workbench.store import Store
from workbench.__main__ import WorkbenchServer,make_handler
from playwright.sync_api import sync_playwright,expect

def main():
 out=ROOT/'artifacts/executors-ui';out.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as temp:
  root=Path(temp);workspace=root/'project';workspace.mkdir();store=Store(root/'data/state.sqlite3');runtime=Runtime(store);runtime.config.add_workspace(str(workspace));runtime.config.binary=str(ROOT/'tests/fixtures/fake_pi.py');runtime.config.version='fixture';runtime.executors.binaries={'codex':str(ROOT/'tests/fixtures/fake_executor.py'),'claude':str(ROOT/'tests/fixtures/fake_executor.py')}
  server=WorkbenchServer(('127.0.0.1',0),make_handler(store,runtime));threading.Thread(target=server.serve_forever,daemon=True).start();errors=[]
  try:
   with sync_playwright() as driver:
    browser=driver.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)));page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='load')
    page.locator('#new-task').click();page.locator('[data-select-for=task-executor]').click();page.get_by_role('option',name='Codex CLI',exact=True).click();expect(page.locator('#executor-options')).to_be_visible()
    page.locator('#prompt').fill('检查交付文档');page.locator('#source-settings').evaluate('(e)=>e.open=true');page.locator('#executor-context').fill('仅使用中文；检查来源保持');page.locator('#start-now').check();page.locator('#source-preview').click();expect(page.locator('#source-preview-status')).to_contain_text('预算内');page.screenshot(path=str(out/'new-task.png'),full_page=True)
    page.locator('#new-form [type=submit]').click();expect(page.locator('#detail-dialog')).to_be_visible();expect(page.locator('#detail-actions')).to_contain_text('已完成',timeout=15000)
    page.locator('#tab-context').click();expect(page.locator('#task-context-content')).to_contain_text('仅使用中文');page.locator('#task-context-content summary').filter(has_text='查看实际初始输入').click();page.screenshot(path=str(out/'context.png'),full_page=True)
    page.locator('#tab-records').click();expect(page.locator('.timeline-agent')).to_have_count(1);expect(page.locator('#messages')).to_contain_text('隔离 CLI 已读取任务上下文并完成检查');page.screenshot(path=str(out/'records.png'),full_page=True)
    page.locator('[data-close="detail-dialog"]').click()
    page.locator('#new-task').click();page.locator('[data-select-for=task-executor]').click();page.get_by_role('option',name='Claude Code',exact=True).click();page.locator('#prompt').fill('Claude 分支验收');page.locator('#new-form [type=submit]').click();expect(page.locator('#detail-actions')).to_contain_text('已完成',timeout=15000)
    page.locator('#tab-records').click();expect(page.locator('#messages')).to_contain_text('隔离 CLI 已读取任务上下文并完成检查');expect(page.locator('.timeline-agent')).to_have_count(1)
    page.locator('[data-close="detail-dialog"]').click();page.locator('#new-task').click();page.locator('#prompt').fill('HANG_TEST 停止验收');page.locator('#new-form [type=submit]').click();expect(page.locator('#detail-actions')).to_contain_text('执行中',timeout=15000);page.locator('#detail-actions').get_by_role('button',name='停止',exact=True).click();expect(page.locator('#detail-actions')).to_contain_text('已停止',timeout=15000)
    for width in (1280,1440,1920):
     page.set_viewport_size({'width':width,'height':1000});assert page.locator('#detail-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1');page.screenshot(path=str(out/f'stopped-{width}.png'),full_page=True)
    assert not errors,errors
    (out/'acceptance.json').write_text(json.dumps({'passed':True,'page_errors':errors,'vendor':'codex and claude fixtures; cancel verified','paid_calls':0},ensure_ascii=False,indent=2));browser.close()
  finally:runtime.shutdown();server.shutdown();server.server_close();store.db.close()
if __name__=='__main__':main()
