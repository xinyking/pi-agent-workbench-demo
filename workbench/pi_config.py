"""Pi runtime paths and redaction; managed API settings are workbench-scoped."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from .agent_registry import AgentRegistry


class PiConfig:
    def __init__(self, store):
        self.store = store
        self.directory = store.path.parent
        self.directory.chmod(0o700)
        # Read old credentials only to redact them from historical diagnostics.
        path = self.directory / 'credentials.json'
        self.secrets = list(json.loads(path.read_text()).values()) if path.exists() else []
        managed=self.directory/'pi-api.json'
        if managed.exists():self.secrets.extend(p.get('api_key','') for p in json.loads(managed.read_text()).get('providers',[]))
        self.binary = shutil.which(os.environ.get('WORKBENCH_PI', 'pi'))
        self.version = None
        if self.binary:
            try:
                result = subprocess.run([self.binary, '--version'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0: self.version = result.stdout.strip()[:80]
            except (OSError, subprocess.TimeoutExpired):
                pass

    def public(self):
        return {'workspaces': self.store.setting('workspaces', []),
                'shell_mode': self.store.setting('shell_mode', 'disabled'),
                'pi': {'available': bool(self.binary and self.version), 'binary': self.binary, 'version': self.version},
                'roles': [a['name'] for a in AgentRegistry(self.store).list(enabled_only=True)]}

    def require_pi(self):
        if not self.binary or not self.version:
            raise ValueError('未找到可运行的 Pi。请先在 WSL 安装并配置 Pi，或设置 WORKBENCH_PI 指向可执行文件')
        return self.binary

    def add_workspace(self, raw):
        if not isinstance(raw, str) or not raw.strip(): raise ValueError('请输入 WSL 工作区绝对路径')
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_dir(): raise ValueError('工作区必须是已存在的 WSL 项目绝对目录')
        path = path.resolve()
        if path in (Path('/'), Path.home()): raise ValueError('请选择具体项目目录')
        if path == self.directory or self.directory in path.parents: raise ValueError('数据目录不能作为工作区')
        with self.store.lock:
            spaces = self.store.setting('workspaces', [])
            if str(path) not in spaces: self.store.set_setting('workspaces', spaces + [str(path)])
        return str(path)

    def workspace(self, value):
        if value not in self.store.setting('workspaces', []) or not Path(value).is_dir(): raise ValueError('请先添加并选择有效工作区')
        return Path(value).resolve()

    def set_shell(self, mode):
        if mode not in ('disabled', 'approval'): raise ValueError('无效 Shell 模式')
        self.store.set_setting('shell_mode', mode)

    def redact(self, value):
        for secret in self.secrets:
            if isinstance(secret, str) and secret: value = value.replace(secret, '[REDACTED]')
        return value
