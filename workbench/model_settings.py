"""Workbench-scoped Pi provider settings. Secrets never enter public API/SQLite."""
import json
import os
import re
import tempfile
from pathlib import Path
from .model_discovery import APIS,validate_endpoint,validate_key,fetch_models

ID=re.compile(r'^[a-z][a-z0-9-]{1,39}$')

class ModelSettings:
    def __init__(self,runtime):self.runtime=runtime;self.store=runtime.store;self.path=self.store.path.parent/'pi-api.json'
    def read(self):
        if not self.path.exists():return {'revision':0,'providers':[],'default_model':''}
        return json.loads(self.path.read_text())
    def public(self):
        with self.store.lock:data=self.read()
        return {**data,'providers':[{**{k:v for k,v in p.items() if k!='api_key'},'has_key':bool(p.get('api_key'))} for p in data['providers']]}
    def choices(self):
        return [{'value':'wbapi-'+p['id']+'/'+m,'label':p['name']+' · '+m} for p in self.read()['providers'] for m in p['models']]
    def default(self):return self.read().get('default_model','')
    def _write(self,data):
        path=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.path.parent,prefix='.pi-api-',delete=False) as f:
                path=Path(f.name);json.dump(data,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
            path.chmod(0o600);os.replace(path,self.path)
        finally:
            if path:path.unlink(missing_ok=True)
    def discover(self,request):
        base=validate_endpoint(request.get('base_url'),request.get('api'));api=request['api']
        key=validate_key(request.get('api_key',''))
        if not key:
            with self.store.lock:
                data=self.read()
                if type(request.get('revision')) is not int or request['revision']!=data['revision']:raise ValueError('设置已更新，请刷新后再获取模型')
                old=next((p for p in data['providers'] if p['id']==request.get('id')),None)
                if not old:raise ValueError('请填写 API Key；无认证的本地服务可填写 local')
                if old['base_url']!=base or old['api']!=api:raise ValueError('地址或协议已变更，请重新填写密钥后获取，避免把原密钥发送到其他服务')
                key=old['api_key']
        return fetch_models(base,api,key)

    def save(self,request):
        r=self.runtime
        with r.lock,self.store.lock:
            if r.active or r.conversations.active:raise ValueError('有任务或对话正在运行，请结束后再修改 API 设置')
            data=self.read()
            if type(request.get('revision')) is not int or request.get('revision')!=data['revision']:raise ValueError('设置已更新，请刷新后重试')
            action=request.get('action');provider_id=request.get('id')
            if action=='upsert':
                if not isinstance(provider_id,str) or not ID.fullmatch(provider_id):raise ValueError('连接标识需要2–40位小写字母、数字或连字符')
                name=request.get('name','');base=request.get('base_url','');api=request.get('api');models=request.get('models');key=request.get('api_key','')
                if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('连接名称需要1–80字符')
                base=validate_endpoint(base,api)
                if not isinstance(models,list) or not 1<=len(models)<=50 or any(not isinstance(m,str) or not m or len(m)>150 or any(c.isspace() for c in m) or m.startswith('-') for m in models):raise ValueError('需要1–50个有效模型ID，每项最多150字符')
                validate_key(key)
                for field,lower,upper in [('context_window',1024,2000000),('max_tokens',128,200000)]:
                    value=request.get(field)
                    if type(value) is not int or not lower<=value<=upper:raise ValueError(field+' 数值无效')
                if request['max_tokens']>request['context_window']:raise ValueError('输出上限不能超过上下文窗口')
                if type(request.get('reasoning',False)) is not bool or type(request.get('vision',False)) is not bool:raise ValueError('能力开关必须为布尔值')
                old=next((p for p in data['providers'] if p['id']==provider_id),{})
                if not old and len(data['providers'])>=32:raise ValueError('最多配置32个API连接')
                if old and not key and (old['base_url']!=base or old['api']!=api):raise ValueError('地址或协议已变更，请重新填写密钥后保存')
                saved_key=key or old.get('api_key','')
                if not saved_key:raise ValueError('请填写API Key；无认证的本地服务可填写占位值 local')
                item={'id':provider_id,'name':name.strip(),'base_url':base.rstrip('/'),'api':api,'models':list(dict.fromkeys(models)),'api_key':saved_key,'context_window':request['context_window'],'max_tokens':request['max_tokens'],'reasoning':request.get('reasoning',False),'vision':request.get('vision',False)}
                removed=set(old.get('models',[]))-set(item['models'])
                self._references(data,provider_id,removed)
                data['providers']=[p for p in data['providers'] if p['id']!=provider_id]+[item]
                if key:r.config.secrets.append(key)
            elif action=='delete':
                old=next((p for p in data['providers'] if p['id']==provider_id),None)
                if not old:raise ValueError('连接不存在')
                self._references(data,provider_id,set(old['models']))
                data['providers']=[p for p in data['providers'] if p['id']!=provider_id]
            elif action=='default':
                value=request.get('model','')
                if not isinstance(value,str) or len(value)>200 or value.startswith('-'):raise ValueError('模型标识无效')
                self.validate_model(value,data);data['default_model']=value.strip()
            else:raise ValueError('不支持的设置操作')
            data['revision']+=1;self._write(data)
            return self.public()
    def _references(self,data,provider_id,removed):
        values={'wbapi-'+provider_id+'/'+m for m in removed}
        if data.get('default_model') in values:raise ValueError('请先更换默认模型，再移除此模型或连接')
        if any(a.get('model') in values for a in self.runtime.agents.list()):raise ValueError('有 Agent 正在使用此模型，请先调整 Agent 的模型选择')
        # Historical tasks/chats keep their model identities; deletion must be explicit and reversible by re-adding.
    def validate_model(self,value,data=None):
        if not isinstance(value,str):raise ValueError('模型标识无效')
        value=value.strip()
        if value.startswith('wbapi-'):
            available={'wbapi-'+p['id']+'/'+m for p in (data or self.read())['providers'] for m in p['models']}
            if value not in available:raise ValueError('所选工作台模型不存在，请先在设置中添加连接和模型')
