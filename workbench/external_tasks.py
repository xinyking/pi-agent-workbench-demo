"""Local task import and ordered progress reports from external executors."""
import hashlib
import json


class ExternalTasks:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.execute('''CREATE TABLE IF NOT EXISTS external_tasks(
                source TEXT, external_id TEXT, run_id TEXT UNIQUE,
                sequence INTEGER DEFAULT 0, digest TEXT DEFAULT '',
                PRIMARY KEY(source,external_id))''')

    @staticmethod
    def text(data, key, limit, required=True):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > limit or required and not value.strip():
            raise ValueError('无效字段：' + key)
        return value.strip()

    def create(self, data):
        source = self.text(data, 'source', 100)
        external_id = self.text(data, 'external_id', 200)
        prompt = self.text(data, 'prompt', 8000)
        with self.store.lock:
            existing = self.store.db.execute('SELECT run_id FROM external_tasks WHERE source=? AND external_id=?', (source, external_id)).fetchone()
            if existing:
                result = self.get(existing['run_id'])
                if result['run']['prompt'] != prompt: raise ValueError('同一外部任务标识的任务要求不一致')
                return result
            run_id = self.store.create(prompt, provider_id='external', model=source)
            with self.store.db:
                self.store.db.execute('INSERT INTO external_tasks(source,external_id,run_id) VALUES(?,?,?)', (source, external_id, run_id))
            self.store.emit(run_id, 'message.user', {'agent_id': 'main', 'text': prompt})
            return self.get(run_id)

    def get(self, run_id):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM external_tasks WHERE run_id=?', (run_id,)).fetchone()
            if row is None: raise ValueError('外部任务不存在')
            return {'id': run_id, 'source': row['source'], 'external_id': row['external_id'],
                    'sequence': row['sequence'], 'run': self.store.run(run_id)}

    def report(self, run_id, data):
        sequence = data.get('sequence')
        if type(sequence) is not int or sequence < 1: raise ValueError('sequence 需要正整数')
        status = data.get('status')
        if status not in {'running', 'completed', 'failed', 'cancelled', 'blocked'}: raise ValueError('无效外部任务状态')
        step = self.text(data, 'step', 500)
        result = self.text(data, 'result', 16000, False)
        if status in {'completed', 'failed', 'blocked'} and not result: raise ValueError('结束或阻塞状态需要结果或原因')
        tool = data.get('tool')
        if tool is not None and (not isinstance(tool, str) or not tool.strip() or len(tool) > 100): raise ValueError('无效工具名称')
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.store.lock:
            current = self.get(run_id)
            row = self.store.db.execute('SELECT digest FROM external_tasks WHERE run_id=?', (run_id,)).fetchone()
            if sequence == current['sequence'] and digest == row['digest']: return {**current, 'duplicate': True}
            if sequence != current['sequence'] + 1: raise ValueError('报告序号冲突；先读取当前 sequence，再提交下一条')
            if current['run']['status'] in {'completed', 'failed', 'cancelled'}: raise ValueError('外部任务已结束，请新建外部任务标识')
            event = {'agent_id': 'main', 'attempt_id': 'external', 'source': current['source'], 'step': step, 'text': result or step}
            self.store.emit(run_id, 'external.progress', event)
            if tool:
                call = {**event, 'id': 'external-' + str(sequence), 'name': tool, 'arguments': {'step': step}}
                self.store.emit(run_id, 'tool.started', call)
                self.store.emit(run_id, 'tool.completed', {**call, 'is_error': status == 'failed', 'result': {'content': [{'type': 'text', 'text': result or step}]}})
            if result:
                self.store.emit(run_id, 'message.started', event)
                self.store.emit(run_id, 'message.completed', event)
            # Reuse the board's current_step projection; it is external evidence, not a Pi plan.
            self.store.emit(run_id, 'plan.updated', {'steps': [{'id': 'external', 'title': step, 'status': 'completed' if status == 'completed' else 'running'}]})
            self.store.emit(run_id, 'status', {'status': 'awaiting_confirmation' if status == 'blocked' else status,
                                               'reason': result if status in {'failed', 'blocked'} else ''})
            if status == 'completed': self.store.emit(run_id, 'task.completed', {'summary': result, 'source': current['source']})
            with self.store.db:
                self.store.db.execute('UPDATE external_tasks SET sequence=?,digest=? WHERE run_id=?', (sequence, digest, run_id))
            return {**self.get(run_id), 'duplicate': False}
