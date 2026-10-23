"""Persistent conversations: Q&A stays independent; planning links to real Pi tasks."""
import hashlib
import json
import threading
import time
import uuid
from .pi_client import PiCancelled


class Conversations:
    def __init__(self, runtime):
        self.runtime, self.store, self.lock = runtime, runtime.store, runtime.lock
        self.active, self.streams = {}, {}
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,mode TEXT,title TEXT,workspace TEXT,model TEXT,status TEXT,task_id TEXT,created REAL,updated REAL);
            CREATE TABLE IF NOT EXISTS conversation_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT,role TEXT,text TEXT,status TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS conversation_events(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT,kind TEXT,data TEXT,created REAL);
            CREATE INDEX IF NOT EXISTS conversation_events_stream ON conversation_events(conversation_id,id);
            CREATE TABLE IF NOT EXISTS context_snapshots(id TEXT PRIMARY KEY,conversation_id TEXT,task_id TEXT,data TEXT,digest TEXT,created REAL);
            ''')
            columns={r['name'] for r in self.store.db.execute('PRAGMA table_info(conversations)')}
            for name in ('deleted','custom_title'):
                if name not in columns:self.store.db.execute(f'ALTER TABLE conversations ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
            self.store.db.execute("UPDATE conversations SET status='interrupted' WHERE status='running'")
            self.store.db.execute("UPDATE conversation_messages SET status='interrupted' WHERE status='streaming'")

    def create(self, mode, workspace='', model=''):
        if mode not in ('chat','planning'): raise ValueError('请选择普通问答或任务规划')
        if workspace: self.runtime.config.workspace(workspace)
        elif mode=='planning': raise ValueError('任务规划需要选择工作区')
        if not isinstance(model,str) or len(model)>200 or model.startswith('-'): raise ValueError('无效模型标识')
        model=model.strip() or self.runtime.models.default();self.runtime.models.validate_model(model)
        conversation_id=uuid.uuid4().hex
        now=time.time()
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT INTO conversations(id,mode,title,workspace,model,status,task_id,created,updated) VALUES(?,?,?,?,?,?,?,?,?)',(conversation_id,mode,'新对话',workspace,model,'idle',None,now,now))
        return self.get(conversation_id)

    def get(self, conversation_id):
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM conversations WHERE id=? AND deleted=0',(conversation_id,)).fetchone()
            if row is None: raise ValueError('对话不存在')
            messages=self.store.db.execute('SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY id',(conversation_id,)).fetchall()
            telemetry=self.store.db.execute("SELECT data FROM conversation_events WHERE conversation_id=? AND kind LIKE 'context.%' ORDER BY id DESC LIMIT 1",(conversation_id,)).fetchone()
            execution=self.store.db.execute("SELECT data,created FROM conversation_events WHERE conversation_id=? AND kind='execution' ORDER BY id DESC LIMIT 150",(conversation_id,)).fetchall()
            cursor=self.store.db.execute('SELECT COALESCE(MAX(id),0) FROM conversation_events WHERE conversation_id=?',(conversation_id,)).fetchone()[0]
        return {**dict(row),'messages':[dict(m) for m in messages],'context':json.loads(telemetry['data']) if telemetry else None,'execution':[{**json.loads(e['data']),'created':e['created']} for e in reversed(execution)],'cursor':cursor,'documents':self.runtime.documents.list(conversation_id)}

    def execution(self, conversation_id, kind, data=None):
        # Only observable metadata: never publish thinking, tool output or file bodies.
        data=data or {}
        safe={key:data[key] for key in ('agent_id','parent_agent_id','role','name','id','status','is_error','path') if key in data}
        if isinstance(data.get('arguments'),dict):
            safe['arguments']={k:v for k,v in data['arguments'].items() if k in ('action','document_id','page','path','offset') and isinstance(v,(str,int))}
        if kind=='plan.updated':safe['steps']=[{k:s[k] for k in ('id','title','agent','status') if k in s} for s in data.get('steps',[])]
        self.event(conversation_id,'execution',{'kind':kind,**safe})

    def list(self):
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute('SELECT * FROM conversations WHERE deleted=0 ORDER BY updated DESC LIMIT 100')]

    def rename(self, conversation_id, title):
        if not isinstance(title,str) or not 1<=len(title.strip())<=80 or any(ord(c)<32 for c in title):raise ValueError('标题需为 1–80 个字符，不能包含换行')
        with self.lock:
            self.get(conversation_id)
            with self.store.lock,self.store.db:self.store.db.execute('UPDATE conversations SET title=?,custom_title=1 WHERE id=?',(title.strip(),conversation_id))
            self.event(conversation_id,'title.changed',{'title':title.strip()})
            return self.get(conversation_id)

    def delete(self, conversation_id):
        with self.lock:
            chat=self.get(conversation_id)
            if conversation_id in self.active or chat['task_id'] in self.runtime.active:raise ValueError('请先停止当前执行，再删除对话')
            # Keep immutable task context, source documents and Pi sessions for linked tasks.
            with self.store.lock,self.store.db:self.store.db.execute('UPDATE conversations SET deleted=1 WHERE id=?',(conversation_id,))
            return {'deleted':True,'id':conversation_id}

    def event(self, conversation_id, kind, data):
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT INTO conversation_events(conversation_id,kind,data,created) VALUES(?,?,?,?)',(conversation_id,kind,json.dumps(data,ensure_ascii=False),time.time()))
            self.store.db.execute('UPDATE conversations SET updated=? WHERE id=?',(time.time(),conversation_id))

    def events(self, conversation_id, after=0):
        with self.store.lock:
            return [{**dict(r),'data':json.loads(r['data'])} for r in self.store.db.execute('SELECT * FROM conversation_events WHERE conversation_id=? AND id>? ORDER BY id LIMIT 1000',(conversation_id,after))]

    def status(self, conversation_id, status):
        with self.store.lock,self.store.db:
            self.store.db.execute('UPDATE conversations SET status=? WHERE id=?',(status,conversation_id))
        self.event(conversation_id,'status',{'status':status})

    def message(self, conversation_id, role, text, status='completed'):
        with self.store.lock,self.store.db:
            cursor=self.store.db.execute('INSERT INTO conversation_messages(conversation_id,role,text,status,created) VALUES(?,?,?,?,?)',(conversation_id,role,text,status,time.time()))
            message_id=cursor.lastrowid
            if role=='user': self.store.db.execute("UPDATE conversations SET title=CASE WHEN title='新对话' AND custom_title=0 THEN ? ELSE title END WHERE id=?",(text[:60],conversation_id))
        self.event(conversation_id,'message',{'id':message_id,'role':role,'text':text,'status':status})
        return message_id

    def update_message(self, conversation_id, message_id, text, status='streaming'):
        # Snapshot and its event cursor are atomic: clients can resume deltas
        # without replaying bytes already present in the fetched message.
        with self.store.lock,self.store.db:
            row=self.store.db.execute('SELECT text FROM conversation_messages WHERE id=? AND conversation_id=?',(message_id,conversation_id)).fetchone()
            if row is None:raise ValueError('消息不存在')
            previous=row['text']
            self.store.db.execute('UPDATE conversation_messages SET text=?,status=? WHERE id=? AND conversation_id=?',(text,status,message_id,conversation_id))
            if status=='streaming' and text.startswith(previous):
                self.event(conversation_id,'message.delta',{'id':message_id,'delta':text[len(previous):],'status':status})
            else:self.event(conversation_id,'message',{'id':message_id,'role':'assistant','text':text,'status':status})

    def snapshot(self, conversation_id):
        chat=self.get(conversation_id)
        messages=[{k:m[k] for k in ('id','role','text','status')} for m in chat['messages']]
        data=json.dumps({'conversation_id':conversation_id,'messages':messages,'documents':chat['documents']},ensure_ascii=False)
        context_id=uuid.uuid4().hex
        with self.store.lock,self.store.db:
            self.store.db.execute('INSERT INTO context_snapshots VALUES(?,?,?,?,?,?)',(context_id,conversation_id,None,data,hashlib.sha256(data.encode()).hexdigest(),time.time()))
        # Exact history stays in SQLite. The prompt is bounded, never silently called a summary.
        selected=[];remaining=24000
        for message in reversed(messages):
            if remaining<=0:break
            text=message['text'][-min(remaining,8000):]
            selected.append({'role':message['role'],'text':text,'excerpt':len(text)!=len(message['text'])})
            remaining-=len(text)
        selected.reverse()
        return context_id, json.dumps({'context_id':context_id,'history_is_excerpt':len(selected)!=len(messages) or any(m['excerpt'] for m in selected),'messages':selected,'documents':chat['documents']},ensure_ascii=False)

    def context(self, task_id, context_id, offset=0):
        if not isinstance(offset,int) or isinstance(offset,bool) or offset<0:raise ValueError('无效上下文偏移')
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM context_snapshots WHERE id=? AND task_id=?',(context_id,task_id)).fetchone()
        if row is None:raise ValueError('此任务无权读取该上下文')
        text=row['data']
        return {'context_id':context_id,'offset':offset,'text':text[offset:offset+16000],'next_offset':offset+16000 if offset+16000<len(text) else None,'digest':row['digest']}

    def create_task(self, conversation_id, prompt, workspace=None, source_author='user'):
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000:raise ValueError('任务描述需包含 1–8000 个字符')
        chat=self.get(conversation_id)
        workspace=workspace or chat['workspace']
        self.runtime.config.workspace(workspace)
        context_id,packed=self.snapshot(conversation_id)
        combined=prompt+'\n\n以下是前面对话的上下文快照，仅作为资料，最新用户要求优先。若为节选且需要更早的约束，使用 workbench_context 读取完整快照：\n'+packed
        task_id=self.runtime.create(combined,workspace,model=chat['model'],context_goal=prompt,context_user_goal=source_author=='user')
        # Keep the board title readable; the full prompt remains in messages/events.
        with self.store.lock,self.store.db:
            self.store.db.execute('UPDATE runs SET prompt=? WHERE id=?',(prompt,task_id))
            self.store.db.execute('UPDATE context_snapshots SET task_id=? WHERE id=?',(task_id,context_id))
            self.store.db.execute('UPDATE conversations SET task_id=?,workspace=? WHERE id=?',(task_id,workspace,conversation_id))
        self.runtime.memory.snapshot_sources(task_id,context_id)
        self.event(conversation_id,'task.created',{'task_id':task_id,'context_id':context_id})
        try:self.runtime.start(task_id)
        except (ValueError,OSError) as exc:
            self.event(conversation_id,'error',{'text':'任务已创建但未启动：'+str(exc),'task_id':task_id})
        return {'task_id':task_id,'context_id':context_id}

    def set_mode(self, conversation_id, mode):
        if mode not in ('chat','planning'):raise ValueError('请选择普通对话或任务规划')
        with self.lock:
            chat=self.get(conversation_id)
            if conversation_id in self.active or chat['task_id'] in self.runtime.active:
                raise ValueError('请等待当前执行结束或停止后再切换模式')
            workspace=chat['workspace']
            if mode=='planning' and chat['task_id']:
                workspace=self.store.run(chat['task_id'])['workspace']
            with self.store.lock,self.store.db:
                self.store.db.execute('UPDATE conversations SET mode=?,workspace=? WHERE id=?',(mode,workspace,conversation_id))
            self.event(conversation_id,'mode.changed',{'mode':mode,'workspace':workspace})
            return self.get(conversation_id)

    def send(self, conversation_id, prompt, workspace=None):
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000:raise ValueError('消息需包含 1–8000 个字符')
        prompt=prompt.strip()
        with self.lock:
            if self.runtime.closed:raise ValueError('工作台正在关闭')
            chat=self.get(conversation_id)
            if conversation_id in self.active:raise ValueError('请等待本次回答结束')
            if chat['mode']=='planning' and chat['task_id'] and workspace is not None and workspace!=chat['workspace']:
                raise ValueError('已有规划任务固定在原工作区；要切换工作区，请新建规划对话')
            if workspace is not None:
                if workspace:self.runtime.config.workspace(workspace)
                elif chat['mode']=='planning':raise ValueError('任务规划需要选择工作区')
                with self.store.lock,self.store.db:self.store.db.execute('UPDATE conversations SET workspace=? WHERE id=?',(workspace,conversation_id))
                chat['workspace']=workspace
            if chat['mode']=='planning' and chat['task_id']:
                task_id=chat['task_id']
                pending=[r for r in self.store.inputs(task_id) if r['status']=='pending']
                if task_id in self.runtime.active and not pending:raise ValueError('任务正在执行，可在任务详情查看或停止后追加要求')
                if len(pending)>1:raise ValueError('有多个待回答问题，请在任务详情逐项回答')
                self.message(conversation_id,'user',prompt)
                if pending:self.runtime.answer(task_id,pending[0]['id'],prompt)
                else:self.runtime.start(task_id,prompt)
                return {'id':conversation_id,'task_id':task_id}
            self.runtime.config.require_pi()
            if chat['mode']=='chat' and len(self.active)>=4:raise ValueError('最多同时生成四个普通对话回答')
            self.message(conversation_id,'user',prompt)
            if chat['mode']=='planning':
                result=self.create_task(conversation_id,prompt,chat['workspace'])
                return {'id':conversation_id,**result}
            stop=threading.Event();self.active[conversation_id]=stop
            self.status(conversation_id,'running')
            self.execution(conversation_id,'request.started')
            threading.Thread(target=self._reply,args=(chat,prompt,stop),daemon=True).start()
            return {'id':conversation_id}

    def on_task_event(self, task_id, kind, data):
        with self.lock:
            with self.store.lock:rows=self.store.db.execute('SELECT id,mode FROM conversations WHERE task_id=?',(task_id,)).fetchall()
            for row in rows:
                chat_id=row['id']
                if row['mode']=='planning' and kind in ('status','agent.started','agent.status','agent.completed','session.connected','message.started','tool.started','tool.completed','file.changed','plan.updated'):
                    self.execution(chat_id,kind,data)
                if kind=='status':
                    self.event(chat_id,'task.status',{'task_id':task_id,**data})
                    if row['mode']=='planning':self.status(chat_id,'running' if data['status'] in ('running','stopping') else 'attention' if data['status'] in ('waiting_input','waiting_approval','failed','interrupted') else 'idle')
                if data.get('agent_id')!='main' or row['mode']!='planning':continue
                if kind=='message.started':self.streams[task_id]={'id':self.message(chat_id,'assistant','','streaming'),'text':''}
                elif kind in ('message.delta','message.completed'):
                    if task_id not in self.streams:self.streams[task_id]={'id':self.message(chat_id,'assistant','','streaming'),'text':''}
                    stream=self.streams[task_id]
                    if not stream['text'] and data.get('text'):self.execution(chat_id,'answer.started',data)
                    stream['text']=stream['text']+data.get('text','') if kind=='message.delta' else data.get('text','')
                    self.update_message(chat_id,stream['id'],stream['text'],'completed' if kind=='message.completed' else 'streaming')

    def _reply(self, chat, prompt, stop):
        chat_id=chat['id'];client=None;message_id=None;text='';settled=False;error=None;final_status='idle';compact_requested=False
        def on_event(event):
            nonlocal message_id,text,settled,error,compact_requested
            kind=event.get('type')
            if kind=='tool_execution_start':self.execution(chat_id,'tool.started',{'id':event.get('toolCallId'),'name':event.get('toolName'),'arguments':event.get('args',{})})
            elif kind=='tool_execution_end':self.execution(chat_id,'tool.completed',{'id':event.get('toolCallId'),'name':event.get('toolName'),'is_error':event.get('isError',False)})
            if kind=='message_start' and event.get('message',{}).get('role')=='assistant':
                self.execution(chat_id,'message.started')
                text='';message_id=self.message(chat_id,'assistant','','streaming')
            elif kind=='message_update' and event.get('assistantMessageEvent',{}).get('type')=='text_delta':
                if not text:self.execution(chat_id,'answer.started')
                text+=event['assistantMessageEvent'].get('delta','')
                if message_id is None:message_id=self.message(chat_id,'assistant','','streaming')
                self.update_message(chat_id,message_id,text)
            elif kind=='message_end' and event.get('message',{}).get('role')=='assistant':
                message=event['message'];text=''.join(p.get('text','') for p in message.get('content',[]) if p.get('type')=='text')
                if message_id is None:message_id=self.message(chat_id,'assistant','','streaming')
                self.update_message(chat_id,message_id,text,'completed')
                if message.get('stopReason')=='error':error=message.get('errorMessage','Pi 请求失败')
            elif kind=='extension_ui_request' and event.get('method')=='input':
                response={'type':'extension_ui_response','id':event['id']}
                try:
                    payload=json.loads(event.get('placeholder','{}'))
                    if event.get('title')=='__workbench_create_task__':result=self.create_task(chat_id,payload['prompt'],payload.get('workspace'),source_author='agent')
                    elif event.get('title')=='__workbench_document__':result=self.runtime.documents.tool(chat_id,payload)
                    elif event.get('title')=='__workbench_create_agent__':
                        if any(a['name']==payload.get('name') for a in self.runtime.agents.list()):raise ValueError('此名称已存在，请使用新的名称')
                        result=self.runtime.agents.save(payload)
                    else:raise ValueError('普通问答不支持此交互')
                    response['value']=json.dumps(result,ensure_ascii=False)
                except (ValueError,KeyError,TypeError,OSError) as exc:response['value']=json.dumps({'error':str(exc)},ensure_ascii=False)
                client.send(response)
            elif kind=='extension_ui_request' and event.get('method')=='notify' and str(event.get('message','')).startswith('__workbench_context_state__'):
                telemetry=json.loads(event['message'][len('__workbench_context_state__'):])
                if telemetry['phase']=='requested':compact_requested=True
                self.event(chat_id,'context.'+telemetry['phase'],telemetry)
            elif kind=='agent_settled':
                settled=True
                if event.get('aborted'):raise PiCancelled()
            elif kind=='extension_error':raise ValueError(event.get('error','扩展异常'))
        try:
            if stop.is_set():raise PiCancelled()
            handoff=''
            with self.store.lock:
                changed=self.store.db.execute("SELECT MAX(id) FROM conversation_events WHERE conversation_id=? AND kind='mode.changed'",(chat_id,)).fetchone()[0] or 0
                connected=self.store.db.execute("SELECT MAX(id) FROM conversation_events WHERE conversation_id=? AND kind='session.connected'",(chat_id,)).fetchone()[0] or 0
            if changed>connected:
                _,packed=self.snapshot(chat_id)
                handoff='\n\n以下是切换模式前后的对话资料（可能为节选，最新用户要求优先）：'+packed
            workspace=self.runtime.config.workspace(chat['workspace']) if chat['workspace'] else self.runtime.config.directory/'chat-workspace'
            workspace.mkdir(exist_ok=True)
            client=self.runtime.client_factory(self.runtime.config.require_pi(),workspace,chat_id,self.runtime.config.directory/'chat-sessions'/chat_id,model=chat['model'],data_dir=self.runtime.config.directory,agent_catalog=self.runtime.agents.list(enabled_only=True),mode='chat')
            commands=client.request('get_commands',stop,on_event)
            if not any(c.get('name')=='workbench-status' for c in commands.get('commands',[])):raise ValueError('Pi 工作台扩展未加载')
            state=client.request('get_state',stop,on_event)
            self.event(chat_id,'session.connected',{'session_id':state.get('sessionId'),'message_count':state.get('messageCount',0)})
            self.execution(chat_id,'session.connected')
            documents=self.runtime.documents.list(chat_id)
            model_prompt=prompt+handoff+('\n\n附件目录（原文必须通过 workbench_document 分页读取，不得仅凭文件名概括）：'+json.dumps(documents,ensure_ascii=False) if documents else '')
            client.request('prompt',stop,on_event,message=model_prompt)
            deadline=time.monotonic()+1800
            while not settled:
                if time.monotonic()>deadline:raise ValueError('对话超过 30 分钟上限')
                try:event=client.next(stop,timeout=1)
                except ValueError as exc:
                    if str(exc)=='等待 Pi 响应超时':continue
                    raise
                on_event(event)
            if error:raise ValueError(error)
            if compact_requested:
                self.event(chat_id,'context.started',{'phase':'started'})
                try:
                    compacted=client.compact(stop,on_event)
                    self.event(chat_id,'context.completed',{'phase':'completed','summary':compacted.get('summary','')})
                except PiCancelled:raise
                except Exception as exc:self.event(chat_id,'context.failed',{'phase':'failed','error':str(exc)})
            if stop.is_set():raise PiCancelled()
        except PiCancelled:final_status='interrupted'
        except Exception as exc:
            self.event(chat_id,'error',{'text':self.runtime.config.redact(str(exc))})
            final_status='failed'
        finally:
            if client:client.close()
            if message_id is not None:
                with self.store.lock,self.store.db:self.store.db.execute("UPDATE conversation_messages SET status='interrupted' WHERE id=? AND status='streaming'",(message_id,))
            with self.lock:
                self.active.pop(chat_id,None)
                self.status(chat_id,final_status)

    def cancel(self, conversation_id):
        with self.lock:
            chat=self.get(conversation_id)
            if conversation_id in self.active:self.active[conversation_id].set()
            if chat['mode']=='planning' and chat['task_id']:self.runtime.cancel(chat['task_id'])

    def shutdown(self):
        with self.lock:
            for stop in self.active.values():stop.set()
