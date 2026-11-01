"""Read saved retrospective reviews without changing task execution state."""
import json


def task_review(store, run_id):
    with store.lock:
        row = store.db.execute("SELECT id,data FROM events WHERE run_id=? AND kind='evaluation.completed' ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        if row is None:
            return None
        latest = store.db.execute("SELECT MAX(id) FROM events WHERE run_id=? AND kind!='evaluation.completed'", (run_id,)).fetchone()[0] or 0
    review = json.loads(row['data'])
    return {**review, 'stale': latest != review.get('evidence_last_id')}


def evaluation_events(store, run_id):
    """Complete numeric evidence, excluding prompts, replies and arbitrary tool output."""
    kinds = ('model.started', 'message.started', 'pi.turn_start', 'tool.started',
             'tool.completed', 'usage', 'error', 'approval.resolved', 'task.reported',
             'executor.result', 'output.structured', 'task.confirmed')
    with store.lock:
        rows = store.db.execute(
            'SELECT id,kind,data,created FROM events WHERE run_id=? AND kind IN ('
            + ','.join('?' for _ in kinds) + ') ORDER BY id', (run_id, *kinds)).fetchall()
    events = []
    fields = ('agent_id', 'attempt_id', 'id', 'is_error', 'decision', 'prompt_tokens',
              'completion_tokens', 'cache_read_tokens', 'cache_write_tokens',
              'total_tokens', 'cost_usd')
    guards = ('工作台拒绝越界或受保护的路径', '工作台尚未启用逐次审批 Shell')
    for row in rows:
        source = json.loads(row['data'])
        data = {key: source[key] for key in fields if key in source}
        if row['kind'] == 'tool.completed':
            result = source.get('result')
            content = result.get('content', []) if isinstance(result, dict) else []
            if not isinstance(content, list):
                content = []
            data['result'] = {'content': [
                {'type': 'text', 'text': prefix}
                for item in content if isinstance(item, dict) and item.get('type') == 'text'
                for prefix in guards if str(item.get('text', '')).startswith(prefix)]}
        events.append({'id': row['id'], 'kind': row['kind'], 'created': row['created'], 'data': data})
    return events
