"""Task coordinator for Pi RPC and explicit external CLI executors."""
import concurrent.futures
import difflib
import json
import threading
import time
import uuid
from pathlib import Path
from .pi_client import PiClient, PiCancelled
from .pi_config import PiConfig
from .agent_registry import AgentRegistry
from .conversations import Conversations
from .planning import validate_plan, prepare_batch
from .documents import Documents
from .task_context import TaskContext
from .memory_service import MemoryService
from .executors import Executors
from .model_settings import ModelSettings
from .source_packets import SourcePackets
from .store import TERMINAL
from .archives import create_archive,task_events

BUCKETS = {'queued': 'pending', 'running': 'running', 'stopping': 'running',
           'waiting_approval': 'attention', 'failed': 'attention', 'interrupted': 'attention',
           'waiting_input': 'attention', 'awaiting_confirmation': 'attention', 'completed': 'done', 'cancelled': 'cancelled'}


class Runtime:
    def __init__(self, store):
        self.store, self.config = store, PiConfig(store)
        self.lock = threading.RLock()
        self.active, self.waiters, self.input_waiters = {}, {}, {}
        self.mutation_locks = {}
        self.client_factory = PiClient
        self.mode = 'pi'
        self.closed = False
        self._documents = Documents(store)
        self._conversations = Conversations(self)
        self._memory = MemoryService(self)
        self._task_context = TaskContext(self)
        self._executors = Executors(self)
        self._sources = SourcePackets(self)

    @property
    def conversations(self):
        if self._conversations.store is not self.store: self._conversations = Conversations(self)
        return self._conversations

    @property
    def documents(self):
        if self._documents.store is not self.store:self._documents=Documents(self.store)
        return self._documents

    @property
    def memory(self):
        if self._memory.store is not self.store: self._memory = MemoryService(self)
        return self._memory

    @property
    def task_context(self):
        if self._task_context.store is not self.store: self._task_context = TaskContext(self)
        return self._task_context

    @property
    def sources(self):
        if self._sources.store is not self.store:self._sources=SourcePackets(self)
        return self._sources

    @property
    def models(self):
        return ModelSettings(self)

    @property
    def executors(self):
        if self._executors.store is not self.store:self._executors=Executors(self)
        return self._executors

    @property
    def agents(self):
        return AgentRegistry(self.store)

    def emit(self, run_id, kind, data):
        def clean(value):
            if isinstance(value, str): return self.config.redact(value)
            if isinstance(value, dict): return {key: clean(item) for key, item in value.items()}
            if isinstance(value, list): return [clean(item) for item in value]
            return value
        cleaned = clean(data)
        self.store.emit(run_id, kind, cleaned)
        self.conversations.on_task_event(run_id, kind, cleaned)

    def create(self, prompt, workspace, provider_id=None, model='', context_goal=None, context_user_goal=True, executor_options=None, source_selection=None, preview_digest=None):
        if provider_id in ('codex','claude'):
            return self.executors.create(prompt,workspace,provider_id,model,executor_options or {},source_selection,preview_digest)
        if provider_id not in (None,'','pi'):raise ValueError('不支持的执行器')
        self.config.workspace(workspace)
        if not isinstance(model, str) or len(model) > 200 or model.startswith('-'):
            raise ValueError('无效 Pi 模型标识')
        model=model.strip() or self.models.default()
        self.models.validate_model(model)
        source_data=self.sources.checked(prompt,source_selection,'pi',model,preview_digest) if source_selection is not None else None
        if source_data:context_goal=prompt+('\n硬约束：'+source_data['packet']['constraints'] if source_data['packet']['constraints'] else '')
        run_id = self.store.create(prompt, workspace, 'pi', model or 'default')
        if source_data:self.sources.bind(run_id,source_data)
        self.store.message(run_id, {'role': 'user', 'content': source_data['text'] if source_data else prompt})
        self.task_context.initialize(run_id, context_goal if context_goal is not None else prompt)
        if context_user_goal:
            self.memory.observe_user(run_id, context_goal if context_goal is not None else prompt, position=0, source_token='goal')
        self.emit(run_id, 'message.user', {'text': prompt, 'agent_id': 'main'})
        return run_id

    def start(self, run_id, followup=None):
        if self.store.run(run_id)['provider_id'] in ('codex','claude'):return self.executors.start(run_id,followup)
        with self.lock:
            if self.closed:raise ValueError('工作台正在关闭')
            run = self.store.run(run_id)
            if run['provider_id'] != 'pi':
                raise ValueError('旧版任务保留为只读历史。请新建 Pi 任务以使用新的执行流程')
            if run_id in self.active: raise ValueError('此任务正在执行')
            if len(self.active) >= 4: raise ValueError('最多同时运行 4 个根任务')
            if any(r['status'] == 'pending' for r in self.store.inputs(run_id)): raise ValueError('请先回答任务中的补充信息请求')
            if run['status'] not in TERMINAL | {'queued', 'awaiting_confirmation', 'waiting_input'}: raise ValueError('当前任务不能启动')
            if run['status'] in {'completed', 'cancelled'} and not followup: raise ValueError('已结束任务需要追加要求后继续')
            self.config.require_pi()
            workspace = self.config.workspace(run['workspace'])
            prompt = followup or (run['prompt'] if run['status'] == 'queued' else '继续之前的任务。先检查已有改动和执行结果，避免重复操作。')
            if not followup and (run.get('reason') or '').startswith('待核对并收尾：'):
                prompt='上轮已有输出，但未提交工作台完成标记。本次请只核对原任务的交付是否完整，复用已有文件和结果，不要重新执行已完成的工作，也不要把“继续”理解为新方向。核对全部目标完成后调用 workbench_finish 提交摘要，或用 structured_output 交付；确有未完成事项则继续处理，缺少必要用户资料时调用 workbench_ask_user。'
            if run['status']=='queued' and not followup:
                history=self.store.messages(run_id)
                prompt=history[0].get('content',prompt) if history else prompt
            if run['status']=='queued' and followup:
                history=self.store.messages(run_id)
                original=history[0].get('content',run['prompt']) if history else run['prompt']
                prompt=original+'\n\n追加要求：'+followup
            if followup or run['status'] != 'queued':
                self.store.message(run_id, {'role': 'user', 'content': prompt})
                self.emit(run_id, 'message.user', {'text': prompt, 'agent_id': 'main'})
            if followup:
                self.task_context.request(run_id, followup)
            stop = threading.Event()
            self.active[run_id] = stop
            self.emit(run_id, 'status', {'status': 'running'})
            threading.Thread(target=self._run, args=(run, workspace, prompt, stop), daemon=True).start()
        return run_id

    def cancel(self, run_id):
        with self.lock:
            run = self.store.run(run_id)
            if run_id in self.active:
                self.active[run_id].set()
                self.emit(run_id, 'status', {'status': 'stopping'})
                return True
            if run['status'] in ('queued', 'waiting_input'):
                self.store.cancel_inputs(run_id)
                self.emit(run_id, 'status', {'status': 'cancelled'})
                return True
            return False

    def confirm(self, run_id):
        with self.lock:
            if self.store.run(run_id)['status'] != 'awaiting_confirmation': raise ValueError('只有待确认任务可以验收')
            self.emit(run_id, 'task.confirmed', {'text': '用户已确认任务结果'})
            self.emit(run_id, 'status', {'status': 'completed'})

    def archive_completed(self,run_id):
        with self.lock:
            run=self.store.run(run_id)
            retry=run['status']=='interrupted' and (run.get('reason') or '').startswith('交付已产生，但保存工作区总结失败：')
            if run['status']!='completed' and not retry:raise ValueError('任务尚未完成，暂不能生成完成总结')
            if run_id in self.active:raise ValueError('当前任务仍在执行')
            if self.closed:raise ValueError('工作台正在关闭')
            workspace=self.config.workspace(run['workspace'])
            reports=[e['data']['summary'] for e in task_events(self.store,run_id) if e['kind'] in ('task.completed','task.reported') and e['data'].get('summary')]
            summary=reports[-1] if reports else run.get('summary')
            if not summary:raise ValueError('任务没有已保存的交付摘要')
            stop=threading.Event();self.active[run_id]=stop
            archive_lock=self.mutation_locks.setdefault(str(workspace),threading.Lock())
        try:
            with archive_lock:archive=create_archive(self.store,run,workspace,summary,self.config.directory,stop)
            with self.lock:
                if stop.is_set():raise PiCancelled()
                self.emit(run_id,'task.archived',archive)
                if retry:
                    self.emit(run_id,'task.completed',{'summary':summary})
                    self.emit(run_id,'status',{'status':'completed'})
            return archive
        except PiCancelled:
            self.emit(run_id,'status',{'status':run['status'],'reason':run.get('reason') or ''})
            raise ValueError('保存归档已停止')
        finally:
            with self.lock:self.active.pop(run_id,None)

    def decide(self, run_id, approval_id, allowed):
        with self.lock:
            waiter = self.waiters.get(approval_id)
            if not waiter or waiter['run_id'] != run_id or waiter['decision'] is not None: raise ValueError('审批已处理或失效')
            if self.active[run_id].is_set(): raise ValueError('任务正在停止')
            self.store.resolve_approval(approval_id, 'approved' if allowed else 'denied')
            waiter['decision'] = allowed
            waiter['event'].set()

    def _refresh_attention(self, run_id):
        pending = [r for r in self.store.inputs(run_id) if r['status'] == 'pending']
        if pending:
            self.emit(run_id, 'status', {'status': 'waiting_input', 'reason': pending[0]['data']['question']})
        elif any(w['run_id'] == run_id for w in self.waiters.values()):
            self.emit(run_id, 'status', {'status': 'waiting_approval', 'reason': '等待工具审批'})
        elif run_id in self.active and not self.active[run_id].is_set():
            self.emit(run_id, 'status', {'status': 'running'})

    def _ask(self, run_id, agent_id, payload, stop):
        question = payload.get('question')
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError('无效的补充信息请求')
        with self.lock:
            if stop.is_set(): raise PiCancelled()
            request_id = self.store.input_request(run_id, agent_id, payload)
            waiter = {'run_id': run_id, 'event': threading.Event(), 'answer': None}
            self.input_waiters[request_id] = waiter
            self.emit(run_id, 'input.requested', {'id': request_id, 'agent_id': agent_id, **payload})
            self.emit(run_id, 'agent.status', {'agent_id': agent_id, 'status': 'waiting_input'})
            self._refresh_attention(run_id)
        try:
            while not waiter['event'].wait(.1):
                if stop.is_set(): raise PiCancelled()
            if stop.is_set(): raise PiCancelled()
            with self.lock:
                self.emit(run_id, 'agent.status', {'agent_id': agent_id, 'status': 'running'})
                self._refresh_attention(run_id)
            return waiter['answer']
        finally:
            with self.lock: self.input_waiters.pop(request_id, None)

    def answer(self, run_id, request_id, answer):
        if not isinstance(request_id, str) or len(request_id) != 32: raise ValueError('无效的信息请求 ID')
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 16000:
            raise ValueError('回答需包含 1–16000 个字符')
        with self.lock:
            self.store.run(run_id)
            if run_id in self.active and self.active[run_id].is_set(): raise ValueError('任务正在停止')
            request = self.store.answer_input(run_id, request_id, answer.strip())
            self.task_context.request(run_id, '用户补充（问题：'+request['data']['question']+'）：'+answer.strip(), source_text=answer.strip())
            self.emit(run_id, 'input.answered', {'id': request_id, 'agent_id': request['agent_id'], 'question': request['data']['question'], 'answer': answer.strip(), 'attempt_id': request['data'].get('attempt_id')})
            waiter = self.input_waiters.get(request_id)
            if waiter:
                waiter['answer'] = answer.strip()
                waiter['event'].set()
                self._refresh_attention(run_id)
                return {'saved': True, 'resumed': True}
            if any(r['status'] == 'pending' for r in self.store.inputs(run_id)):
                self._refresh_attention(run_id)
                return {'saved': True, 'resumed': False}
            replies = [{'question': r['data']['question'], 'answer': r['answer']} for r in self.store.inputs(run_id) if r['status'] == 'answered']
            try:
                self.start(run_id, '用户已补充信息。继续原任务，核对已有结果：' + json.dumps(replies, ensure_ascii=False))
                return {'saved': True, 'resumed': True}
            except (ValueError, OSError) as exc:
                self.emit(run_id, 'status', {'status': 'interrupted', 'reason': '回答已保存，点击继续任务：' + str(exc)})
                return {'saved': True, 'resumed': False}

    def _approve(self, run_id, agent_id, data, stop, attempt=None):
        with self.lock:
            if stop.is_set(): raise PiCancelled()
            data = {**data, 'agent_id': agent_id, 'attempt_id': attempt}
            approval_id = self.store.approval(run_id, data)
            waiter = {'run_id': run_id, 'decision': None, 'event': threading.Event()}
            self.waiters[approval_id] = waiter
            self.emit(run_id, 'approval.requested', {'id': approval_id, **data})
            self.emit(run_id, 'agent.status', {'agent_id': agent_id, 'attempt_id': attempt, 'status': 'waiting_approval'})
            self._refresh_attention(run_id)
        try:
            deadline = time.monotonic() + 590
            while not waiter['event'].wait(.1):
                if stop.is_set() or time.monotonic() > deadline: break
            with self.lock:
                if waiter['decision'] is None: self.store.resolve_approval(approval_id, 'expired')
                self.waiters.pop(approval_id, None)
                self.emit(run_id, 'approval.resolved', {'id': approval_id, 'agent_id': agent_id, 'attempt_id': attempt, 'decision': 'approved' if waiter['decision'] else 'denied' if waiter['decision'] is False else 'expired'})
                if stop.is_set(): raise PiCancelled()
                self.emit(run_id, 'agent.status', {'agent_id': agent_id, 'status': 'running'})
                self._refresh_attention(run_id)
            return waiter['decision'] is True
        finally:
            with self.lock: self.waiters.pop(approval_id, None)

    def _run(self, run, workspace, prompt, stop):
        attempt = uuid.uuid4().hex
        try:
            budget = {'count': 0, 'failed': False, 'catalog': self.agents.list(enabled_only=True), 'plan': []}
            saved=json.loads(run.get('plan') or '[]')
            if run['status'] in ('failed','interrupted','waiting_input') and saved and all(all(k in step for k in ('id','task','agent','depends_on')) for step in saved):
                budget['plan']=[{**step,'status':'completed' if step['status']=='completed' else 'pending'} for step in saved]
                self.emit(run['id'],'plan.updated',{'steps':budget['plan'],'attempt_id':attempt})
                prompt+='\n继续以下保存的计划，复用 step_id，已完成步骤不重复执行，未完成步骤仍需完成：'+json.dumps(budget['plan'],ensure_ascii=False)
            self._session(run, workspace, prompt, stop, 'main', None, '主 Agent', attempt, budget)
            if stop.is_set(): raise PiCancelled()
            if budget['failed']: raise ValueError('本次执行存在失败的子任务，请查看执行记录后重试')
            if any(s['status'] != 'completed' for s in budget['plan']): raise ValueError('计划仍有未完成步骤，请继续处理')
            if not budget.get('completion'):
                self.emit(run['id'],'task.needs_completion',{'reason':'Agent 已结束本轮执行，但未提交完成标记；输出与文件记录已保留，请核对后继续收尾'})
                self.emit(run['id'],'status',{'status':'interrupted','reason':'待核对并收尾：Agent 未提交完成标记，已有输出与文件记录已保留'})
                return
            try:
                with self.lock:archive_lock=self.mutation_locks.setdefault(str(workspace),threading.Lock())
                with archive_lock:
                    archive=create_archive(self.store,self.store.run(run['id']),workspace,budget['completion'],self.config.directory,stop)
            except (OSError,ValueError) as exc:
                self.emit(run['id'],'archive.failed',{'error':str(exc)})
                self.emit(run['id'],'status',{'status':'interrupted','reason':'交付已产生，但保存工作区总结失败：'+str(exc)})
                return
            with self.lock:
                if stop.is_set(): raise PiCancelled()
                self.emit(run['id'],'task.archived',archive)
                self.emit(run['id'], 'task.completed', {'summary': budget['completion']})
                self.emit(run['id'], 'status', {'status': 'completed'})
        except PiCancelled:
            self.emit(run['id'], 'status', {'status': 'cancelled'})
        except Exception as exc:
            self.emit(run['id'], 'error', {'text': str(exc)})
            self.emit(run['id'], 'status', {'status': 'cancelled' if stop.is_set() else 'failed', 'reason': str(exc)})
        finally:
            with self.lock:
                self.store.cancel_inputs(run['id'])
                self.active.pop(run['id'], None)

    def _delegate(self, run, workspace, request, stop, attempt, budget):
        tasks = request.get('tasks')
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 4: raise ValueError('每次委派需包含 1–4 个子任务')
        for task in tasks:
            if not isinstance(task, dict) or task.get('agent') not in {a['name'] for a in budget['catalog']} or not isinstance(task.get('task'), str) or not 1 <= len(task['task']) <= 8000: raise ValueError('无效子任务或角色')
        with self.lock:
            if budget['count'] + len(tasks) > 16: raise ValueError('本次根任务最多创建 16 个子 Agent')
            prepare_batch(tasks, budget['plan'])
            if budget['plan']:self.emit(run['id'],'plan.updated',{'steps':budget['plan'],'attempt_id':attempt})
            budget['count'] += len(tasks)
            if budget['count'] > 16: raise ValueError('本次根任务最多创建 16 个子 Agent')
        batch_id = uuid.uuid4().hex
        entries = [{**task, 'agent_id': uuid.uuid4().hex} for task in tasks]
        self.emit(run['id'], 'workflow.batch_started', {'batch_id': batch_id, 'attempt_id': attempt, 'tasks': entries})
        def child(task):
            agent_id = task['agent_id']
            try:
                text = self._session(run, workspace, task['task'], stop, agent_id, 'main', task['agent'], attempt, budget, batch_id)
                return {'agent_id': agent_id, 'agent': task['agent'], 'success': True, 'result': text[:32000]}
            except PiCancelled: raise
            except Exception as exc:
                with self.lock: budget['failed'] = True
                return {'agent_id': agent_id, 'agent': task['agent'], 'success': False, 'error': str(exc)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(child, entries))
        if budget['plan']:
            for task,result in zip(entries,results):
                step=next(s for s in budget['plan'] if s['id']==task['step_id'])
                step['status']='completed' if result['success'] else 'failed'
                step['agent_id']=result['agent_id']
            self.emit(run['id'],'plan.updated',{'steps':budget['plan'],'attempt_id':attempt})
        self.emit(run['id'], 'workflow.batch_completed', {'batch_id': batch_id, 'attempt_id': attempt, 'success': all(r['success'] for r in results)})
        return results

    def _session(self, run, workspace, prompt, stop, agent_id, parent_id, role, attempt, budget, batch_id=None):
        run_id = run['id']
        selected_sources=self.sources.get(run_id)
        if selected_sources and (parent_id or not prompt.startswith(selected_sources['text'])):
            prompt+='\n本任务已选资料可用 workbench_context(context_id="selected") 与 workbench_document 读取；只能引用选中快照。创建时目标：'+selected_sources['packet']['goal']+'\n创建时硬约束（本轮明确更新优先）：'+selected_sources['packet']['constraints']
        session_id = run_id if agent_id == 'main' else agent_id
        emit = lambda kind, data: self.emit(run_id, kind, {**data, 'agent_id': agent_id, 'parent_agent_id': parent_id, 'attempt_id': attempt, 'batch_id': batch_id})
        emit('agent.started', {'role': role, 'task': prompt, 'status': 'running', 'session_id': str(uuid.UUID(session_id))})
        client = None
        with self.lock:
            mutation_lock = self.mutation_locks.setdefault(str(workspace), threading.Lock())
        leases = {}
        state = {'paused': 0, 'snapshots': {}, 'structured': False, 'compact_requested': False, 'settled': False, 'error': None, 'text': '', 'aborted': False, 'calls': {}}
        def file_text(raw):
            if not isinstance(raw, str) or not raw: return None
            try:
                target = (workspace / raw).resolve()
                if not target.is_relative_to(workspace): return None
                parts = target.relative_to(workspace).parts
                if any(p in ('.git', '.ssh', '.aws', '.codex') or p.startswith(('.env', '.workbench')) for p in parts): return None
                if target == self.config.directory or self.config.directory in target.parents: return None
                if not target.exists(): return ''
                if target.stat().st_size > 1024 * 1024: return None
                return target.read_text(encoding='utf-8')
            except (OSError, UnicodeError): return None
        def on_event(event):
            kind = event.get('type')
            if kind == 'extension_ui_request':
                interaction_started = time.monotonic()
                method = event.get('method')
                response = {'type': 'extension_ui_response', 'id': event.get('id')}
                if method == 'input' and event.get('title') in ('__workbench_create_agent__', '__workbench_context__','__workbench_document__','__workbench_memory__'):
                    try:
                        payload = json.loads(event.get('placeholder', '{}'))
                        if not isinstance(payload,dict):raise ValueError('工作台工具需要 JSON 对象')
                        if event['title'] == '__workbench_create_agent__':
                            if any(a['name'] == payload.get('name') for a in self.agents.list()): raise ValueError('此名称已存在，请使用新的名称')
                            result = self.agents.save(payload)
                            budget['catalog'].append({**result, 'source':'custom', 'enabled':True})
                            emit('agent.definition_created', {'name':result['name'], 'description':result['description']})
                        elif event['title']=='__workbench_memory__':
                            action = payload.get('action')
                            if action == 'assemble': result = self.task_context.assemble(run_id, agent_id)
                            elif action == 'offload': result = self.task_context.offload(run_id, agent_id, payload)
                            else: result = self.task_context.tool(run_id, payload, agent_id)
                        elif event['title']=='__workbench_document__':result=self.sources.tool(run_id,payload) if self.sources.get(run_id) else self.documents.tool(self.documents.for_task(run_id),payload)
                        else: result = self.sources.context(run_id,payload['context_id'],payload.get('offset',0)) if self.sources.get(run_id) else self.conversations.context(run_id, payload['context_id'], payload.get('offset', 0))
                        response['value'] = json.dumps(result, ensure_ascii=False)
                    except (ValueError, KeyError, TypeError) as exc: response['value'] = json.dumps({'error': str(exc)}, ensure_ascii=False)
                    client.send(response)
                elif method == 'input' and event.get('title') == '__workbench_plan__' and agent_id == 'main':
                    try:
                        if any(s['status'] in ('running','completed') for s in budget['plan']):raise ValueError('已执行的计划不能被覆盖，请继续当前步骤')
                        payload=json.loads(event.get('placeholder','{}'))
                        budget['plan']=validate_plan(payload.get('steps'),budget['catalog'])
                        emit('plan.updated',{'steps':budget['plan']})
                        response['value']=json.dumps({'saved':True,'steps':budget['plan']},ensure_ascii=False)
                    except (ValueError,TypeError) as exc:response['value']=json.dumps({'error':str(exc)},ensure_ascii=False)
                    client.send(response)
                elif method == 'input' and event.get('title') == '__workbench_delegate__' and agent_id == 'main':
                    try:
                        result = self._delegate(run, workspace, json.loads(event.get('placeholder', '{}')), stop, attempt, budget)
                        response['value'] = json.dumps(result, ensure_ascii=False)
                    except PiCancelled: raise
                    except Exception as exc: response['value'] = json.dumps({'error': str(exc)}, ensure_ascii=False)
                    client.send(response)
                elif method == 'confirm':
                    try: preview = json.loads(event.get('message', '{}'))
                    except (ValueError, TypeError): preview = {'name': event.get('title', 'Pi 审批'), 'message': event.get('message', '')}
                    if not isinstance(preview, dict): preview = {'name': 'Pi 审批', 'message': str(preview)}
                    response['confirmed'] = self._approve(run_id, agent_id, preview, stop, attempt)
                    if response['confirmed']:
                        # Hold the shared workspace mutation lease until Pi reports
                        # the matching tool result, not just until approval returns.
                        call_id = preview.get('call_id')
                        if call_id:
                            while not mutation_lock.acquire(timeout=.1):
                                if stop.is_set(): raise PiCancelled()
                            leases[call_id] = mutation_lock
                    client.send(response)
                elif method in ('input', 'editor', 'select'):
                    if event.get('title') == '__workbench_ask__':
                        payload = json.loads(event.get('placeholder', '{}'))
                    else:
                        payload = {'question': event.get('title') or '请补充信息', 'options': event.get('options', [])}
                    response['value'] = self._ask(run_id, agent_id, {**payload, 'attempt_id': attempt}, stop)
                    client.send(response)
                elif method == 'notify' and str(event.get('message', '')).startswith('__workbench_context_state__'):
                    telemetry=json.loads(event['message'][len('__workbench_context_state__'):])
                    if telemetry['phase']=='requested':state['compact_requested']=True
                    emit('context.' + telemetry['phase'],telemetry)
                elif method == 'notify' and str(event.get('message', '')).startswith('__workbench_finish__') and agent_id == 'main':
                    report = json.loads(event['message'][len('__workbench_finish__'):])
                    if not isinstance(report.get('summary'), str) or not report['summary'].strip(): raise ValueError('无效的完成报告')
                    budget['completion'] = report['summary']
                    emit('task.reported', report)
                else: emit('pi.notification', event)
                state['paused'] += time.monotonic() - interaction_started
                return
            if kind == 'stderr':
                emit('pi.diagnostic', {'text': event['text']})
                return
            if kind == 'extension_error':
                raise ValueError('Pi 工作台扩展异常：' + event.get('error', '未知错误'))
            if kind == 'message_update':
                update = event.get('assistantMessageEvent', {})
                if update.get('type') == 'text_delta': emit('message.delta', {'text': update.get('delta', '')})
                return
            if kind == 'message_start' and event.get('message', {}).get('role') == 'assistant':
                emit('message.started', {'id': uuid.uuid4().hex})
            elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
                message = event['message']
                state['text'] = ''.join(part.get('text', '') for part in message.get('content', []) if part.get('type') == 'text')
                state['error'] = message.get('errorMessage', 'Pi 模型请求失败') if message.get('stopReason') == 'error' else None
                state['aborted'] = message.get('stopReason') == 'aborted'
                emit('message.completed', {'text': state['text']})
                usage = message.get('usage', {})
                emit('usage', {'model': message.get('model', run['model']), 'prompt_tokens': usage.get('input', 0), 'completion_tokens': usage.get('output', 0), 'total_tokens': usage.get('totalTokens', 0)})
            elif kind == 'tool_execution_start':
                state['calls'][event['toolCallId']] = event
                if event['toolName'] in ('write', 'edit'):
                    state['snapshots'][event['toolCallId']] = file_text(event.get('args', {}).get('path', ''))
                emit('tool.started', {'id': event['toolCallId'], 'name': event['toolName'], 'arguments': event.get('args', {})})
            elif kind == 'tool_execution_end':
                if event.get('toolName') == 'structured_output' and not event.get('isError'):
                    output = (event.get('result') or {}).get('details') or {}
                    if not isinstance(output.get('summary'), str): raise ValueError('结构化输出缺少总结')
                    state['text'] = output['summary'] + '\n' + '\n'.join(output.get('actionItems', []))
                    state['structured'] = True
                    if agent_id == 'main':
                        budget['completion'] = output['summary']
                        emit('task.reported', {'summary': output['summary'], 'source':'structured_output'})
                    emit('output.structured', {'output': output})
                emit('tool.completed', {'id': event['toolCallId'], 'name': event['toolName'], 'result': event.get('result'), 'is_error': event.get('isError', False)})
                if event.get('toolName') in ('write', 'edit') and not event.get('isError'):
                    args = state['calls'].get(event['toolCallId'], {}).get('args', {})
                    result = event.get('result') or {}
                    diff = (result.get('details') or {}).get('diff', '')
                    before = state['snapshots'].pop(event['toolCallId'], None)
                    after = file_text(args.get('path', ''))
                    if not diff and before is not None and after is not None:
                        lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile='修改前/' + args.get('path', '?'), tofile='修改后/' + args.get('path', '?'), lineterm=''))
                        diff = '\n'.join(lines[:400]) + ('\n… 差异超过 400 行，已截断' if len(lines) > 400 else '')
                    emit('file.changed', {'path': args.get('path', '?'), 'diff': diff, 'tool_call_id': event['toolCallId'], 'source': 'pi'})
                lease = leases.pop(event['toolCallId'], None)
                if lease: lease.release()
            elif kind == 'tool_execution_update': emit('tool.output', {'id': event.get('toolCallId'), 'result': event.get('partialResult')})
            elif kind == 'agent_settled':
                state['settled'], state['aborted'] = True, event.get('aborted', state['aborted'])
                emit('pi.agent_settled', {'aborted': state['aborted']})
            elif kind not in ('response',): emit('pi.' + str(kind), event)
        try:
            if stop.is_set():raise PiCancelled()
            agent_definition = next((a for a in budget['catalog'] if a['name'] == role), None) if parent_id else None
            client = self.client_factory(self.config.require_pi(), workspace, session_id,
                self.config.directory / 'pi-sessions' / run_id, model=(agent_definition.get('model') or run['model']) if agent_definition else run['model'],
                role=self.agents.snapshot(agent_definition) if agent_definition else None,
                agent_catalog=budget['catalog'],
                shell_mode=self.store.setting('shell_mode', 'disabled'), data_dir=self.config.directory)
            commands = client.request('get_commands', stop, on_event)
            if not any(c.get('name') == 'workbench-status' for c in commands.get('commands', [])):
                raise ValueError('Pi 工作台扩展未加载，已停止执行')
            info = client.request('get_state', stop, on_event)
            emit('session.connected', {'session_id': info.get('sessionId'), 'session_file': info.get('sessionFile'), 'message_count': info.get('messageCount', 0)})
            accepted = client.request('prompt', stop, on_event, message=prompt)
            if accepted.get('disposition') == 'handled': raise ValueError('请求被命令处理，未启动任务')
            deadline = time.monotonic() + 1800
            while not state['settled']:
                if time.monotonic() > deadline + state['paused']: raise ValueError('Pi 会话实际执行超过 30 分钟上限')
                try: event = client.next(stop, timeout=1)
                except ValueError as exc:
                    if str(exc) == '等待 Pi 响应超时': continue
                    raise
                on_event(event)
            if stop.is_set() or state['aborted']: raise PiCancelled()
            if state['error']: raise ValueError(state['error'])
            if state['compact_requested']:
                emit('context.started',{'phase':'started'})
                try:
                    compacted=client.compact(stop,on_event)
                    emit('context.completed',{'phase':'completed','summary':compacted.get('summary','')})
                except PiCancelled:raise
                except Exception as exc:emit('context.failed',{'phase':'failed','error':str(exc)})
            if not state['text']: raise ValueError('Pi 未返回最终结果')
            if state['structured']: emit('message.completed', {'text': state['text']})
            emit('agent.completed', {'status': 'completed', 'result': state['text']})
            return state['text']
        except PiCancelled:
            emit('agent.completed', {'status': 'cancelled'})
            raise
        except Exception as exc:
            emit('agent.completed', {'status': 'failed', 'error': str(exc)})
            raise
        finally:
            if client: client.close()
            for lease in leases.values(): lease.release()

    def board(self):
        tasks, metadata = [], self.store.board_metadata()
        for run in self.store.runs():
            plan = json.loads(run.get('plan') or '[]')
            tasks.append({**run, **metadata.get(run['id'], {}), 'bucket': BUCKETS.get(run['status'], 'attention'), 'plan': plan,
                'summary': run.get('summary') or '', 'reason': run.get('reason') or '',
                'current_step': next((step['title'] for step in plan if step['status'] == 'running'), None)})
        return {'tasks': tasks, 'counts': {key: sum(t['bucket'] == key for t in tasks) for key in ('pending','running','attention','done','cancelled')}}

    def shutdown(self):
        with self.lock:
            self.closed=True
            for stop in self.active.values(): stop.set()
            self.conversations.shutdown()
        self.documents.shutdown()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with self.lock:
                if not self.active and not self.conversations.active:return
            time.sleep(.02)
