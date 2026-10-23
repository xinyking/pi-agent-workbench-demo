"""Pi 1.3 RPC transport. All commands and events are strict LF-framed JSON."""
import json
import os
import queue
import selectors
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path


class PiCancelled(Exception):
    pass


class PiClient:
    def __init__(self, binary, workspace, session_id, session_dir, model='', role=None, shell_mode='disabled', data_dir=None, extra_extensions=(), agent_catalog=(), mode='task'):
        bridge = Path(__file__).parent / 'pi/bridge.ts'
        args = [binary, '--mode', 'rpc', '--offline', '--no-extensions', '--no-mcp', '--no-approve',
                '--extension', str(bridge), '--session-id', str(uuid.UUID(session_id)), '--session-dir', str(session_dir)]
        args.extend(['--extension', str(bridge.parent / 'providers.ts')])
        args.extend(['--extension', str(bridge.parent / 'context-manager.ts')])
        if mode != 'chat':
            args.extend(['--extension', str(bridge.parent / 'task-memory.ts')])
        if mode != 'chat':
            args.extend(['--extension', str(bridge.parent / 'vendor/structured-output.ts')])
        for extension in extra_extensions:
            args.extend(['--extension', str(extension)])
        tools = 'read,grep,find,ls,write,edit,bash,workbench_delegate,workbench_ask_user,workbench_finish,workbench_create_agent,workbench_context,workbench_memory,structured_output,workbench_plan,workbench_document'
        if role:
            text = Path(role).read_text()
            frontmatter = text.split('---', 2)[1]
            tools = next(line.split(':', 1)[1].strip().replace(' ', '') for line in frontmatter.splitlines() if line.startswith('tools:'))
            tools += ',workbench_ask_user,workbench_context,workbench_memory,structured_output,workbench_document'
            args.extend(['--append-system-prompt', str(role)])
        else:
            args.extend(['--append-system-prompt', '你是任务工作台的主 Agent。使用中文。复杂任务先用 workbench_plan 提交含依赖、角色与交付要求的步骤，再通过 workbench_delegate（每项带 step_id） 委派调查、实现、审查和验证，独立任务可并行，有依赖的阶段分次调用。所有工具执行和子任务结果会记录在看板中。先阅读项目约定，完成后提供改动、验证及剩余问题；缺少用户资料时必须调用 workbench_ask_user 挂起提问，不要仅在回复中索取资料，也不要猜测。所有目标完成后调用 workbench_finish 提交交付摘要再总结结果；如果用户需要结构化报告，也可使用 structured_output 一次提交标题、总结和行动项作为最终交付，该工具会结束本轮，不再另行调用 workbench_finish。不要把失败的子任务写成成功。结束本轮前必须自检是否已经调用 workbench_finish 或 structured_output；仅在文字中说“已完成”不会登记任务完成。'])
        if not role and agent_catalog:
            args[-1] += '\n你服务于通用任务规划，不限于编程。可用子 Agent：' + json.dumps([{k:a[k] for k in ('name','description')} for a in agent_catalog], ensure_ascii=False)
        if mode == 'chat':
            tools = 'workbench_create_task,workbench_create_agent,workbench_document'
            prompt_index = args.index('--append-system-prompt')
            args[prompt_index] = '--system-prompt'
            args[prompt_index + 1] = '你是通用中文问答助手。根据会话历史回答问题，不自动创建任务、不执行文件操作。只有用户明确要求基于对话创建任务时调用 workbench_create_task，任务指令应包含目标、约束、期望交付，不要重复整段历史，系统会自动保存并转交上下文。任务需要已选择的工作区，若缺少先询问用户选择。只有用户要求新增专家或子 Agent 时调用 workbench_create_agent。附件通过 workbench_document 分页读取，不得只凭文件名概括论文。关键事实附工具返回的 paper: 页码引用，区分作者结论和你的推断。不能声称已经完成后台任务，返回任务链接后用户可在任务中心观察。'
        args.extend(['--tools', tools])
        if (not model or model=='default') and data_dir:
            settings=Path(data_dir)/'pi-api.json'
            if settings.exists():model=json.loads(settings.read_text()).get('default_model','')
        if model and model != 'default':
            args.extend(['--model', model])
        env = {**os.environ, 'WORKBENCH_CHILD': '1' if role else '0', 'WORKBENCH_SHELL_MODE': shell_mode,
               'WORKBENCH_DATA_DIR': str(data_dir or ''), 'WORKBENCH_MODE': mode, 'WORKBENCH_AGENTS': json.dumps([a['name'] for a in agent_catalog]), 'PI_OFFLINE': '1', 'PI_TELEMETRY': '0'}
        Path(session_dir).mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(args, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True)
        self.records = queue.Queue(maxsize=2048)
        self.closed = threading.Event()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _put(self, record):
        while not self.closed.is_set():
            try:
                self.records.put(record, timeout=.1)
                return
            except queue.Full:
                pass

    def _read(self):
        selector = selectors.DefaultSelector()
        for stream in (self.process.stdout, self.process.stderr):
            selector.register(stream, selectors.EVENT_READ)
        buffers = {self.process.stdout: b'', self.process.stderr: b''}
        try:
            while selector.get_map() and not self.closed.is_set():
                for key, _ in selector.select(.1):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        if buffers[key.fileobj] and key.fileobj is self.process.stderr:
                            self._put({'type': 'stderr', 'text': buffers[key.fileobj].decode(errors='replace')[:8000]})
                        continue
                    buffers[key.fileobj] += data
                    if len(buffers[key.fileobj]) > 8 * 1024 * 1024:
                        raise ValueError('Pi 单条输出超过 8 MiB')
                    while b'\n' in buffers[key.fileobj]:
                        line, buffers[key.fileobj] = buffers[key.fileobj].split(b'\n', 1)
                        if not line.strip():
                            continue
                        if key.fileobj is self.process.stderr:
                            self._put({'type': 'stderr', 'text': line.decode(errors='replace')[:8000]})
                        else:
                            record = json.loads(line)
                            if not isinstance(record, dict):
                                raise ValueError('Pi RPC 返回了非对象记录')
                            self._put(record)
        except Exception as exc:
            self._put({'type': 'transport_error', 'error': str(exc)})
        finally:
            selector.close()
            self._put({'type': 'process_exit'})

    def send(self, record):
        self.process.stdin.write(json.dumps(record, ensure_ascii=False).encode() + b'\n')
        self.process.stdin.flush()

    def next(self, stop, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop.is_set():
                raise PiCancelled()
            try:
                event = self.records.get(timeout=.1)
            except queue.Empty:
                continue
            if event['type'] in ('process_exit', 'transport_error'):
                raise ValueError(event.get('error', 'Pi 进程已退出；请检查 Pi 配置和运行轨迹中的诊断'))
            return event
        raise ValueError('等待 Pi 响应超时')

    def request(self, command, stop, on_event=None, timeout=60, **data):
        request_id = uuid.uuid4().hex
        self.send({'id': request_id, 'type': command, **data})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event = self.next(stop, max(.1, deadline - time.monotonic()))
            if event.get('type') == 'response' and event.get('id') == request_id:
                if not event.get('success'):
                    raise ValueError(event.get('error', 'Pi 拒绝请求'))
                return event.get('data', {})
            if on_event:
                on_event(event)
        raise ValueError('Pi 请求超时：' + command)

    def compact(self, stop, on_event=None):
        return self.request('compact', stop, on_event, timeout=180,
            customInstructions='使用中文。保留用户最新目标、硬约束、已确认事实、决定、资料来源、context_id、当前计划与任务状态、未解决问题、文件路径和下一步。区分完成与计划，不能把推断当成用户要求，保留冲突与不确定性。')

    def close(self):
        if self.closed.is_set():
            return
        try:
            self.send({'type': 'abort'})
        except (OSError, ValueError):
            pass
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait()
        self.closed.set()
        self.reader.join(timeout=1)
        self.process.stdout.close()
        self.process.stderr.close()
