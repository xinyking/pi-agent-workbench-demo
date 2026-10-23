"""Opt-in memory policy, grounded constraint extraction and scoped durable memory."""
import hashlib
import json
import re
import time
import uuid
from .constraint_extraction import extract, clauses, classify, PRIVATE, OPT_OUT, DURABLE

DEFAULTS={'task_memory':True,'auto_constraints':False,'long_term_read':False,'long_term_write':False,
          'tool_offload':True,'assembly_history':True,'retention_days':90}
BOOLS=set(DEFAULTS)-{'retention_days'}


class MemoryService:
    def __init__(self,runtime):
        self.runtime,self.store=runtime,runtime.store
        with self.store.lock,self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS memory_catalog(id INTEGER PRIMARY KEY,revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO memory_catalog VALUES(1,0);
            CREATE TABLE IF NOT EXISTS memory_user_sources(
                id TEXT PRIMARY KEY,task_id TEXT NOT NULL,position INTEGER NOT NULL,
                text TEXT NOT NULL,origin TEXT NOT NULL,created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS memory_source_task ON memory_user_sources(task_id,position);
            CREATE TABLE IF NOT EXISTS constraint_conflicts(
                id TEXT PRIMARY KEY,task_id TEXT NOT NULL,existing_id TEXT NOT NULL,
                source_id TEXT NOT NULL,key TEXT NOT NULL,quote TEXT NOT NULL,status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS long_term_memories(
                id TEXT PRIMARY KEY,scope TEXT NOT NULL,key TEXT NOT NULL,text TEXT NOT NULL,
                source_task_id TEXT,source_id TEXT,status TEXT NOT NULL,created REAL NOT NULL,
                updated REAL NOT NULL,expires REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS long_memory_scope ON long_term_memories(scope,status,expires);
            ''')

    def policy(self):
        saved=self.store.setting('memory_policy',{})
        return {**DEFAULTS,**saved,'revision':saved.get('revision',0),'auto_since':saved.get('auto_since',0)}

    def configure(self,payload):
        if set(payload)-set(DEFAULTS)-{'revision'}:raise ValueError('存在未知记忆设置')
        with self.store.lock,self.store.db:
            old=self.policy()
            if type(payload.get('revision')) is not int or payload['revision']!=old['revision']:raise ValueError('记忆设置已更新，请刷新后重试')
            new={**old,**payload}
            if any(type(new[k]) is not bool for k in BOOLS):raise ValueError('记忆开关必须为布尔值')
            if type(new['retention_days']) is not int or new['retention_days'] not in (30,90,365):raise ValueError('有效期请选择 30、90 或 365 天')
            if (new['auto_constraints'] and not old['auto_constraints']) or (new['long_term_write'] and not old['long_term_write']):new['auto_since']=time.time()
            new['revision']=old['revision']+1
            self.store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('memory_policy',json.dumps(new)))
        return new

    def _bump(self):self.store.db.execute('UPDATE memory_catalog SET revision=revision+1 WHERE id=1')

    def _forget_orphan_sources(self,source_ids):
        for source_id in set(source_ids)-{None}:
            linked=self.store.db.execute('SELECT 1 FROM long_term_memories WHERE source_id=?',(source_id,)).fetchone()
            pinned=self.store.db.execute("SELECT 1 FROM task_memory WHERE json_extract(source,'$.source_id')=?",(source_id,)).fetchone()
            conflict=self.store.db.execute('SELECT 1 FROM constraint_conflicts WHERE source_id=?',(source_id,)).fetchone()
            if not linked and not pinned and not conflict:self.store.db.execute('DELETE FROM memory_user_sources WHERE id=?',(source_id,))

    def _expire(self):
        with self.store.lock,self.store.db:
            expired=[r['source_id'] for r in self.store.db.execute('SELECT source_id FROM long_term_memories WHERE expires<=?',(time.time(),))]
            removed=self.store.db.execute('DELETE FROM long_term_memories WHERE expires<=?',(time.time(),)).rowcount
            self._forget_orphan_sources(expired)
            if removed:self._bump()

    def catalog(self):
        self._expire()
        with self.store.lock:
            revision=self.store.db.execute('SELECT revision FROM memory_catalog WHERE id=1').fetchone()[0]
            items=[dict(r) for r in self.store.db.execute('SELECT * FROM long_term_memories ORDER BY updated DESC,id')]
        return {'policy':self.policy(),'revision':revision,'items':items,'workspaces':self.store.setting('workspaces',[])}

    def _check_revision(self,payload):
        revision=self.store.db.execute('SELECT revision FROM memory_catalog WHERE id=1').fetchone()[0]
        if type(payload.get('revision')) is not int or payload['revision']!=revision:raise ValueError('长期记忆列表已更新，请刷新后重试')

    def _scope(self,scope):
        if scope=='global':return scope
        if not isinstance(scope,str):raise ValueError('请选择项目范围或全局偏好')
        return str(self.runtime.config.workspace(scope))

    def manage(self,payload):
        self._expire()
        action=payload.get('action','save')
        if payload.get('id') is not None and (not isinstance(payload['id'],str) or not re.fullmatch('[a-f0-9]{32}',payload['id'])):
            raise ValueError('无效长期记忆编号')
        with self.store.lock,self.store.db:
            self._check_revision(payload)
            old_sources=[r['source_id'] for r in self.store.db.execute('SELECT source_id FROM long_term_memories')]
            if action=='clear_task_memory':
                if payload.get('confirm') is not True:raise ValueError('请确认清除并关闭额外任务记忆')
                if self.runtime.active:raise ValueError('请先停止运行中的任务，再清除任务记忆')
                for table in ('task_memory','task_memory_versions','task_context_state','task_context_assemblies','task_context_artifacts','memory_user_sources','constraint_conflicts'):
                    self.store.db.execute('DELETE FROM '+table)
                policy=self.policy();policy.update(task_memory=False,auto_constraints=False,tool_offload=False,assembly_history=False,revision=policy['revision']+1)
                self.store.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('memory_policy',json.dumps(policy)))
                self._bump()
            elif action=='clear':
                if payload.get('confirm') is not True:raise ValueError('请确认清除全部长期记忆')
                self.store.db.execute('DELETE FROM long_term_memories');self._bump()
            elif action=='delete':
                if not isinstance(payload.get('id'),str):raise ValueError('需要记忆编号')
                if not self.store.db.execute('DELETE FROM long_term_memories WHERE id=?',(payload['id'],)).rowcount:raise ValueError('记忆不存在')
                self._bump()
            elif action in ('save','confirm'):
                if not self.policy()['long_term_write']:raise ValueError('长期记忆写入已关闭，请先在设置中开启')
                old=None
                if payload.get('id'):
                    old=self.store.db.execute('SELECT * FROM long_term_memories WHERE id=?',(payload['id'],)).fetchone()
                    if old is None:raise ValueError('记忆不存在')
                if action=='confirm' and old is None:raise ValueError('需要待确认的记忆')
                text=payload.get('text',old['text'] if old else '')
                if not isinstance(text,str) or not 1<=len(text.strip())<=1000:raise ValueError('长期记忆需为 1–1000 个字符')
                text=text.strip()
                if PRIVATE.search(text):raise ValueError('请勿将凭据、联系方式或明显敏感标识保存为长期记忆')
                scope=self._scope(payload.get('scope',old['scope'] if old else None))
                key=classify(text)
                if key in (None,'constraint'):key='note:'+hashlib.sha256(text.encode()).hexdigest()[:16]
                self._limit(old is not None)
                now=time.time();entry_id=old['id'] if old else uuid.uuid4().hex
                # A user-confirmed newer preference replaces the same key in the same scope.
                if not key.startswith('note:') and key!='constraint':
                    self.store.db.execute('DELETE FROM long_term_memories WHERE scope=? AND key=? AND id!=?',(scope,key,entry_id))
                self.store.db.execute('INSERT OR REPLACE INTO long_term_memories VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (entry_id,scope,key,text,old['source_task_id'] if old else None,old['source_id'] if old else None,'active',old['created'] if old else now,now,now+self.policy()['retention_days']*86400))
                self._bump()
            else:raise ValueError('无效长期记忆操作')
            self._forget_orphan_sources(old_sources)
        return self.catalog()

    def _limit(self,replacing=False):
        if not replacing and self.store.db.execute('SELECT COUNT(*) FROM long_term_memories').fetchone()[0]>=100:raise ValueError('最多保存 100 条长期记忆，请先清理已有内容')

    def observe_user(self,task_id,text,position=None,origin='task_user',source_token=None):
        policy=self.policy()
        if not ((policy['task_memory'] and policy['auto_constraints']) or policy['long_term_write']):return
        if not isinstance(text,str) or OPT_OUT.search(text) or PRIVATE.search(text):return
        candidates=extract(text)
        if not (policy['task_memory'] and policy['auto_constraints']):
            candidates=[i for i in candidates if DURABLE.search(i['quote']) or re.search(r'请记住[：:]',text)]
        if not candidates:return
        # Keep only eligible verbatim clauses, not an extra copy of personal history.
        selected_text='\n'.join(dict.fromkeys(i['quote'] for i in candidates))
        self.runtime.task_context._require(task_id)
        with self.store.lock,self.store.db:
            if position is None:
                position=self.store.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM memory_user_sources WHERE task_id=?',(task_id,)).fetchone()[0]
            source_id=hashlib.sha256((task_id+'|'+(source_token or uuid.uuid4().hex)).encode()).hexdigest()[:32]
            if self.store.db.execute('SELECT 1 FROM memory_user_sources WHERE id=?',(source_id,)).fetchone():return
            self.store.db.execute('INSERT INTO memory_user_sources VALUES(?,?,?,?,?,?)',(source_id,task_id,position,selected_text,origin,time.time()))
        for item in candidates:
            self._grounded(task_id,source_id,item['quote'],engine='rules-v1')

    def snapshot_sources(self,task_id,context_id):
        policy=self.policy()
        if not (policy['auto_constraints'] or policy['long_term_write']):return
        with self.store.lock:
            row=self.store.db.execute('SELECT data FROM context_snapshots WHERE id=? AND task_id=?',(context_id,task_id)).fetchone()
        if row is None:return
        messages=json.loads(row['data'])['messages']
        for index,message in enumerate(messages):
            if message['role']!='user' or message['status']!='completed':continue
            with self.store.lock:
                original=self.store.db.execute('SELECT created FROM conversation_messages WHERE id=?',(message['id'],)).fetchone()
            # Enabling the feature never scans messages from before consent.
            if original is None or original['created']<policy['auto_since']:continue
            self.observe_user(task_id,message['text'],index-len(messages),'conversation_user',context_id+':'+str(message['id']))

    def _grounded(self,task_id,source_id,quote,engine='model-grounded'):
        policy=self.policy()
        if not ((policy['task_memory'] and policy['auto_constraints']) or policy['long_term_write']):raise ValueError('自动提炼与长期写入均已关闭')
        if not isinstance(source_id,str) or not re.fullmatch('[a-f0-9]{32}',source_id):raise ValueError('无效用户来源编号')
        with self.store.lock:
            source=self.store.db.execute('SELECT * FROM memory_user_sources WHERE task_id=? AND id=?',(task_id,source_id)).fetchone()
        if source is None:raise ValueError('此任务无权使用该用户来源')
        if not isinstance(quote,str) or not 4<=len(quote)<=500 or quote not in source['text'] or PRIVATE.search(quote) or OPT_OUT.search(source['text']):raise ValueError('提炼必须引用当前任务用户来源中的非敏感原文')
        if not any(quote in clause for clause in clauses(source['text'])):raise ValueError('引用来自示例、引文或不适合提炼的内容')
        key=classify(quote)
        if key is None:raise ValueError('没有识别出明确约束，请记录待确认问题')
        effective_key=key if key!='constraint' else 'constraint:'+hashlib.sha256(quote.encode()).hexdigest()[:16]
        result={'key':effective_key,'quote':quote,'source_id':source_id}
        if policy['task_memory'] and policy['auto_constraints']:
            result.update(self._apply_constraint(task_id,source,quote,effective_key,engine))
        if policy['long_term_write'] and (DURABLE.search(quote) or re.search(r'请记住[：:]',source['text'])):
            result['long_term_candidate']=self._candidate(task_id,source_id,quote,effective_key)
        return result

    def _apply_constraint(self,task_id,source,quote,key,engine):
        context=self.runtime.task_context;context.initialize(task_id)
        changed=False
        with self.store.lock,self.store.db:
            state=context._state(task_id)
            rows=[dict(r) for r in self.store.db.execute('SELECT * FROM task_memory WHERE task_id=?',(task_id,))]
            for r in rows:
                origin=json.loads(r['source'])
                if origin.get('source_id')==source['id'] and r['text']==quote:return {'saved':False,'reason':'already_processed'}
            active=[i for i in state['items'] if i['kind']=='constraint' and (i['source'].get('key')==key or (i['authority']=='user' and classify(i['text'])==key))]
            for old in active:
                if old['text']==quote:return {'saved':False,'reason':'already_present'}
                if old['authority']=='user':
                    conflict_id=hashlib.sha256((task_id+old['id']+source['id']+quote).encode()).hexdigest()[:32]
                    self.store.db.execute('INSERT OR IGNORE INTO constraint_conflicts VALUES(?,?,?,?,?,?,?)',(conflict_id,task_id,old['id'],source['id'],key,quote,'pending'))
                    return {'saved':False,'conflict_id':conflict_id}
                if old['source'].get('position',0)>source['position']:return {'saved':False,'reason':'older_source'}
            replace_ids=[i['id'] for i in active if i['authority']=='user_extracted']
            remaining=[i for i in state['items'] if i['id'] not in replace_ids]
            if len(remaining)>=40 or sum(len(i['text']) for i in remaining)+len(quote)>12000:return {'saved':False,'reason':'memory_limit'}
            for old_id in replace_ids:self.store.db.execute('UPDATE task_memory SET active=0 WHERE task_id=? AND id=?',(task_id,old_id))
            origin={'type':'extracted_user','source_id':source['id'],'origin':source['origin'],'key':key,'position':source['position'],'quote':quote,'engine':engine}
            self.store.db.execute('INSERT INTO task_memory VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,task_id,'constraint',quote,'user_extracted',json.dumps(origin,ensure_ascii=False),1,time.time()))
            context._advance(task_id);changed=True
        if changed:self.runtime.emit(task_id,'memory.updated',{'revision':state['revision']+1,'actor':'extraction','engine':engine})
        return {'saved':changed}

    def _candidate(self,task_id,source_id,quote,key):
        run=self.runtime.task_context._require(task_id);scope=run['workspace']
        with self.store.lock,self.store.db:
            existing=self.store.db.execute('SELECT id FROM long_term_memories WHERE scope=? AND text=?',(scope,quote)).fetchone()
            if existing:return {'id':existing['id'],'status':'already_present'}
            try:self._limit()
            except ValueError:return {'status':'limit_reached'}
            now=time.time();entry_id=uuid.uuid4().hex
            self.store.db.execute('INSERT INTO long_term_memories VALUES(?,?,?,?,?,?,?,?,?,?)',(entry_id,scope,key,quote,task_id,source_id,'pending',now,now,now+self.policy()['retention_days']*86400))
            self._bump()
        return {'id':entry_id,'status':'pending_confirmation'}

    def sources(self,task_id):
        if not ((self.policy()['task_memory'] and self.policy()['auto_constraints']) or self.policy()['long_term_write']):return []
        with self.store.lock:
            return [dict(r) for r in self.store.db.execute('SELECT id,position,origin,substr(text,1,1000) AS preview FROM memory_user_sources WHERE task_id=? ORDER BY position DESC LIMIT 12',(task_id,))]

    def conflicts(self,task_id):
        with self.store.lock:return [dict(r) for r in self.store.db.execute("SELECT * FROM constraint_conflicts WHERE task_id=? AND status='pending'",(task_id,))]

    def resolve(self,task_id,payload):
        if not self.policy()['task_memory']:raise ValueError('任务记忆已关闭')
        if not isinstance(payload.get('conflict_id'),str) or not re.fullmatch('[a-f0-9]{32}',payload['conflict_id']):raise ValueError('无效约束冲突编号')
        with self.store.lock,self.store.db:
            conflict=self.store.db.execute("SELECT * FROM constraint_conflicts WHERE task_id=? AND id=? AND status='pending'",(task_id,payload.get('conflict_id'))).fetchone()
            if conflict is None:raise ValueError('冲突已处理或不属于此任务')
            context=self.runtime.task_context;state=context._state(task_id)
            if type(payload.get('revision')) is not int or payload['revision']!=state['revision']:raise ValueError('任务记忆已更新，请刷新后重试')
            existing=next((i for i in state['items'] if i['id']==conflict['existing_id']),None)
            if existing is None:raise ValueError('原约束已修改，请刷新后重新核对')
            if payload.get('choice')=='accept':
                context.update(task_id,{'revision':state['revision'],'id':conflict['existing_id'],'kind':'constraint','text':conflict['quote']})
                resolution='用户已在任务面板确认采用新约束：'+conflict['quote']
            elif payload.get('choice')=='keep':
                resolution='用户已在任务面板确认保留现有约束：'+existing['text']
            else:raise ValueError('请选择采用新约束或保留现有约束')
            # The panel decision is the newest user instruction; an older raw
            # follow-up must not silently override the choice on the next call.
            self.store.db.execute('UPDATE task_context_state SET latest_request=? WHERE task_id=?',(resolution,task_id))
            context._advance(task_id)
            self.store.db.execute('UPDATE constraint_conflicts SET status=? WHERE id=?',(payload['choice'],conflict['id']))
            revision=context._state(task_id)['revision']
        self.runtime.emit(task_id,'memory.updated',{'revision':revision,'actor':'user_resolution'})
        return {'saved':True}

    def retrieve(self,task_id,keys=()):
        if not self.policy()['long_term_read']:return []
        self._expire();run=self.runtime.task_context._require(task_id)
        with self.store.lock:
            rows=[dict(r) for r in self.store.db.execute("SELECT * FROM long_term_memories WHERE status='active' AND scope IN (?,?) AND (source_task_id IS NULL OR source_task_id!=?) ORDER BY CASE WHEN scope='global' THEN 1 ELSE 0 END,updated DESC",(run['workspace'],'global',task_id))]
        selected=[];seen=set(keys);chars=0
        for item in rows:
            if item['key'] in seen:continue
            if len(selected)>=5 or chars+len(item['text'])>3000:continue
            seen.add(item['key']);chars+=len(item['text'])
            selected.append({k:item[k] for k in ('id','scope','key','text','source_task_id','updated')})
        return selected
