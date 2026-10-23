"""SQLite is the source of truth for tasks, messages, approvals and configuration."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

TERMINAL = {'completed', 'cancelled', 'failed', 'interrupted'}


class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, prompt TEXT, status TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, kind TEXT, data TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS events_run_id ON events(run_id, id);
            CREATE TABLE IF NOT EXISTS agents(run_id TEXT, id TEXT, parent_id TEXT, role TEXT, task TEXT, status TEXT, data TEXT, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY, run_id TEXT, agent_id TEXT, data TEXT, status TEXT, answer TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, run_id TEXT, data TEXT, status TEXT, created REAL);
            ''')
            columns = {r['name'] for r in self.db.execute('PRAGMA table_info(runs)')}
            for name in ('workspace', 'provider_id', 'model', 'summary', 'reason', 'plan'):
                if name not in columns:
                    self.db.execute(f'ALTER TABLE runs ADD COLUMN {name} TEXT')
            interrupted = self.db.execute("SELECT id FROM runs WHERE status IN ('running','stopping','waiting_approval')").fetchall()
            for row in interrupted:
                self.emit(row['id'], 'status', {'status': 'interrupted', 'reason': '后台重启，等待继续任务'})
            self.db.execute("UPDATE agents SET status='interrupted' WHERE status IN ('running','waiting_approval')")
            for row in self.db.execute("SELECT id FROM runs WHERE status='failed' AND reason='Pi 尚未明确报告任务完成，请查看执行记录后继续任务'").fetchall():
                self.emit(row['id'],'status',{'status':'interrupted','reason':'待核对并收尾：Agent 未提交完成标记，已有输出与文件记录已保留'})
            for row in self.db.execute("SELECT id FROM runs WHERE status='awaiting_confirmation'").fetchall():
                self.emit(row['id'], 'status', {'status': 'interrupted', 'reason': '旧版运行未明确报告任务完成，请继续执行并核对缺失信息'})
            for row in self.db.execute("SELECT DISTINCT run_id FROM inputs WHERE status='pending'").fetchall():
                self.emit(row['run_id'], 'status', {'status': 'waiting_input', 'reason': '等待补充信息；后台重启后请求仍保留'})
            self.db.execute("UPDATE approvals SET status='expired' WHERE status='pending'")
        self.path.chmod(0o600)

    def create(self, prompt, workspace='', provider_id='', model=''):
        run_id = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO runs(id,prompt,status,created,workspace,provider_id,model) VALUES(?,?,?,?,?,?,?)',
                            (run_id, prompt, 'queued', time.time(), workspace, provider_id, model))
        self.emit(run_id, 'status', {'status': 'queued'})
        return run_id

    def emit(self, run_id, kind, data):
        with self.lock, self.db:
            if kind == 'agent.started':
                self.db.execute('INSERT OR REPLACE INTO agents VALUES(?,?,?,?,?,?,?)',
                    (run_id, data['agent_id'], data.get('parent_agent_id'), data['role'], data['task'], 'running', json.dumps(data, ensure_ascii=False)))
            elif kind in ('agent.status', 'agent.completed'):
                self.db.execute('UPDATE agents SET status=? WHERE run_id=? AND id=?', (data['status'], run_id, data['agent_id']))
            if kind == 'status':
                self.db.execute('UPDATE runs SET status=?,reason=? WHERE id=?', (data['status'], data.get('reason', ''), run_id))
            elif kind == 'model.started':
                self.db.execute('UPDATE runs SET summary=? WHERE id=?', ('模型正在处理 · ' + data['model'], run_id))
            elif kind == 'tool.started':
                self.db.execute('UPDATE runs SET summary=? WHERE id=?', ('调用工具：' + data['name'], run_id))
            elif kind == 'task.completed':
                self.db.execute('UPDATE runs SET summary=? WHERE id=?', (data['summary'], run_id))
            elif kind == 'plan.updated':
                self.db.execute('UPDATE runs SET plan=? WHERE id=?', (json.dumps(data['steps'], ensure_ascii=False), run_id))
            elif kind == 'error':
                self.db.execute('UPDATE runs SET reason=? WHERE id=?', (data['text'], run_id))
            self.db.execute('INSERT INTO events(run_id,kind,data,created) VALUES(?,?,?,?)',
                            (run_id, kind, json.dumps(data, ensure_ascii=False), time.time()))

    def run(self, run_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise ValueError('任务不存在')
            return dict(row)

    def runs(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM runs ORDER BY created DESC')]

    def events(self, run_id, after=0, limit=1000):
        with self.lock:
            rows = self.db.execute('SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?', (run_id, after, limit)).fetchall()
        return [{**dict(r), 'data': json.loads(r['data'])} for r in rows]

    def message(self, run_id, data):
        with self.lock, self.db:
            self.db.execute('INSERT INTO messages(run_id,data) VALUES(?,?)', (run_id, json.dumps(data, ensure_ascii=False)))

    def messages(self, run_id):
        with self.lock:
            return [json.loads(r['data']) for r in self.db.execute('SELECT data FROM messages WHERE run_id=? ORDER BY id', (run_id,))]

    def setting(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT data FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row['data']) if row else default

    def set_setting(self, key, data):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(data, ensure_ascii=False)))

    def approval(self, run_id, data):
        approval_id = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO approvals VALUES(?,?,?,?,?)', (approval_id, run_id, json.dumps(data, ensure_ascii=False), 'pending', time.time()))
        return approval_id

    def approvals(self, run_id=None):
        with self.lock:
            rows = self.db.execute('SELECT * FROM approvals' + (' WHERE run_id=?' if run_id else '') + ' ORDER BY created', (run_id,) if run_id else ()).fetchall()
        return [{**dict(r), 'data': json.loads(r['data'])} for r in rows]

    def resolve_approval(self, approval_id, status):
        with self.lock, self.db:
            result = self.db.execute("UPDATE approvals SET status=? WHERE id=? AND status='pending'", (status, approval_id))
            if result.rowcount != 1:
                raise ValueError('审批已处理或已失效')

    def usage(self):
        with self.lock:
            rows = self.db.execute("SELECT data FROM events WHERE kind='usage'").fetchall()
        result = {'requests': len(rows), 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
        for row in rows:
            data = json.loads(row['data'])
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                result[key] += data.get(key, 0) or 0
        return result

    def cursor(self):
        with self.lock:
            return self.db.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0]

    def board_metadata(self):
        """Derive timestamps and distinct changed file counts from real events."""
        with self.lock:
            rows = self.db.execute("""
                SELECT run_id, MAX(created) AS updated,
                       COUNT(DISTINCT CASE WHEN kind='file.changed'
                             THEN json_extract(data, '$.path') END) AS file_change_count
                FROM events GROUP BY run_id
            """).fetchall()
        result = {row['run_id']: {'updated': row['updated'], 'file_change_count': row['file_change_count']} for row in rows}
        with self.lock:
            counts = self.db.execute("""
                SELECT child.run_id, COUNT(*) AS total,
                       SUM(CASE WHEN child.status='completed' THEN 1 ELSE 0 END) AS done
                FROM agents child JOIN agents root ON root.run_id=child.run_id AND root.id='main'
                WHERE child.parent_id IS NOT NULL
                  AND json_extract(child.data,'$.attempt_id')=json_extract(root.data,'$.attempt_id')
                GROUP BY child.run_id
            """).fetchall()
        for row in counts:
            result.setdefault(row['run_id'], {}).update(agent_count=row['total'], agents_completed=row['done'])
        return result

    def agents(self, run_id):
        with self.lock:
            rows = self.db.execute('SELECT * FROM agents WHERE run_id=? ORDER BY rowid', (run_id,)).fetchall()
        return [{**dict(row), 'data': json.loads(row['data'])} for row in rows]

    def input_request(self, run_id, agent_id, data):
        request_id = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO inputs VALUES(?,?,?,?,?,?,?)', (request_id,run_id,agent_id,json.dumps(data,ensure_ascii=False),'pending',None,time.time()))
        return request_id

    def inputs(self, run_id):
        with self.lock:
            rows = self.db.execute('SELECT * FROM inputs WHERE run_id=? ORDER BY created', (run_id,)).fetchall()
        return [{**dict(row), 'data': json.loads(row['data'])} for row in rows]

    def answer_input(self, run_id, request_id, answer):
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM inputs WHERE id=? AND run_id=? AND status='pending'", (request_id,run_id)).fetchone()
            if row is None: raise ValueError('此信息请求已处理或不存在')
            self.db.execute("UPDATE inputs SET status='answered',answer=? WHERE id=?", (answer,request_id))
            return {**dict(row), 'data': json.loads(row['data'])}

    def cancel_inputs(self, run_id):
        with self.lock, self.db:
            self.db.execute("UPDATE inputs SET status='cancelled' WHERE run_id=? AND status='pending'", (run_id,))
