#!/usr/bin/env python3
"""Real Chromium E2E, isolated data, deterministic Pi peer; no model API calls.
Run outside restricted execution sandboxes: python scripts/verify_ui.py
Requires the development-only Python playwright package and Chromium.
"""
import argparse
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.__main__ import WorkbenchServer,make_handler
from workbench.runtime import Runtime
from workbench.store import Store


def wait_for(condition):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        if condition():return
        time.sleep(.03)
    raise AssertionError('等待真实后端状态超时')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/ui')
    parser.add_argument('--desktop-only',action='store_true',help='仅运行桌面宽度检查')
    args=parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright,expect
    except ImportError:
        parser.exit(2,'需要 playwright；先执行 python -m pip install playwright 和 python -m playwright install chromium。\n')
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workbench-ui-') as temporary:
        workspace=Path(temporary)/'project';workspace.mkdir()
        store=Store(Path(temporary)/'data/state.sqlite3')
        runtime=Runtime(store)
        runtime.config.binary=str(ROOT/'tests/fixtures/fake_pi.py')
        runtime.config.version='ui-fixture'
        runtime.config.add_workspace(str(workspace))
        server=WorkbenchServer(('127.0.0.1',0),make_handler(store,runtime))
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        errors=[]
        try:
            with sync_playwright() as browser_driver:
                browser=browser_driver.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.add_init_script("""(()=>{const Native=window.EventSource;window.__ownedStreams=[];window.EventSource=class extends Native{constructor(url,options){super(url,options);const entry={url:String(url),closed:false};window.__ownedStreams.push(entry);this.close=()=>{entry.closed=true;super.close();};}};})();""")
                def review_size(width,height):
                    if args.desktop_only and width<1000:return
                    page.set_viewport_size({'width':width,'height':height})
                def capture(name):
                    if args.desktop_only and ('mobile' in name or '390' in name):return
                    page.screenshot(path=str(args.output/name),full_page=True)
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='load')
                expect(page.locator('#new-conversation')).to_be_visible()
                # Semantic text pairs must remain readable across the shared palette.
                palette=page.evaluate("""()=>{const s=getComputedStyle(document.documentElement);return Object.fromEntries(['canvas','surface','sidebar','ink','secondary','muted','accent','accent-soft','success','success-soft','warning','warning-soft','danger','danger-soft'].map(k=>[k,s.getPropertyValue('--'+k).trim()]));}""")
                def luminance(value):
                    rgb=[int(value[i:i+2],16)/255 for i in (1,3,5)]
                    rgb=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
                    return sum(v*w for v,w in zip(rgb,(.2126,.7152,.0722)))
                contrasts={}
                for foreground,background in [('ink','canvas'),('secondary','surface'),('muted','canvas'),('muted','sidebar'),('surface','accent'),('accent','accent-soft'),('success','success-soft'),('warning','warning-soft'),('danger','danger-soft')]:
                    a,b=sorted((luminance(palette[foreground]),luminance(palette[background])))
                    ratio=(b+.05)/(a+.05);contrasts[foreground+'/'+background]=round(ratio,2)
                    assert ratio>=4.5,(foreground,background,ratio)
                import json
                (args.output/'color-contrast.json').write_text(json.dumps(contrasts,indent=2))

                # Regression: hover must never switch scrollbar implementations and shrink content.
                for css_class in ('dialog-scroll','cards','workspace-options'):
                    page.evaluate("""cls=>{const p=document.createElement('div');p.id='hover-scroll-probe';p.className=cls;Object.assign(p.style,{position:'fixed',left:'600px',top:'300px',width:'280px',height:'120px',maxHeight:'120px',overflow:'auto',display:'block',zIndex:'9999'});const child=document.createElement('div');Object.assign(child.style,{height:'300px',width:'100%'});child.textContent='滚动区域悬停稳定性检查';p.append(child);document.body.append(p);} """,css_class)
                    probe=page.locator('#hover-scroll-probe')
                    page.mouse.move(0,0)
                    before=probe.evaluate('(e)=>({width:e.clientWidth,x:e.firstChild.getBoundingClientRect().x,childWidth:e.firstChild.getBoundingClientRect().width})')
                    probe.hover()
                    after=probe.evaluate('(e)=>({width:e.clientWidth,x:e.firstChild.getBoundingClientRect().x,childWidth:e.firstChild.getBoundingClientRect().width})')
                    assert before==after,(css_class,before,after)
                    probe.evaluate('(e)=>e.remove()')

                def choose_select(select_id,value):
                    page.locator(f'[data-select-for="{select_id}"]').click()
                    page.locator(f'#{select_id}-menu [data-value="{value}"]').click()

                def select_workspace(select_id):
                    picker=page.locator('#'+select_id+' + .workspace-picker')
                    picker.get_by_role('combobox').click()
                    picker.get_by_role('option').filter(has_text=str(workspace)).click()

                def new_chat(mode):
                    page.locator('#new-conversation').click()
                    expect(page.locator('#chat-view')).to_be_visible()
                    expect(page.locator('#chat-composer-mode')).to_have_text('普通对话')
                    expect(page.locator('dialog[open]')).to_have_count(0)
                    select_workspace('chat-workspace')
                    if mode=='planning':
                        page.locator('#chat-mode-picker summary').click()
                        page.locator('[data-chat-mode="planning"]').click()
                        expect(page.locator('#chat-composer-mode')).to_have_text('任务规划')

                def send(text):
                    page.locator('#chat-input').fill(text)
                    page.locator('#chat-send').click()
                    expect(page.locator('#chat-input')).to_have_value('',timeout=15000)
                    wait_for(lambda:not runtime.conversations.active and (not runtime.active or any(r['status'] in ('waiting_input','waiting_approval') for r in store.runs())))
                    if page.locator('#chat-composer-mode').inner_text()=='普通对话':
                        expect(page.locator('#chat-status')).to_have_text('回答已完成',timeout=15000)

                new_chat('chat')
                capture('chat-welcome-desktop.png')
                if args.desktop_only:
                    for width,height in ((1280,800),(1920,1080)):
                        review_size(width,height)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        expect(page.locator('#chat-input')).to_be_in_viewport()
                        expect(page.locator('[data-chat-view="documents"]')).to_be_in_viewport()
                        header=page.locator('.chat-header').bounding_box()
                        tabs=page.locator('.chat-tabs').bounding_box()
                        assert header['y']<=tabs['y'] and tabs['y']+tabs['height']<=header['y']+header['height']
                        capture(f'chat-welcome-{width}.png')
                    review_size(1440,1000)
                page.locator('[data-chat-view="documents"]').click()
                expect(page.locator('#chat-document-empty')).to_be_visible()
                expect(page.locator('#chat-messages')).to_be_hidden()
                page.locator('[data-chat-view="conversation"]').click()
                page.locator('.chat-suggestions button').first.click()
                expect(page.locator('#chat-input')).to_have_value('梳理这个项目的架构与改进方向')
                expect(page.locator('.chat-user')).to_have_count(0)
                page.locator('#chat-input').fill('')
                page.locator('[data-chat-view="trace"]').click()
                expect(page.locator('#trace-entries')).to_contain_text('发送消息后')
                capture('chat-empty-desktop.png')
                for width in (390,1440):
                    review_size(width,900)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), '页面横向溢出'
                    expect(page.locator('#chat-trace-close')).to_be_in_viewport()
                    expect(page.locator('#chat-input')).to_be_in_viewport()
                    capture(f'chat-empty-{width}.png')
                page.locator('#chat-trace-close').click()
                expect(page.locator('[data-chat-view="conversation"]')).to_be_focused()
                review_size(390,844)
                capture('chat-welcome-mobile.png')
                review_size(1440,1000)
                send('三年 Python 开发经验，目标杭州，不考虑外包')
                expect(page.locator('.chat-assistant').last).to_contain_text('普通回答')
                page.locator('[data-chat-view="trace"]').click()
                expect(page.locator('#trace-entries .waterfall-axis')).to_contain_text('耗时')
                model_step=page.locator('#trace-entries .trace-entry[data-kind="model"]').first
                expect(model_step.locator('.waterfall-duration')).not_to_have_text('—')
                model_step.locator(':scope > summary').click()
                expect(model_step).to_contain_text('输入 2')
                expect(model_step).to_contain_text('费用未知')
                page.locator('#chat-trace-close').click()
                sidebar=page.locator('.sidebar').bounding_box()
                assert 250<=sidebar['width']<=260,sidebar
                nav_weights=page.locator('.sidebar .nav').evaluate_all('(nodes)=>nodes.map(n=>getComputedStyle(n).fontWeight)')
                assert len(set(nav_weights))==1,nav_weights
                expect(page.locator('#conversation-list .conversation-item small')).to_have_count(0)
                expect(page.locator('#conversation-list .conversation-open').first).to_have_attribute('title',re.compile('普通对话'))
                expect(page.locator('.sidebar #usage')).to_have_count(0)
                expect(page.locator('.sidebar [aria-current="page"]')).to_have_count(1)
                assert not store.runs(),'普通问答错误地产生了任务'
                page.locator('#conversation-list .conversation-item').first.hover()
                page.locator('#conversation-list .conversation-more').first.click()
                page.locator('#conversation-title-input').fill('论文阅读测试')
                page.locator('#conversation-rename-save').click()
                expect(page.locator('#chat-title')).to_have_text('论文阅读测试')
                expect(page.locator('#conversation-list .conversation-title').first).to_have_text('论文阅读测试')

                composer=page.locator('#chat-form').bounding_box()
                assert composer['height']<180,composer
                page.locator('#chat-mode-picker summary').click()
                page.locator('[data-chat-mode="planning"]').click()
                expect(page.locator('#chat-composer-mode')).to_have_text('任务规划')
                assert not store.runs(),'切换模式不应自动创建任务'
                page.locator('#chat-mode-picker summary').click()
                page.locator('[data-chat-mode="chat"]').click()
                expect(page.locator('#chat-composer-mode')).to_have_text('普通对话')
                expect(page.locator('.chat-assistant').last).to_contain_text('普通回答')
                capture('composer.png')
                page.locator('#paper-upload').set_input_files({'name':'paper.txt','mimeType':'text/plain','buffer':'研究设计：样本量为 120。'.encode()})
                expect(page.locator('#chat-documents')).to_contain_text('可阅读')
                page.locator('[data-chat-view="documents"]').click()
                expect(page.locator('#chat-document-empty')).to_be_hidden()
                expect(page.locator('#chat-document-view .document-card')).to_contain_text('paper.txt')
                capture('chat-documents-desktop.png')
                page.locator('#chat-input').fill('保留这份未发送草稿')
                review_size(390,844)
                expect(page.locator('#chat-add-document')).to_be_in_viewport()
                expect(page.locator('#chat-document-view .document-card')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                capture('chat-documents-mobile.png')
                page.locator('[data-chat-view="conversation"]').focus()
                page.keyboard.press('Enter')
                expect(page.locator('#chat-messages')).to_be_visible()
                expect(page.locator('#chat-input')).to_have_value('保留这份未发送草稿')
                page.locator('#chat-input').fill('')
                review_size(1440,1000)
                page.get_by_role('button',name='查看原文',exact=True).click()
                expect(page.locator('#paper-text')).to_contain_text('样本量为 120')
                page.locator('[data-close="paper-dialog"]').click()
                send('[markdown] 输出阅读笔记')
                expect(page.locator('#chat-messages .markdown-body h1').last).to_have_text('阅读笔记')
                expect(page.locator('#chat-messages .markdown-body table').last).to_contain_text('120')
                expect(page.locator('#chat-messages math mfrac').last).to_be_visible()
                expect(page.locator('#chat-status')).to_have_text('回答已完成')
                expect(page.locator('#chat-progress summary')).to_contain_text('运行过程')
                capture('markdown-notes.png')
                page.locator('#chat-input').fill('保留桌面草稿')
                page.locator('#chat-input').dispatch_event('input')
                chat_id=runtime.conversations.list()[0]['id']
                runtime.conversations.message(chat_id,'assistant','\n\n'.join(f'### 检查点 {i}\n这是一段用于验证长对话滚动和阅读位置保留的说明。' for i in range(35)))
                expect(page.locator('#chat-messages')).to_contain_text('检查点 34',timeout=15000)
                page.locator('#chat-messages').evaluate('(e)=>e.scrollTop=140')
                expect(page.locator('#chat-latest')).to_be_visible()
                position=page.locator('#chat-messages').evaluate('(e)=>e.scrollTop')
                page.locator('[data-chat-view="trace"]').click()
                runtime.conversations.execution(chat_id,'session.connected',{'name':'阅读位置回归事件'})
                expect(page.locator('#trace-entries')).to_contain_text('阅读位置回归事件',timeout=15000)
                page.locator('[data-chat-view="conversation"]').click()
                assert abs(page.locator('#chat-messages').evaluate('(e)=>e.scrollTop')-position)<3
                expect(page.locator('#chat-input')).to_have_value('保留桌面草稿')
                capture('chat-reading-desktop.png')
                page.locator('#chat-latest').click()
                expect(page.locator('#chat-latest')).to_be_hidden()
                page.locator('#chat-input').fill('')
                # Seed only the isolated database to exercise error/interruption and truncated history UI.
                chat_id=runtime.conversations.list()[0]['id']
                for kind,data in [
                    ('request.started',{}),
                    ('tool.started',{'id':'ok','name':'read','agent_id':'main','arguments':{'path':'fixture.md'}}),
                    ('tool.completed',{'id':'ok','agent_id':'main'}),
                    ('tool.started',{'id':'bad','name':'read','agent_id':'worker','role':'task-worker'}),
                    ('tool.completed',{'id':'bad','agent_id':'worker','is_error':True}),
                    ('tool.started',{'id':'interrupted','name':'grep','agent_id':'worker'}),
                    ('agent.completed',{'agent_id':'worker'}),
                    ('tool.completed',{'id':'missing-start','name':'read','agent_id':'worker','is_error':True}),
                ]:runtime.conversations.execution(chat_id,kind,data)
                page.locator('[data-chat-view="trace"]').click()
                expect(page.locator('[data-chat-view="trace"]')).to_have_attribute('aria-pressed','true')
                expect(page.locator('#trace-entries [data-status="completed"]')).to_have_count(1,timeout=15000)
                expect(page.locator('#trace-entries [data-status="failed"]')).to_have_count(2)
                expect(page.locator('#trace-entries [data-status="interrupted"]')).to_have_count(1)
                agent_picker=page.locator('[data-select-for="trace-agent"]')
                agent_picker.click()
                expect(agent_picker).to_have_attribute('aria-expanded','true')
                expect(page.locator('#trace-agent-menu')).to_be_in_viewport()
                capture('trace-agent-menu.png')
                agent_picker.click()
                expect(agent_picker).to_have_attribute('aria-expanded','false')
                agent_picker.click()
                runtime.conversations.execution(chat_id,'session.connected',{'name':'下拉框刷新回归'})
                expect(page.locator('#trace-entries')).to_contain_text('下拉框刷新回归',timeout=15000)
                expect(agent_picker).to_have_attribute('aria-expanded','true')
                expect(agent_picker).to_be_focused()
                page.keyboard.press('Escape')
                expect(agent_picker).to_be_focused()
                expect(agent_picker).to_have_attribute('aria-expanded','false')
                event_picker=page.locator('[data-select-for="trace-kind"]')
                event_picker.focus();page.keyboard.press('ArrowDown');page.keyboard.press('End')
                page.keyboard.press('Enter')
                expect(page.locator('#trace-kind')).to_have_value('failed')
                expect(page.locator('#trace-entries details')).to_have_count(3)
                event_picker.click();capture('trace-event-menu.png');page.keyboard.press('Escape')
                choose_select('trace-kind','all')
                choose_select('trace-agent','worker')
                choose_select('trace-kind','failed')
                expect(page.locator('#trace-entries details')).to_have_count(3)
                choose_select('trace-agent','main')
                choose_select('trace-kind','tools')
                page.locator('#trace-entries details').first.locator('summary').click()
                expect(page.locator('#trace-entries')).to_contain_text('耗时')
                expect(page.locator('#trace-entries')).to_contain_text('fixture.md')
                choose_select('trace-agent','all')
                choose_select('trace-kind','all')
                expect(page.locator('#chat-messages')).to_be_hidden()
                stage=page.locator('.chat-stage').bounding_box()
                trace=page.locator('#chat-trace').bounding_box()
                assert abs(stage['width']-trace['width'])<2,(stage,trace)
                page.locator('#trace-entries').evaluate('(e)=>e.scrollTop=e.scrollHeight')
                capture('chat-trace-desktop.png')
                review_size(390,844)
                expect(page.locator('#chat-trace-close')).to_be_in_viewport()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                capture('chat-trace-mobile.png')
                page.locator('#chat-trace-close').click()
                expect(page.locator('[data-chat-view="conversation"]')).to_be_focused()
                capture('chat-markdown-mobile.png')
                review_size(1440,1000)
                page.reload(wait_until='load')
                page.locator('#conversation-list .conversation-open').first.click()
                page.locator('[data-chat-view="trace"]').click()
                expect(page.locator('#trace-entries [data-status="failed"]')).to_have_count(2)
                page.locator('#chat-trace-close').click()
                send('[create-agent] 创建一个面试辅导 Agent')
                assert any(a['name']=='interview-mentor' for a in runtime.agents.list())
                send('[create-task] 请基于前面内容创建任务')
                expect(page.locator('#chat-task-open')).to_be_visible()
                wait_for(lambda:not runtime.active)
                page.locator('#chat-task-open').click()
                expect(page.locator('#tab-records')).to_have_attribute('aria-selected','true')
                page.locator('#tab-files').click()
                expect(page.locator('#task-archive')).to_be_visible()
                page.locator('#tab-flow').click()
                expect(page.locator('#flow-canvas')).to_be_visible()
                expect(page.locator('.flow-node').first).to_be_visible()
                page.locator('#tab-files').click()
                expect(page.locator('#task-archive')).to_be_visible()
                page.locator('#task-archive summary').click()
                expect(page.locator('#task-archive .markdown-body')).to_contain_text('任务完成总结')
                page.locator('#task-archive summary').click()
                page.locator('#tab-flow').click()
                stages=page.locator('#flow-rows .flow-row')
                if stages.count()>1:
                    expect(stages.nth(1)).to_be_visible()
                    dimensions=stages.evaluate_all('(nodes)=>nodes.slice(0,2).map(e=>({x:e.getBoundingClientRect().x,width:e.getBoundingClientRect().width}))')
                    first,second=dimensions
                    assert second['x']>first['x']+first['width'],(first,second)
                expect(page.locator('#flow-edges path').first).to_have_attribute('d',re.compile('C'))

                page.locator('[data-close="detail-dialog"]').click()

                heading=page.locator('#conversation-heading').bounding_box()
                projects=page.locator('#workspace-heading').bounding_box()
                assert heading['y']<projects['y'] and projects['y']>500,(heading,projects)
                page.locator('#sidebar-workspaces .workspace-item').first.click()
                expect(page.locator('#chat-view')).to_be_hidden()
                expect(page.locator('#filter-info')).to_contain_text(workspace.name)
                expect(page.locator('#sidebar-workspaces .workspace-item').first).to_have_attribute('aria-pressed','true')
                expect(page.locator('#sidebar-workspaces .workspace-task-count')).to_have_count(0)
                expect(page.locator('#board-home')).not_to_have_class(re.compile(r'\bactive\b'))
                expect(page.locator('.sidebar [aria-current="page"]')).to_have_count(1)
                page.locator('#sidebar-toggle').click()
                expect(page.locator('#sidebar-toggle')).to_have_attribute('aria-expanded','false')
                expect(page.locator('#new-conversation')).to_be_visible()
                toggle=page.locator('#sidebar-toggle').bounding_box()
                new_button=page.locator('#new-conversation').bounding_box()
                assert abs(toggle['x']+toggle['width']/2-new_button['x']-new_button['width']/2)<2,(toggle,new_button)
                workspace_icon=page.locator('#sidebar-workspaces .workspace-item .icon').first.bounding_box()
                add=page.locator('#workspace-add').bounding_box()
                assert abs(workspace_icon['x']+workspace_icon['width']/2-add['x']-add['width']/2)<1,(workspace_icon,add)
                capture('sidebar-collapsed-alignment.png')
                page.locator('#sidebar-toggle').click()
                toggle=page.locator('#sidebar-toggle').bounding_box()
                brand=page.locator('.sidebar-brand').bounding_box()
                assert abs(toggle['y']+toggle['height']/2-brand['y']-brand['height']/2)<2,(toggle,brand)
                scrollbar=page.locator('#conversation-list').evaluate('(e)=>({width:getComputedStyle(e,"::-webkit-scrollbar").width,radius:getComputedStyle(e,"::-webkit-scrollbar-thumb").borderTopLeftRadius})')
                assert scrollbar=={'width':'7px','radius':'20px'},scrollbar
                capture('sidebar-workspace.png')

                new_chat('planning')
                send('[parallel][askchildren] 调查并核验求职资料')
                wait_for(lambda:any(r['status']=='waiting_input' for r in store.runs()))
                expect(page.locator('#chat-pending .chat-question').first).to_be_visible()
                pending=page.locator('#chat-pending').bounding_box()
                assert pending['width']<=780,pending
                capture('pending-question-width.png')
                page.locator('#chat-task-open').click()
                forms=page.locator('#input-requests form')
                expect(forms).to_have_count(2)
                assert forms.first.bounding_box()['width']<=760
                capture('task-input-width.png')
                forms.first.locator('textarea').fill('有三年后端开发经验')
                forms.first.locator('button[type=submit]').click()
                expect(forms).to_have_count(1)
                forms.first.locator('textarea').fill('目标杭州，不考虑外包')
                forms.first.locator('button[type=submit]').click()
                wait_for(lambda:not runtime.active)
                expect(page.locator('#detail-actions .status')).to_have_text('已完成',timeout=15000)
                expect(page.locator('#detail-actions .status')).to_have_attribute('data-status','completed')
                page.locator('#tab-flow').click()
                flow=page.locator('#flow-canvas')
                dimensions=flow.evaluate('(e)=>({width:e.clientWidth,content:e.scrollWidth,height:e.clientHeight,contentHeight:e.scrollHeight})')
                assert dimensions['content']>dimensions['width'],dimensions
                flow.evaluate('(e)=>e.scrollLeft=e.scrollWidth')
                assert flow.evaluate('(e)=>e.scrollLeft')>0
                flow.evaluate('(e)=>e.scrollLeft=0')
                capture('horizontal-flow.png')
                page.locator('#tab-records').click()
                expect(page.locator('#events .waterfall-entry').first).to_be_visible()
                expect(page.locator('#events .waterfall-axis')).to_contain_text('耗时')
                assert all(__import__('re').fullmatch(r'\d+\.\d{2}s',t) for t in page.locator('#events .waterfall-clock').all_text_contents())
                assert page.locator('#events').evaluate('(e)=>e.clientWidth')>900
                page.locator('#events .waterfall-entry').first.locator(':scope > summary').click()
                expect(page.locator('#events .waterfall-body').first).to_be_visible()
                keys=page.locator('#events .waterfall-entry').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.timelineKey)')
                assert len(keys)==len(set(keys)),keys
                expect(page.locator('#followup-form')).not_to_be_visible()
                capture('task-records.png')

                page.locator('[data-close="detail-dialog"]').click()
                page.locator('#board-home').click()

                page.locator('#new-task').click()
                page.locator('#prompt').fill('验证停止后保留在待开始')
                select_workspace('workspace')
                page.locator('#start-now').uncheck()
                page.locator('#new-form button[type=submit]').click()
                page.locator('#detail-actions').get_by_role('button',name='停止',exact=True).click()
                expect(page.locator('#detail-actions .status')).to_have_text('已停止')
                page.locator('[data-close="detail-dialog"]').click()
                stopped=page.locator('#lane-pending .task').filter(has_text='验证停止后保留在待开始')
                expect(stopped).to_have_count(1)
                expect(stopped.locator('.card-status')).to_have_text('已停止')
                expect(stopped.locator('.card-status')).to_have_css('opacity','1')
                expect(page.locator('.archive')).to_be_hidden()
                capture('stopped-task-pending.png')
                stopped.click()
                page.locator('#detail-actions').get_by_role('button',name='继续任务',exact=True).click()
                wait_for(lambda:not runtime.active)
                expect(page.locator('#detail-actions .status')).to_have_text('已完成',timeout=15000)
                page.locator('[data-close="detail-dialog"]').click()
                page.locator('#new-task').click()
                page.locator('#prompt').fill('[write] 写入一个可观察的结果文件')
                select_workspace('workspace')
                page.locator('#start-now').check()
                page.locator('#new-form button[type=submit]').click()
                expect(page.locator('.approval')).to_be_visible(timeout=15000)
                page.get_by_role('button',name='允许本次执行',exact=True).click()
                wait_for(lambda:(workspace/'result.txt').exists())
                page.locator('#tab-files').click()
                expect(page.locator('#files')).to_contain_text('result.txt')
                expect(page.locator('#files .diff')).to_contain_text('+done')
                capture('files.png')
                page.locator('[data-close="detail-dialog"]').click()

                radii=page.locator('.lane-body').first.evaluate('(element)=>{const s=getComputedStyle(element);return [s.borderTopLeftRadius,s.borderTopRightRadius,s.borderBottomLeftRadius,s.borderBottomRightRadius]}')
                assert len(set(radii))==1 and radii[0]!='0px',radii
                capture('board.png')
                page.locator('#settings-open').click()
                expect(page.locator('#settings-panel-general')).to_be_visible()
                capture('settings-general.png')
                baseline=page.locator('.page-title h1').evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)')
                choose_select('font-scale','1.2')
                enlarged=page.locator('.page-title h1').evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)')
                assert abs(enlarged/baseline-1.2)<0.01,(baseline,enlarged)
                assert page.locator('[data-select-for="font-scale"]').bounding_box()['width']<=190
                assert page.locator('[data-select-for="font-scale"]').locator('..').locator(':scope > div').bounding_box()['width']>200
                capture('settings-large-font.png')
                page.reload(wait_until='load')
                assert page.evaluate('getComputedStyle(document.documentElement).getPropertyValue("--font-scale").trim()')=='1.2'
                page.locator('#settings-open').click()
                choose_select('font-scale','1')
                page.locator('[data-settings-tab="workspaces"]').click()
                expect(page.locator('#workspace-list')).to_contain_text(str(workspace))
                capture('settings-workspaces.png')
                second_workspace=Path(temporary)/'reference-project';second_workspace.mkdir()
                page.locator('#workspace-path').fill(str(second_workspace))
                page.locator('#workspace-form button[type=submit]').click()
                expect(page.locator('#settings-message')).to_contain_text('工作区已添加')
                expect(page.locator('#workspace-list')).to_contain_text(str(second_workspace))
                assert str(second_workspace) in runtime.config.public()['workspaces']
                page.locator('[data-settings-tab="models"]').click()
                expect(page.locator('#pi-status')).to_contain_text('ui-fixture')
                capture('settings-models.png')
                page.locator('[data-settings-tab="usage"]').click()
                expect(page.locator('#settings-usage-grid > div')).to_have_count(8)
                expect(page.locator('#settings-usage-grid')).to_contain_text('费用未知的记录')
                expect(page.locator('#settings-usage-grid')).to_contain_text('缓存读取 Tokens')
                expect(page.locator('#usage')).to_contain_text('Tokens')
                capture('settings-usage.png')
                page.locator('[data-settings-tab="permissions"]').click()
                shell=page.locator('[data-select-for="shell-mode"]')
                shell.click()
                expect(page.locator('#shell-mode-menu')).to_be_in_viewport()
                capture('settings-select-menu.png')
                page.keyboard.press('Escape')
                expect(page.locator('#settings-dialog')).to_be_visible()
                expect(shell).to_be_focused()
                choose_select('shell-mode','approval')
                page.locator('#shell-form button[type=submit]').click()
                expect(page.locator('#settings-message')).to_contain_text('命令设置已保存')
                assert runtime.config.public()['shell_mode']=='approval'
                page.locator('[data-settings-tab="permissions"]').focus()
                page.keyboard.press('ArrowDown')
                expect(page.locator('#settings-panel-agents')).to_be_visible()
                expect(page.locator('#role-list')).to_contain_text('task-worker')
                capture('settings-agents.png')
                page.keyboard.press('Home')
                expect(page.locator('#settings-panel-general')).to_be_visible()
                page.locator('[data-settings-link="permissions"]').click()
                choose_select('shell-mode','disabled')
                page.locator('#shell-form button[type=submit]').click()
                expect(page.locator('#settings-message')).to_contain_text('命令设置已保存')
                assert runtime.config.public()['shell_mode']=='disabled'
                shell.click()
                page.locator('[data-close="settings-dialog"]').click()
                expect(page.locator('#shell-mode-menu')).to_be_hidden()
                page.locator('#workspace-add').click()
                expect(page.locator('#settings-panel-workspaces')).to_be_visible()
                expect(page.locator('#workspace-path')).to_be_focused()
                page.locator('[data-close="settings-dialog"]').click()
                page.locator('#settings-open').click()
                page.locator('[data-settings-tab="agents"]').click()
                page.locator('#settings-manage-agents').click()
                expect(page.locator('#settings-dialog')).to_be_hidden()
                expect(page.locator('#agents-library')).to_contain_text('career-coach')
                expect(page.locator('#agents-library')).to_contain_text('interview-mentor')
                capture('agents.png')
                page.locator('[data-close="agents-dialog"]').click()
                # Real file formats through browser upload and bounded parser subprocess.
                sys.path.insert(0,str(ROOT/'tests'))
                from fixtures.document_samples import docx_bytes,pdf_bytes
                new_chat('chat')
                page.locator('#paper-upload').set_input_files([
                    {'name':'研究.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':docx_bytes()},
                    {'name':'文本.pdf','mimeType':'application/pdf','buffer':pdf_bytes(('Sample size 120',''))},
                    {'name':'资料.md','mimeType':'text/markdown','buffer':'# 背景\n\n这里是原始资料。\n\n## 方法\n\n样本量 120。'.encode()},
                    {'name':'扫描.pdf','mimeType':'application/pdf','buffer':pdf_bytes(('',))},
                ])
                page.locator('[data-chat-view="documents"]').click()
                cards=page.locator('#chat-documents .document-card')
                expect(cards).to_have_count(4)
                word=cards.filter(has_text='研究.docx')
                expect(word).to_contain_text('可阅读',timeout=15000)
                expect(word).to_contain_text('docx-xml-v1')
                expect(cards.filter(has_text='文本.pdf')).to_contain_text('pypdf-',timeout=15000)
                expect(cards.filter(has_text='扫描.pdf')).to_contain_text('未提取到文字',timeout=15000)
                capture('document-pipeline.png')
                word.get_by_role('button',name='查看原文',exact=True).click()
                expect(page.locator('#paper-text')).to_contain_text('实验组')
                expect(page.locator('#paper-section option')).to_contain_text(['研究方法'])
                section=page.locator('[data-select-for="paper-section"]')
                section.click()
                expect(page.locator('#paper-section-menu')).to_be_in_viewport()
                capture('reader-section-menu.png')
                page.locator('#paper-section-menu [role="option"]').first.click()
                expect(section).to_be_focused()
                page.locator('#paper-search-query').fill('120')
                page.locator('#paper-search-form button').click()
                expect(page.locator('#paper-search-results')).to_contain_text('样本量')
                page.locator('#paper-search-results button').first.click()
                expect(page.locator('#paper-text')).to_contain_text('120')
                capture('document-reader.png')
                page.locator('[data-close="paper-dialog"]').click()
                cards.filter(has_text='文本.pdf').get_by_role('button',name='查看原文',exact=True).click()
                expect(page.locator('#paper-text')).to_contain_text('Sample size 120')
                page.locator('[data-close="paper-dialog"]').click()
                page.locator('[data-chat-view="conversation"]').click()
                send('先讨论可读资料，不要假设扫描件已经识别')
                expect(page.locator('.chat-assistant').last).to_contain_text('普通回答')
                new_chat('chat')
                page.locator('#conversation-list .conversation-item').first.hover()
                page.locator('#conversation-list .conversation-more').first.click()
                before=len(runtime.conversations.list())
                page.locator('#conversation-delete').click()
                page.locator('#conversation-delete').click()
                expect(page.locator('#chat-view')).to_be_hidden()
                wait_for(lambda:len(runtime.conversations.list())==before-1)
                # External software uses the actual HTTP contract; no Pi execution involved.
                imported=page.request.post(f'http://127.0.0.1:{server.server_port}/api/external/tasks',data={'source':'外部测试工具','external_id':'browser-001','prompt':'外部工具读取结果回传验证'}).json()
                run_id=imported['id']
                route=f'http://127.0.0.1:{server.server_port}/api/external/tasks/{run_id}/reports'
                response=page.request.post(route,data={'sequence':1,'status':'running','step':'正在核查外部结果','tool':'test-reader','result':'已读取外部材料'})
                assert response.ok,response.text()
                card=page.locator('#lane-running .task').filter(has_text='外部工具读取结果回传验证')
                expect(card).to_be_visible(timeout=15000)
                expect(card).to_contain_text('正在核查外部结果')
                card.click()
                expect(page.locator('#detail-actions')).to_contain_text('外部执行：外部测试工具')
                expect(page.locator('#flow-rows')).to_contain_text('正在核查外部结果')
                response=page.request.post(route,data={'sequence':2,'status':'completed','step':'外部工具交付完成','result':'检查通过，外部执行结果已经回到任务中心'})
                assert response.ok,response.text()
                expect(page.locator('#detail-actions .status')).to_have_text('已完成',timeout=15000)
                page.locator('#tab-records').click()
                expect(page.locator('#messages')).to_contain_text('检查通过，外部执行结果已经回到任务中心')
                expect(page.locator('#detail-error')).to_be_hidden()
                expect(page.locator('#events .waterfall-entry > summary').filter(has_text='正在核查外部结果').first).to_be_visible()
                capture('external-task-results.png')
                page.locator('[data-close="detail-dialog"]').click()
                expect(page.locator('#lane-done .task').filter(has_text='外部工具读取结果回传验证')).to_be_visible()

                # Switching views must invalidate delayed loads, even when returning to the same ID.
                host=f'http://127.0.0.1:{server.server_port}'
                a=page.request.post(host+'/api/conversations',data={'mode':'chat'}).json()
                b=page.request.post(host+'/api/conversations',data={'mode':'chat'}).json()
                page.request.post(host+f"/api/conversations/{a['id']}/rename",data={'title':'延迟会话 A'})
                page.request.post(host+f"/api/conversations/{b['id']}/rename",data={'title':'当前会话 B'})
                snapshot=page.request.get(host+f"/api/conversations/{a['id']}").json()
                page.reload(wait_until='load')
                pending=[]
                page.route(host+f"/api/conversations/{a['id']}",lambda route:pending.append(route))
                page.locator(f"[data-sidebar-key='{a['id']}'] .conversation-open").click()
                expect(page.locator(f"[data-sidebar-key='{b['id']}'] .conversation-open")).to_be_visible()
                page.locator(f"[data-sidebar-key='{b['id']}'] .conversation-open").click()
                expect(page.locator('#chat-title')).to_have_text('当前会话 B')
                assert pending
                pending.pop().fulfill(json=snapshot)
                page.wait_for_timeout(200)
                expect(page.locator('#chat-title')).to_have_text('当前会话 B')
                page.locator('#board-home').click()
                expect(page.locator('#chat-view')).to_be_hidden()
                assert page.evaluate("window.__ownedStreams.filter(s=>s.url.includes('/api/conversations/')).every(s=>s.closed)")
                page.locator(f"[data-sidebar-key='{a['id']}'] .conversation-open").click()
                page.locator('#board-home').click()
                assert pending
                pending.pop().fulfill(json=snapshot)
                page.wait_for_timeout(200)
                expect(page.locator('#chat-view')).to_be_hidden()
                page.unroute(host+f"/api/conversations/{a['id']}")
                if page.locator('#board .task').count():
                    page.locator('#board .task').first.click()
                    expect(page.locator('#detail-dialog')).to_be_visible()
                    page.locator('[data-close="detail-dialog"]').click()
                    assert page.evaluate("window.__ownedStreams.filter(s=>s.url.includes('/api/runs/')).every(s=>s.closed)")
                # Task memory: real HTTP/UI edits, stale revisions, source projection and navigation.
                memory_task=runtime.create('上下文管理验收：整理本地资料',str(workspace))
                initial_assembly=runtime.task_context.assemble(memory_task, 'main', 'ui-observe')
                runtime.emit(memory_task,'usage',{'model':'本地计价样本','provider':'fixture','agent_id':'main','attempt_id':'ui-observe','assembly_id':initial_assembly['assembly_id'],'prompt_tokens':100,'completion_tokens':20,'total_tokens':150,'cache_read_tokens':30,'cache_write_tokens':0,'cost_usd':0.0012,'cost_source':'pi_model_rates'})
                runtime.emit(memory_task,'usage',{'model':'未计价样本','agent_id':'child-observe','attempt_id':'ui-child','prompt_tokens':10,'completion_tokens':2,'total_tokens':12})
                runtime.task_context.offload(memory_task,'main',{'text':'原始工具资料\n'*1500,'name':'read','tool_call_id':'ui-large-read'})
                page.reload(wait_until='load')
                memory_card=page.locator('#board .task').filter(has_text='上下文管理验收：整理本地资料')
                expect(memory_card).to_be_visible();memory_card.click()
                page.locator('#tab-records').click()
                page.locator('#events select[aria-label="执行轮次"]').select_option('all')
                expect(page.locator('#events .task-usage')).to_contain_text('2 条模型用量记录')
                expect(page.locator('#events .task-usage')).to_contain_text('US$0.001200')
                expect(page.locator('#events .task-usage')).to_contain_text('1 条费用未知')
                usage_entry=page.locator('#events .waterfall-entry').filter(has=page.locator('summary',has_text='模型用量与费用')).first
                usage_entry.locator(':scope > summary').click()
                expect(usage_entry).to_contain_text(initial_assembly['assembly_id'])
                capture('task-usage-cost.png')
                assembly_entry=page.locator('#events .waterfall-entry').filter(has=page.locator('summary',has_text='调用前组装记忆')).first
                assembly_entry.locator(':scope > summary').click()
                assembly_entry.get_by_role('button',name='查看注入快照').click()
                expect(page.locator('#tab-context')).to_have_attribute('aria-selected','true')
                expect(page.locator(f'.assembly-history details[data-assembly-id="{initial_assembly["assembly_id"]}"]')).to_have_attribute('open','')
                page.locator('#tab-context').click()
                expect(page.locator('#task-context-content')).to_contain_text('任务目标')
                expect(page.locator('.assembly-history details')).to_have_count(1)
                draft=page.locator('[data-memory-draft]');draft.fill('禁止联网，交付使用中文')
                page.get_by_role('button',name='保存约束',exact=True).click()
                expect(page.locator('.memory-item')).to_contain_text('禁止联网，交付使用中文')
                expect(page.locator('.memory-item')).to_contain_text('来源：用户')
                state=runtime.task_context.overview(memory_task)
                runtime.task_context.tool(memory_task,{'action':'save','revision':state['revision'],'kind':'progress','text':'已读取附件目录'})
                runtime.task_context.assemble(memory_task)
                draft.fill('尚未提交的要求草稿')
                expect(page.locator('.memory-item').filter(has_text='已读取附件目录')).to_be_visible()
                expect(page.locator('[data-memory-draft]')).to_have_value('尚未提交的要求草稿')
                expect(page.locator('[data-memory-draft]')).to_be_focused()
                expect(page.locator('#task-context-content')).to_contain_text('Agent 工作笔记')
                expect(page.locator('.assembly-history details')).to_have_count(2)
                history=page.locator('.assembly-history details').first
                history.locator('summary').click()
                expect(history).to_contain_text('已读取附件目录')
                history.scroll_into_view_if_needed()
                capture('memory-injection-history.png')
                # Compare a stale version through the real API; the existing user constraint survives.
                response=page.request.post(host+f'/api/runs/{memory_task}/context',data={'revision':1,'kind':'constraint','text':'过时覆盖'})
                assert response.status==400,response.text()
                user_item=page.locator('.memory-item').filter(has_text='禁止联网，交付使用中文')
                user_item.get_by_role('button',name='编辑',exact=True).click()
                page.locator('[data-memory-draft]').fill('只使用本地 PDF，交付使用中文')
                page.get_by_role('button',name='保存修改',exact=True).click()
                expect(page.locator('.memory-item').filter(has_text='只使用本地 PDF')).to_be_visible()
                expect(page.locator('.assembly-history details').first).to_have_attribute('open','')
                page.locator('#detail-dialog .dialog-scroll').evaluate('(e)=>e.scrollTop=0')
                capture('task-context-overview.png')
                expect(page.locator('#task-context-content')).to_contain_text('当前记忆已更新')
                page.locator('#task-context-content summary').filter(has_text='查看本次组装内容与来源').click()
                expect(page.locator('.memory-projection').first).to_contain_text('user_memory')
                source_entry=page.locator('.task-memory-side details').filter(has=page.locator('summary',has_text='read ·'))
                source_entry.locator('summary').click()
                source_entry.get_by_role('button',name='读取原文',exact=True).click()
                expect(source_entry.locator('pre')).to_contain_text('原始工具资料')
                source_entry.get_by_role('button',name='继续读取',exact=True).click()
                for width in [1280,1440,1920]:
                    review_size(width,1000)
                    assert page.locator('#pane-context').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    capture(f'task-context-{width}.png')
                page.locator('.memory-item').filter(has_text='只使用本地 PDF').get_by_role('button',name='移除',exact=True).click()
                expect(page.locator('.memory-item').filter(has_text='只使用本地 PDF')).to_have_count(0)
                page.locator('[data-close="detail-dialog"]').click()
                capture('controller-navigation.png')
                # Synthetic evidence: Agent completed, followed by a failed plan check.
                failure_task=runtime.create('轨迹验收：执行结束后的计划校验',str(workspace))
                reason='计划仍有未完成步骤，请继续处理'
                base=time.time()-120
                fixture=[
                    (0,'agent.started',{'role':'主 Agent','task':'历史轮次验收','attempt_id':'old','status':'running'}),
                    (1,'tool.started',{'attempt_id':'old','id':'same','name':'旧轮次读取'}),
                    (4,'tool.completed',{'attempt_id':'old','id':'same','name':'旧轮次读取'}),
                    (5,'agent.completed',{'attempt_id':'old','status':'completed'}),
                    (20,'agent.started',{'role':'主 Agent','task':'当前轮次验收','attempt_id':'current','status':'running'}),
                    (21,'pi.turn_start',{}),(22,'message.started',{}),
                    (39,'message.completed',{'text':'已完成本轮分析，准备写入报告。'}),
                    (39.1,'usage',{'model':'本地验收模型','total_tokens':120,'prompt_tokens':100,'completion_tokens':20,'cost_usd':.0012}),
                    (40,'tool.started',{'id':'same','name':'read','arguments':{'path':'notes.md'}}),
                    (41,'tool.started',{'id':'write','name':'write','arguments':{'path':'report.md'}}),
                    (43,'tool.completed',{'id':'same','name':'read','result':{'content':[{'type':'text','text':'已读取资料'}]}}),
                    (44,'approval.requested',{'id':'approval','name':'write'}),
                    (58,'approval.resolved',{'id':'approval','decision':'approved'}),
                    (60,'tool.completed',{'id':'write','name':'write','result':{'content':[{'type':'text','text':'报告已保存'}]}}),
                    (61,'pi.turn_start',{}),(62,'message.started',{}),
                    (80,'message.completed',{'text':'本轮报告已交付。'}),
                    (80.1,'usage',{'model':'本地验收模型','total_tokens':50,'prompt_tokens':40,'completion_tokens':10}),
                    (81,'task.reported',{'summary':'报告内容已生成；计划步骤仍待核对。'}),
                    (82,'agent.completed',{'status':'completed'}),
                    (82.1,'error',{'text':reason}),(82.2,'status',{'status':'failed','reason':reason})]
                for offset,kind,data in fixture:
                    runtime.emit(failure_task,kind,{'agent_id':'main','attempt_id':data.get('attempt_id','current'),**data})
                    with store.lock,store.db:
                        store.db.execute('UPDATE events SET created=? WHERE id=(SELECT MAX(id) FROM events WHERE run_id=?)',(base+offset,failure_task))
                page.reload(wait_until='load')
                page.locator('#board .task').filter(has_text='轨迹验收：执行结束后的计划校验').click()
                expect(page.locator('#tab-records')).to_have_attribute('aria-selected','true')
                expect(page.locator('#task-outcome')).to_contain_text('收尾校验失败')
                expect(page.locator('#task-outcome')).to_contain_text(reason)
                expect(page.locator('#events .waterfall-entry > summary').filter(has_text='旧轮次读取')).to_have_count(0)
                page.locator('#events select[aria-label="执行轮次"]').select_option('all')
                expect(page.locator('#events .waterfall-entry > summary').filter(has_text='旧轮次读取')).to_be_visible()
                page.locator('#tab-flow').click()
                page.get_by_role('button',name='定位失败步骤',exact=True).click()
                failure=page.locator('#events .waterfall-entry[data-status="failed"]').last
                expect(failure).to_have_attribute('open','')
                expect(failure).to_contain_text(reason)
                expect(page.locator('#events select[aria-label="执行轮次"]')).to_have_value('latest')
                for width in [1280,1440,1920]:
                    review_size(width,1000)
                    assert page.locator('#pane-records').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    page.locator('#detail-dialog .dialog-scroll').evaluate('(e)=>e.scrollTop=0')
                    capture(f'waterfall-failure-{width}.png')
                # Opening a step survives another render, and keyboard tabs still work.
                tool=page.locator('#events .waterfall-entry[data-kind="tool"]').first
                tool.locator(':scope > summary').click()
                page.locator('#tab-records').click()
                expect(tool).to_have_attribute('open','')
                page.locator('#tab-records').focus();page.keyboard.press('ArrowRight')
                expect(page.locator('#tab-files')).to_have_attribute('aria-selected','true')
                expect(page.locator('#task-delivery')).to_contain_text('报告内容已生成')
                page.locator('#tab-flow').click()
                capture('task-overview.png')
                page.locator('[data-close="detail-dialog"]').click()
                # Numeric-only chat metadata with a normal turn and a failed retry.
                trace_chat=runtime.conversations.create('chat',str(workspace))['id']
                runtime.conversations.message(trace_chat,'user','对话时间轴验收（构造数据）')
                runtime.conversations.message(trace_chat,'assistant','本轮过程仅用于界面验收。')
                base=time.time()-86490
                samples=[(0,'request.started',{}),(1,'model.started',{}),(2,'message.started',{}),(3,'memory.assembled',{}),
                    (15,'model.completed',{'provider':'fixture','model':'本地验收模型','prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'cache_read_tokens':0,'cache_write_tokens':0,'cost_usd':.0012}),
                    (16,'tool.started',{'id':'read','name':'workbench_document','arguments':{'action':'read','path':'example.md'}}),
                    (20,'tool.completed',{'id':'read','name':'workbench_document','is_error':False}),
                    (21,'model.started',{}),(22,'message.started',{}),
                    (40,'model.completed',{'provider':'fixture','model':'本地验收模型','prompt_tokens':40,'completion_tokens':10,'total_tokens':50,'cost_usd':None}),
                    (41,'request.completed',{'status':'completed'}),
                    (86450,'request.started',{}),(86451,'model.started',{}),(86452,'message.started',{}),
                    (86456,'model.completed',{'provider':'fixture','model':'失败验收模型','total_tokens':0,'cost_usd':None,'stop_reason':'error'}),
                    (86457,'request.completed',{'status':'failed'})]
                for offset,kind,data in samples:
                    runtime.conversations.execution(trace_chat,kind,data)
                    with store.lock,store.db:store.db.execute('UPDATE conversation_events SET created=? WHERE id=(SELECT MAX(id) FROM conversation_events WHERE conversation_id=?)',(base+offset,trace_chat))
                runtime.conversations.status(trace_chat,'failed')
                page.reload(wait_until='load')
                page.locator(f"[data-sidebar-key='{trace_chat}'] .conversation-open").click()
                page.locator('[data-chat-view="trace"]').click()
                expect(page.locator('#trace-entries .trace-entry[data-kind="model"]')).to_have_count(1)
                choose_select('trace-turn','all')
                expect(page.locator('#trace-entries .trace-entry[data-kind="model"]')).to_have_count(3)
                expect(page.locator('#trace-summary')).to_contain_text('US$0.001200')
                expect(page.locator('#trace-entries .trace-run-heading')).to_have_count(2)
                expect(page.locator('#trace-entries .waterfall-axis')).to_have_count(2)
                expect(page.locator('#trace-entries .trace-run-heading').first).to_contain_text('41.00s')
                expect(page.locator('#trace-entries .trace-run-heading').last).to_contain_text('7.00s')
                before=page.locator('#trace-entries .trace-run-heading').all_text_contents()
                page.evaluate('()=>{const later=Date.now()+86400000;Date.now=()=>later;}')
                page.locator('[data-chat-view="trace"]').click()
                assert before==page.locator('#trace-entries .trace-run-heading').all_text_contents()
                step=page.locator('#trace-entries .trace-entry[data-kind="model"]').first
                step.locator(':scope > summary').click()
                expect(step).to_contain_text('记忆组装完成至模型回复结束')
                prep=page.locator('#trace-entries .trace-entry').filter(has=page.locator('summary',has_text='调用前准备（含记忆组装）'))
                expect(prep.locator('.waterfall-duration')).to_have_text('2.00s')
                expect(step.locator('.waterfall-clock')).to_have_text('3.00s')
                expect(step.locator('.waterfall-duration')).to_have_text('12.00s')
                kinds=page.locator('#trace-entries .trace-entry').evaluate_all('(nodes)=>nodes.map(n=>n.dataset.kind)')
                assert kinds.index('memory') < kinds.index('model')
                assert all(__import__('re').fullmatch(r'\d+\.\d{2}s',t) for t in page.locator('#trace-entries .waterfall-clock').all_text_contents())
                choose_select('trace-kind','models')
                expect(page.locator('#trace-entries .trace-entry')).to_have_count(3)
                choose_select('trace-kind','failed')
                expect(page.locator('#trace-entries .trace-entry')).to_have_count(2)
                choose_select('trace-kind','all')
                expect(step).to_have_attribute('open','')
                capture('chat-waterfall-step.png')
                step.locator(':scope > summary').click()
                for width in [1280,1440,1920]:
                    review_size(width,1000)
                    assert page.locator('#chat-trace').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                    page.locator('#trace-entries').evaluate('(e)=>e.scrollTop=0')
                    capture(f'chat-waterfall-{width}.png')
                # Chat trace is also readable at narrow widths, independently of mobile board checks.
                page.set_viewport_size({'width':390,'height':1000})
                assert page.locator('#chat-trace').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                expect(page.locator('#chat-trace-close')).to_be_in_viewport()
                page.screenshot(path=str(args.output/'chat-waterfall-390.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1000})
                page.locator('[data-chat-view="conversation"]').click()
                expect(page.locator('.chat-assistant')).to_contain_text('本轮过程仅用于界面验收')
                assert not errors,errors
                browser.close()
            print('PASS: 双模式、附件、编排、审批与文件差异、轨迹、长对话滚动恢复、草稿、焦点及脚本；布局模式：'+('桌面 1280/1440/1920px' if args.desktop_only else '桌面与小屏'))
            print('截图目录：'+str(args.output))
        finally:
            runtime.shutdown();server.shutdown();server.server_close();serving.join(timeout=2)
            store.db.close()


if __name__=='__main__':main()
