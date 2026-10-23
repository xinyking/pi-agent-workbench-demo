#!/usr/bin/env python3
"""Isolated Chromium acceptance for memory consent, scope, conflicts and forgetting."""
import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.__main__ import WorkbenchServer,make_handler
from workbench.runtime import Runtime
from workbench.store import Store


def main():
    from playwright.sync_api import sync_playwright,expect
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=ROOT/'artifacts/private-memory-ui');args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workbench-memory-ui-') as temporary:
        root=Path(temporary);workspace=root/'project';workspace.mkdir();other=root/'other';other.mkdir()
        store=Store(root/'data/state.sqlite3');runtime=Runtime(store);runtime.config.binary=str(ROOT/'tests/fixtures/fake_pi.py');runtime.config.version='memory-ui-fixture';runtime.config.add_workspace(str(workspace));runtime.config.add_workspace(str(other))
        old=runtime.create('开启前的任务：以后回答都用英文',str(workspace))
        server=WorkbenchServer(('127.0.0.1',0),make_handler(store,runtime));serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start();errors=[]
        try:
            with sync_playwright() as driver:
                browser=driver.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce');page.on('pageerror',lambda error:errors.append(str(error)));host=f'http://127.0.0.1:{server.server_port}'
                def settings():
                    page.locator('#settings-open').click();page.locator('#settings-tab-memory').click();expect(page.locator('#memory-policy-form')).to_be_visible()
                def save():
                    page.get_by_role('button',name='保存记忆设置',exact=True).click();expect(page.locator('#settings-message')).to_contain_text('记忆设置已保存')
                page.goto(host,wait_until='load');settings()
                for key in ('auto_constraints','long_term_read','long_term_write'):expect(page.locator('#memory-'+key)).not_to_be_checked()
                expect(page.locator('#memory-task_memory')).to_be_checked()
                for key in ('auto_constraints','long_term_read','long_term_write'):page.locator('#memory-'+key).check()
                save();page.reload(wait_until='load');settings()
                for key in ('auto_constraints','long_term_read','long_term_write'):expect(page.locator('#memory-'+key)).to_be_checked()
                assert not runtime.task_context.overview(old)['items']
                first=runtime.create('以后回答都用中文，预算不超过200元，禁止联网',str(workspace))
                page.locator('#settings-tab-general').click();page.locator('#settings-tab-memory').click()
                item=page.locator('.memory-manager-item').filter(has_text='以后回答都用中文')
                expect(item).to_contain_text('待确认');expect(page.locator('.memory-manager-item')).to_have_count(1)
                item.get_by_role('button',name='确认使用',exact=True).click();expect(page.locator('.memory-manager-item')).to_contain_text('已启用')
                for width in (1280,1440,1920):
                    page.set_viewport_size({'width':width,'height':1000});page.locator('#settings-panel-memory').evaluate('(e)=>e.scrollTop=0')
                    assert page.locator('#settings-panel-memory').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    expect(page.locator('#memory-retention-menu')).to_have_count(1)
                    expect(page.locator('#long-memory-scope-menu')).to_have_count(1)
                    assert page.locator('.memory-toggle').first.evaluate('(e)=>getComputedStyle(e).flexDirection')=='row'
                    page.screenshot(path=str(args.output/f'memory-settings-{width}.png'),full_page=True)
                page.locator('[data-close="settings-dialog"]').click()
                second=runtime.create('预算改为300元',str(workspace));runtime.task_context.assemble(second)
                projection=json.loads(runtime.task_context.assemble(second)['text']);assert projection['long_term_memory'][0]['text']=='以后回答都用中文'
                assert not runtime.task_context.overview(runtime.create('检查另一项目',str(other)))['long_term']
                page.reload(wait_until='load');page.locator('#board .task').filter(has_text='预算改为300元').click();page.locator('#tab-context').click()
                expect(page.locator('#task-context-content')).to_contain_text('来源：自动提炼 · 用户原文')
                expect(page.locator('#task-context-content')).to_contain_text('以后回答都用中文')
                constraint=page.locator('.memory-item').filter(has_text='预算改为300元');constraint.get_by_role('button',name='编辑',exact=True).click();page.locator('[data-memory-draft]').fill('预算不超过100元');page.get_by_role('button',name='保存修改',exact=True).click();expect(page.locator('.memory-item')).to_contain_text('预算不超过100元')
                runtime.task_context.request(second,'预算改为400元');runtime.task_context.assemble(second)
                expect(page.locator('.memory-conflict')).to_contain_text('预算改为400元');page.locator('.memory-conflict').get_by_role('button',name='保留现有约束',exact=True).click();expect(page.locator('.memory-conflict')).to_have_count(0);expect(page.locator('.memory-item')).to_contain_text('预算不超过100元')
                page.locator('#detail-dialog .dialog-scroll').evaluate('(e)=>e.scrollTop=0');page.screenshot(path=str(args.output/'automatic-constraints-and-recall.png'),full_page=True)
                page.locator('[data-close="detail-dialog"]').click();settings()
                for key in ('task_memory','auto_constraints','long_term_read','long_term_write','tool_offload','assembly_history'):page.locator('#memory-'+key).uncheck()
                save();assert runtime.task_context.assemble(second)['enabled'] is False
                item=page.locator('.memory-manager-item');expect(item.get_by_role('button',name='编辑',exact=True)).to_be_disabled();item.get_by_role('button',name='删除',exact=True).click();expect(page.locator('.memory-manager-item')).to_have_count(0)
                # Manual writes and explicitly global scope remain available when consented.
                page.locator('#memory-long_term_write').check();save()
                page.locator('#long-memory-text').fill('交付格式使用Markdown')
                page.locator('[data-select-for="long-memory-scope"]').click();page.locator('#long-memory-scope-menu [role="option"]').filter(has_text='全局偏好').click()
                page.get_by_role('button',name='保存长期记忆',exact=True).click();expect(page.locator('.memory-manager-item')).to_contain_text('全局偏好（所有项目）')
                entry=page.locator('.memory-manager-item');entry.get_by_role('button',name='编辑',exact=True).click();page.locator('#long-memory-text').fill('交付格式使用JSON');page.get_by_role('button',name='保存修改',exact=True).click();expect(page.locator('.memory-manager-item')).to_contain_text('交付格式使用JSON')
                page.get_by_role('button',name='清除全部长期记忆',exact=True).click();page.locator('.memory-clear-confirm').get_by_role('button',name='确认清除',exact=True).click();expect(page.locator('.memory-manager-item')).to_have_count(0)
                before=store.messages(second);page.get_by_role('button',name='清除并关闭额外任务记忆',exact=True).click();page.locator('.memory-clear-confirm').get_by_role('button',name='确认清除',exact=True).click();expect(page.locator('#settings-message')).to_contain_text('已清除');assert store.messages(second)==before;assert store.run(second)['prompt']=='预算改为300元'
                page.locator('#settings-panel-memory').evaluate('(e)=>e.scrollTop=0');page.screenshot(path=str(args.output/'memory-recording-disabled.png'),full_page=True)
                assert not errors,errors
                (args.output/'acceptance.json').write_text(json.dumps({'settings_persist':True,'opt_in':True,'no_history_backfill':True,'pending_confirmation':True,'scope_isolation':True,'conflict_resolution':True,'disable_stops_injection':True,'delete_with_writes_off':True,'manual_global_edit':True,'clear_preserves_task_history':True,'page_errors':errors},indent=2))
                browser.close()
            print('PASS: 记忆开关持久化、默认关闭、不回扫、候选确认、项目隔离、自动约束/冲突、关闭即时生效、全局手工维护及清理；桌面1280/1440/1920px')
        finally:
            runtime.shutdown();server.shutdown();server.server_close();serving.join(timeout=2);store.db.close()


if __name__=='__main__':main()
