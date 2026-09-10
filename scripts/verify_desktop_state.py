"""Export non-secret evidence from the explicitly created native verification project."""
import json
import os
import sqlite3
from pathlib import Path

database=Path(os.environ['LOCALAPPDATA'])/'dev.frontier.harness'/'harness.db'
with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
    projects=[json.loads(row[0]) for row in db.execute("SELECT data FROM entities WHERE kind='projects'")]
    project=next(p for p in projects if p['name']=='Desktop verification')
    runs=[json.loads(row[0]) for row in db.execute("SELECT data FROM entities WHERE kind='runs'")]
    runs=sorted([r for r in runs if r['workflow'].get('project_id')==project['id']],key=lambda r:r['created_at'])
assert sum(r['status']=='complete' for r in runs)>=2 and runs[-1]['status']=='complete'
final_changes={c['path']:c for r in runs for c in r['changes'] if c['status']=='applied'}
for change in final_changes.values():
    assert (Path(project['root'])/change['path']).read_text(encoding='utf-8')==change['after']
report={'project':project['name'],'runs':[{
    'instruction':r['workflow']['objective'],'status':r['status'],'iteration':r['iteration'],
    'error_code':r.get('error_code'),
    'models':sorted({m['model_name'] for m in r['models'].values()}),
    'input_tokens':r['input_tokens'],'output_tokens':r['output_tokens'],'cost_complete':r['cost_complete'],
    'changes':[{'path':c['path'],'kind':c['kind'],'status':c['status']} for c in r['changes']],
    'checks':[{'command':c['command'],'exit_code':c['exit_code'],'output':c['output']} for c in r['commands']],
    'review':r['stages'][-1]['artifact'],
} for r in runs],'latest_files_match_recorded_changes':True}
target=Path(__file__).resolve().parents[1]/'docs'/'live-verification.json'
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(f'Verified {len(runs)} completed native runs and their actual project files. Saved {target.name}.')
