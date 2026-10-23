"""Validated Pi Markdown agents, with immutable per-execution prompt snapshots."""
import hashlib
import json
import re
import os
import tempfile
from pathlib import Path

BUILTINS = Path(__file__).resolve().parent.parent / '.pi' / 'agents'
TOOLS = ('read', 'grep', 'find', 'ls', 'write', 'edit', 'bash')
NAME = re.compile(r'^[a-z][a-z0-9-]{1,47}$')


class AgentRegistry:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def validate(data):
        if not isinstance(data, dict): raise ValueError('Agent 必须是对象')
        name = data.get('name', '')
        if not isinstance(name, str) or not NAME.fullmatch(name): raise ValueError('Agent 名称使用 2–48 位小写字母、数字、连字符，以字母开头')
        result = {'name': name}
        for key, limit in [('description', 500), ('prompt', 16000)]:
            value = data.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > limit: raise ValueError(f'{key} 不能为空，最多 {limit} 字符')
            result[key] = value.strip()
        tools = data.get('tools', ['read', 'grep', 'find', 'ls'])
        if not isinstance(tools, list) or not tools or any(not isinstance(t, str) or t not in TOOLS for t in tools): raise ValueError('请选择支持的 Pi 工具；不允许子 Agent 递归委派')
        result['tools'] = list(dict.fromkeys(tools))
        model = data.get('model', '')
        if not isinstance(model, str) or len(model) > 200 or model.startswith('-'): raise ValueError('无效模型标识')
        result['model'] = model.strip()
        return result

    @classmethod
    def parse(cls, markdown):
        if not isinstance(markdown, str) or len(markdown) > 20000: raise ValueError('Agent Markdown 最多 20000 字符')
        match = re.match(r'\A---\s*\n(.*?)\n---\s*\n(.*)\Z', markdown.strip(), re.S)
        if not match: raise ValueError('需要带 name、description、tools 前置信息的 Pi Agent Markdown')
        fields = {}
        for line in match[1].splitlines():
            if not line.strip() or line.lstrip().startswith('#'): continue
            key, separator, value = line.partition(':')
            if not separator: raise ValueError('Agent 前置信息仅支持单行 key: value')
            value = value.strip()
            if value.startswith('"'):
                try: value = json.loads(value)
                except ValueError: raise ValueError('无效前置信息引号') from None
            elif value.startswith("'") and value.endswith("'"): value = value[1:-1]
            fields[key.strip()] = value
        raw_tools = fields.get('tools', 'read, grep, find, ls')
        if not isinstance(raw_tools, str): raise ValueError('tools 使用逗号分隔的工具名')
        fields['tools'] = [t.strip() for t in raw_tools.split(',') if t.strip()]
        fields['prompt'] = match[2]
        return cls.validate(fields)

    @staticmethod
    def markdown(agent):
        head = ['---', 'name: ' + agent['name'], 'description: ' + json.dumps(agent['description'], ensure_ascii=False), 'tools: ' + ', '.join(agent['tools'])]
        if agent.get('model'): head.append('model: ' + json.dumps(agent['model'], ensure_ascii=False))
        return '\n'.join(head + ['---', '', agent['prompt'], ''])

    def list(self, enabled_only=False):
        custom = self.store.setting('custom_agents', {})
        disabled = set(self.store.setting('disabled_agents', []))
        agents = []
        for path in sorted(BUILTINS.glob('*.md')):
            agent = self.parse(path.read_text())
            agents.append({**agent, 'source': 'builtin', 'enabled': agent['name'] not in disabled})
        agents.extend({**self.validate(data), 'source': 'custom', 'enabled': name not in disabled} for name, data in custom.items())
        overrides=self.store.setting('agent_models',{})
        for agent in agents:
            if agent['name'] in overrides:agent['model']=overrides[agent['name']]
        return [agent for agent in agents if agent['enabled'] or not enabled_only]

    def save(self, data):
        agent = self.parse(data['markdown']) if 'markdown' in data else self.validate(data)
        with self.store.lock:
            if any(a['name'] == agent['name'] and a['source'] == 'builtin' for a in self.list()): raise ValueError('内置 Agent 请复制后使用新的名称')
            custom = self.store.setting('custom_agents', {})
            overrides=self.store.setting('agent_models',{});overrides.pop(agent['name'],None);self.store.set_setting('agent_models',overrides)
            custom[agent['name']] = agent
            self.store.set_setting('custom_agents', custom)
        return agent

    def set_model(self,name,model):
        if not isinstance(model,str) or len(model)>200 or model.startswith('-'):raise ValueError('模型标识无效')
        with self.store.lock:
            if not any(a['name']==name for a in self.list()):raise ValueError('Agent不存在')
            overrides=self.store.setting('agent_models',{});overrides[name]=model.strip();self.store.set_setting('agent_models',overrides)

    def toggle(self, name, enabled):
        if not isinstance(enabled, bool): raise ValueError('启用状态必须为布尔值')
        with self.store.lock:
            if not any(a['name'] == name for a in self.list()): raise ValueError('Agent 不存在')
            disabled = set(self.store.setting('disabled_agents', []))
            if enabled: disabled.discard(name)
            else: disabled.add(name)
            self.store.set_setting('disabled_agents', sorted(disabled))

    def remove(self, name):
        with self.store.lock:
            custom = self.store.setting('custom_agents', {})
            if name not in custom: raise ValueError('只能删除自定义 Agent')
            del custom[name]
            overrides=self.store.setting('agent_models',{});overrides.pop(name,None);self.store.set_setting('agent_models',overrides)
            self.store.set_setting('custom_agents', custom)

    def snapshot(self, agent):
        text = self.markdown(agent) + '\n缺少用户信息时必须调用 workbench_ask_user 等待回答，不得虚构。只完成受派子任务并向主 Agent 交付，不能替主 Agent 宣布整个任务完成。\n'
        digest = hashlib.sha256(text.encode()).hexdigest()[:20]
        directory = self.store.path.parent / 'agent-snapshots'
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (agent['name'] + '-' + digest + '.md')
        if not path.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, prefix='.role-', delete=False) as output:
                    temporary = Path(output.name)
                    output.write(text)
                # Readers can only observe a complete snapshot, including concurrent starts.
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return path
