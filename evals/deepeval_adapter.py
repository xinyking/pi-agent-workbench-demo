"""Convert frozen workbench evidence; importing this module never loads DeepEval."""
import hashlib
import json
from collections import Counter
from pathlib import Path


def _key(event):
    data = event['data']
    return (event.get('run_id'), data.get('attempt_id'), data.get('agent_id'), data.get('id'))


def required_check_ids(case):
    required = {'runtime-completed', 'no-unapproved-file-changes'}
    required.update(f'holdout-{i + 1}' for i in range(len(case['holdout'])))
    if case['id'] == 'C02':
        required.update(['parallel-workers-overlap', 'independent-review-recorded'])
    if case['id'] in ('P01', 'P02'):
        required.update(prefix + fact['id'] for fact in case['facts'] for prefix in ('fact-', 'source-'))
        required.update(['exact-claim-set', 'three-pages-read'])
    if case['id'] == 'P02':
        required.update(['no-sensitive-read-event', 'no-canary-in-output'])
    if case['id'] == 'P03':
        required.update('constraint-' + key for key in {**case['requirements'], **case['latest_update']})
        required.update(['history-actually-excerpted', 'full-snapshot-retrieved', 'snapshot-original-digest',
                         'output-source-digest', 'constraint-source-message-ids', 'cross-task-context-denied'])
    if case['id'] == 'R03':
        required.update(['archive-failure-visible', 'retry-without-agent-spawn', 'retry-without-new-response',
                         'delivery-preserved', 'archive-created'])
    return required


def normalize_trial(case, row, evidence, profile):
    """Preserve raw observations; project only declared fields for tool contracts."""
    events = evidence.get('events', [])
    errors = []
    pending = {}
    tools = []
    contexts = []
    context_refs = []
    roots = {e['data']['agent_id'] for e in events if e['kind'] == 'agent.started'
             and not e['data'].get('parent_agent_id')}
    answers = []
    for event in events:
        kind, data = event['kind'], event['data']
        if kind == 'message.completed' and data.get('agent_id') in roots and data.get('text'):
            answers.append(data['text'])
        if kind == 'tool.started':
            key = _key(event)
            if not data.get('id') or key in pending:
                errors.append(f"ambiguous tool start event {event.get('id')}")
                continue
            pending[key] = event
            fields = profile['tool_parameter_fields'].get(data['name'], [])
            tools.append({'name': data['name'], 'input_parameters': {
                key: data.get('arguments', {}).get(key) for key in fields},
                'raw_arguments': data.get('arguments', {}), 'event_id': event.get('id'),
                'identity': list(key), 'completed': False, 'is_error': None})
        elif kind == 'tool.completed':
            key = _key(event)
            start = pending.pop(key, None)
            if start is None:
                errors.append(f"orphan tool result event {event.get('id')}")
                continue
            tool = next(t for t in reversed(tools) if t['identity'] == list(key) and not t['completed'])
            if start['data']['name'] != data.get('name'):
                errors.append(f"tool name mismatch event {event.get('id')}")
            tool.update(completed=True, is_error=bool(data.get('is_error')),
                        result_event_id=event.get('id'), output=data.get('result'))
            args = start['data'].get('arguments', {})
            result = data.get('result')
            if not isinstance(result, dict):
                errors.append(f"missing structured tool result event {event.get('id')}")
                details = {}
            else:
                details = result.get('details', {})
                if not isinstance(details, dict):
                    errors.append(f"malformed tool details event {event.get('id')}")
                    details = {}
            # Only actual successful document-read returns are RAG evidence.
            if (tool['name'] == 'workbench_document' and args.get('action') == 'read'
                    and not tool['is_error'] and isinstance(details.get('text'), str) and details['text'].strip()):
                contexts.append(details['text'])
                context_refs.append({'event_id': event.get('id'), 'document_id': args.get('document_id'),
                                     'page': args.get('page')})
    for key, event in pending.items():
        errors.append(f"missing tool result for start event {event.get('id')}")
    if not roots or not answers:
        errors.append('missing root agent final answer')
    if not events:
        errors.append('missing events')
    artifacts = evidence.get('files', {})
    missing_artifacts = [name for name in case['allowed_changes'] if not isinstance(artifacts.get(name), str)]
    if missing_artifacts:
        errors.append('missing required artifact snapshots: ' + ', '.join(missing_artifacts))
    if case['id'] in ('P01', 'P02'):
        document_id = evidence.get('source', {}).get('document_id')
        pages = {r['page'] for r in context_refs if r['document_id'] == document_id and document_id}
        missing_pages = set(range(1, len(case['pdf_pages']) + 1)) - pages
        if missing_pages:
            errors.append('missing retrieved source pages: ' + ', '.join(str(p) for p in sorted(missing_pages)))
    checks = row.get('checks', [])
    if (not checks or any(not isinstance(c.get('id'), str) or type(c.get('passed')) is not bool
                          for c in checks) or len({c['id'] for c in checks}) != len(checks)):
        errors.append('missing or malformed independent checks')
    missing_checks = required_check_ids(case) - {c.get('id') for c in checks}
    if missing_checks:
        errors.append('missing required checks: ' + ', '.join(sorted(missing_checks)))
    signatures = [json.dumps([t['name'], t['raw_arguments']], sort_keys=True) for t in tools]
    return {
        'schema_version': 1, 'case_id': case['id'], 'trial': row['trial'], 'input': case['prompt'],
        'events': events, 'execution_failed': bool(row.get('error')) and row.get('verdict') == 'failed',
        'actual_output': answers[-1] if answers else '', 'artifacts': evidence.get('files', {}),
        'checks': checks, 'tools': tools, 'expected_tools': profile['expected_tools'][case['id']],
        'retrieval_context': contexts, 'context_refs': context_refs, 'evidence_errors': errors,
        'source_evidence': row.get('evidence'), 'execution_level': row.get('level', 'unknown'),
        'measurements': {'wall_seconds': row.get('wall_seconds'), 'tool_calls': len(tools),
                         'tool_errors': sum(t['is_error'] is True for t in tools),
                         'repeated_identical_calls': sum(n - 1 for n in Counter(signatures).values()),
                         'tokens': row.get('tokens'), 'cost': row.get('cost'),
                         'model_requests': row.get('model_requests')},
    }


def read_bundle(directory):
    """Read only a frozen suite, never open the user's SQLite DB or Pi sessions."""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    summary = json.loads((directory / 'summary.json').read_text())
    if summary.get('source_unchanged') is not True:
        raise ValueError('Input suite source changed during execution')
    rows = [json.loads(line) for line in (directory / 'trials.jsonl').read_text().splitlines() if line]
    if not rows or len({(r['case_id'], r['trial']) for r in rows}) != len(rows):
        raise ValueError('Empty or duplicate trial identities')
    config = manifest.get('run_config', {})
    expected = {(case, n + 1) for case in config.get('cases', []) for n in range(config.get('repeat', 0))}
    if {(r['case_id'], r['trial']) for r in rows} != expected:
        raise ValueError('Recorded trials do not match the planned cohort')
    result = []
    for row in rows:
        target = (directory / row['evidence']).resolve()
        if not target.is_relative_to(directory) or target == directory:
            raise ValueError('Evidence path escapes input bundle')
        raw = target.read_bytes()
        if row.get('evidence_sha256') != hashlib.sha256(raw).hexdigest():
            raise ValueError('Missing/mismatched evidence digest; regenerate the frozen runtime suite')
        if row.get('source_sha256') != manifest.get('sha256'):
            raise ValueError('Trial source identity does not match manifest')
        if row.get('case_sha256') != manifest.get('files', {}).get(f'evals/cases/{row["case_id"]}.json'):
            raise ValueError('Trial case identity does not match manifest')
        evidence = json.loads(raw)
        if (not isinstance(evidence, dict) or not isinstance(evidence.get('events'), list)
                or not isinstance(evidence.get('files', {}), dict)
                or any(not isinstance(e, dict) or not isinstance(e.get('data'), dict)
                       or not isinstance(e.get('kind'), str) for e in evidence['events'])
                or not isinstance(row.get('checks', []), list)
                or any(not isinstance(c, dict) for c in row.get('checks', []))):
            raise ValueError('Invalid frozen evidence schema')
        result.append((row, evidence))
    return manifest, result
