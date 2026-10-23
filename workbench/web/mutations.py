import json
import re
import time
from urllib.parse import urlparse, parse_qs, quote
from .common import ID, STATIC, STREAM_TERMINAL

class MutationRoutes:

    def do_POST(self):
        if not self.valid_host():
            return self.reply({'error': '只允许本机访问'}, 403)
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.reply({'error': '来源不允许'}, 403)
        if self.headers.get_content_type() != 'application/json':
            return self.reply({'error': '需要 application/json'}, 415)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            upload_route = re.fullmatch('/api/conversations/[a-f0-9]{32}/documents', self.path)
            limit = 15 * 1024 * 1024 if upload_route else 256 * 1024
            if not 0 < length <= limit:
                raise ValueError('请求大小无效')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('需要 JSON 对象')
            if self.path == '/api/conversations':
                return self.reply(self.runtime.conversations.create(data.get('mode'), data.get('workspace') or '', data.get('model') or ''), 201)
            chat_parts = self.path.strip('/').split('/')
            if len(chat_parts) == 6 and chat_parts[:2] == ['api', 'conversations'] and ID.fullmatch(chat_parts[2]) and (chat_parts[3] == 'documents'):
                self.runtime.conversations.get(chat_parts[2])
                if chat_parts[5] == 'retry':
                    self.runtime.documents.retry(chat_parts[2], chat_parts[4])
                    return self.reply({'saved': True})
                if chat_parts[5] == 'archive':
                    self.runtime.documents.archive(chat_parts[2], chat_parts[4])
                    return self.reply({'saved': True})
            if len(chat_parts) == 4 and chat_parts[:2] == ['api', 'conversations'] and ID.fullmatch(chat_parts[2]):
                if chat_parts[3] == 'documents':
                    self.runtime.conversations.get(chat_parts[2])
                    return self.reply({'document': self.runtime.documents.upload(chat_parts[2], data.get('name'), data.get('content_base64'))}, 201)
                if chat_parts[3] == 'mode':
                    return self.reply(self.runtime.conversations.set_mode(chat_parts[2], data.get('mode')))
                if chat_parts[3] == 'rename':
                    return self.reply(self.runtime.conversations.rename(chat_parts[2], data.get('title')))
                if chat_parts[3] == 'delete':
                    return self.reply(self.runtime.conversations.delete(chat_parts[2]))
                if chat_parts[3] == 'messages':
                    return self.reply(self.runtime.conversations.send(chat_parts[2], self.prompt(data), data.get('workspace')))
                if chat_parts[3] == 'cancel':
                    self.runtime.conversations.cancel(chat_parts[2])
                    return self.reply({'stopping': True})
            if self.path == '/api/context/preview':
                return self.reply(self.runtime.sources.preview(self.prompt(data),data.get('source_selection'),data.get('provider_id') or 'pi',data.get('model') or ''))
            if self.path == '/api/settings/models/discover':
                return self.reply(self.runtime.models.discover(data))
            if self.path == '/api/settings/models':
                return self.reply(self.runtime.models.save(data))
            if self.path == '/api/agents/model':
                self.runtime.models.validate_model(data.get('model',''))
                self.runtime.agents.set_model(data.get('name'),data.get('model',''))
                return self.reply({'saved':True})
            if self.path == '/api/agents':
                definition=self.runtime.agents.parse(data['markdown']) if 'markdown' in data else data
                self.runtime.models.validate_model(definition.get('model',''))
                return self.reply({'agent': self.runtime.agents.save(data)})
            if self.path == '/api/agents/toggle':
                self.runtime.agents.toggle(data.get('name'), data.get('enabled'))
                return self.reply({'saved': True})
            if self.path == '/api/agents/delete':
                self.runtime.agents.remove(data.get('name'))
                return self.reply({'saved': True})
            if self.path == '/api/providers':
                return self.reply({'error': '请使用设置中的 API 连接配置，接口为 /api/settings/models'}, 400)
            if self.path == '/api/workspaces':
                return self.reply({'path': self.runtime.config.add_workspace(data.get('path'))})
            if self.path == '/api/settings/memory':
                return self.reply(self.runtime.memory.configure(data))
            if self.path == '/api/memory':
                return self.reply(self.runtime.memory.manage(data))
            if self.path == '/api/settings':
                self.runtime.config.set_shell(data.get('shell_mode'))
                return self.reply({'saved': True})
            if self.path == '/api/external/tasks':
                return self.reply(self.external.create(data), 201)
            external_report = re.fullmatch('/api/external/tasks/([a-f0-9]{32})/reports', self.path)
            if external_report:
                return self.reply(self.external.report(external_report[1], data))
            if self.path == '/api/runs':
                prompt = self.prompt(data)
                run_id = self.runtime.create(prompt, data.get('workspace'), data.get('provider_id'), data.get('model') or '', executor_options=data.get('executor_options'),source_selection=data.get('source_selection'),preview_digest=data.get('preview_digest'))
                return self.reply({'id': run_id}, 201)
            parts = self.path.strip('/').split('/')
            if len(parts) == 4 and parts[:2] == ['api', 'runs'] and ID.fullmatch(parts[2]):
                run_id, action = parts[2:]
                if action == 'context':
                    if self.store.run(run_id)['provider_id'] in ('codex','claude'):raise ValueError('外部执行上下文为只读快照；请通过追加要求继续')
                    if data.get('action')=='resolve_conflict':return self.reply(self.runtime.memory.resolve(run_id,data))
                    return self.reply(self.runtime.task_context.update(run_id, data))
                if action == 'start':
                    return self.reply({'id': self.runtime.start(run_id)})
                if action == 'archive':
                    return self.reply(self.runtime.archive_completed(run_id))
                if action == 'followup':
                    return self.reply({'id': self.runtime.start(run_id, self.prompt(data))})
                if action == 'cancel':
                    return self.reply({'stopping': self.runtime.cancel(run_id)})
                if action == 'confirm':
                    self.runtime.confirm(run_id)
                    return self.reply({'confirmed': True})
                if action == 'answer':
                    return self.reply(self.runtime.answer(run_id, data.get('id'), data.get('answer')))
                if action == 'approval':
                    if not isinstance(data.get('allowed'), bool):
                        raise ValueError('审批决定必须为布尔值')
                    self.runtime.decide(run_id, data.get('id'), data['allowed'])
                    return self.reply({'saved': True})
            return self.reply({'error': '不存在'}, 404)
        except (ValueError, TypeError, AttributeError, OSError) as exc:
            return self.reply({'error': self.runtime.config.redact(str(exc))}, 400)

    @staticmethod
    def prompt(data):
        prompt = data.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
            raise ValueError('任务需包含 1–8000 个字符')
        return prompt.strip()
