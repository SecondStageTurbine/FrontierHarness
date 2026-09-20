"""Export non-secret evidence from the explicitly created native verification project.

Read-only against the installed application's own database, after a conversation has been held
in the native window. It proves the installed build did the work, not the source tree.
"""
import json
import os
import sqlite3
from pathlib import Path

PROJECT = os.environ.get('HARNESS_VERIFY_PROJECT', 'Desktop verification')
database = Path(os.environ['LOCALAPPDATA'])/'dev.frontier.harness'/'harness.db'
with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as db:
    projects = [json.loads(row[0]) for row in db.execute("SELECT data FROM entities WHERE kind='projects'")]
    project = next(p for p in projects if p['name'] == PROJECT)
    sessions = [json.loads(row[0]) for row in db.execute("SELECT data FROM entities WHERE kind='sessions'")]
    sessions = sorted([s for s in sessions if s['project_id'] == project['id']], key=lambda s: s['created_at'])

replies = [m for s in sessions for m in s['messages'] if m['role'] == 'assistant']
assert replies, 'No agent has answered in this project yet.'
assert any(r['status'] == 'complete' for r in replies), 'No turn completed.'
# What the agent wrote is only proven by the folder still holding it.
for reply in replies:
    for change in reply.get('changes', []):
        if change['status'] != 'removed':
            assert (Path(project['root'])/change['path']).read_text(encoding='utf-8') == change['after'], change['path']

agents = [r['model_name'] for r in replies if r.get('model_name')]
report = {
    'project': project['name'],
    'conversations': len(sessions),
    'agents_that_answered': sorted(set(agents)),
    'switched_mid_conversation': [{'from': r['switched_from'], 'to': r['model_name']} for r in replies if r.get('switched_from')],
    'turns': [{'agent': r.get('model_name'), 'mode': r.get('mode'), 'status': r['status'],
               'reply': (r.get('content') or '')[:200],
               'changed_files': [c['path'] for c in r.get('changes', [])]} for r in replies],
    'checks_run': [{'command': c['command'], 'exit_code': c['exit_code'], 'status': c['status']}
                   for s in sessions for c in s.get('commands', [])],
}
Path(__file__).resolve().parent.parent.joinpath('docs/desktop-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
