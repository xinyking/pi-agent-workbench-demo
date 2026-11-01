#!/usr/bin/env python3
"""Append explicitly authored task reviews; never execute tasks or change their status."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def evidence_hash(db, run_id):
    run = db.execute('SELECT id,prompt,status,summary,reason,plan FROM runs WHERE id=?', (run_id,)).fetchone()
    if run is None:
        raise ValueError('任务不存在')
    events = db.execute("SELECT id,kind,data,created FROM events WHERE run_id=? AND kind!='evaluation.completed' ORDER BY id", (run_id,)).fetchall()
    payload = json.dumps([list(run), [list(row) for row in events]], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest(), events[-1][0] if events else 0


def apply_reviews(database, records):
    db = sqlite3.connect(database, timeout=10)
    try:
        # Validate everything before backing up or appending; freeze evidence during writes.
        db.execute('BEGIN IMMEDIATE')
        pending = []
        for record in records:
            if record['verdict'] not in ('passed', 'partial', 'incomplete', 'failed') or not record['checks']:
                raise ValueError('无效评测')
            digest, cursor = evidence_hash(db, record['run_id'])
            if digest != record['evidence_sha256'] or cursor != record['evidence_last_id']:
                raise ValueError('任务证据已变化，请重新审阅')
            previous = db.execute("SELECT data FROM events WHERE run_id=? AND kind='evaluation.completed' ORDER BY id DESC LIMIT 1", (record['run_id'],)).fetchone()
            encoded = json.dumps(record, ensure_ascii=False)
            if not previous or previous[0] != encoded:
                pending.append((record['run_id'], encoded))
        # Backup from a separate read connection; the reserved lock prevents intervening writers.
        if pending:
            backup_dir = Path(database).parent / 'backups'
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f'before-reviews-{time.time_ns()}.sqlite3'
            with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            backup.chmod(0o600)
            db.executemany("INSERT INTO events(run_id,kind,data,created) VALUES(?,'evaluation.completed',?,?)", [(run_id, data, time.time()) for run_id, data in pending])
        db.commit()
        return len(pending)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--reviews', required=True)
    args = parser.parse_args()
    print(f'已保存 {apply_reviews(args.database, json.loads(Path(args.reviews).read_text()))} 条评测')
