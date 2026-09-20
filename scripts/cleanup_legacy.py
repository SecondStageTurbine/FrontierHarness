"""Clear what the workflow engine left behind in a store created before 0.3.0.

Two things survive an upgrade that the application can no longer show. Saved workflows and runs
belong to a subsystem that no longer exists, so nothing reads them and nothing ever will. And
models were named after the role they played on a four-model team — Developer, Architect,
Project Manager — because the old interface asked for a job title; the agent picker shows that
name, so the roles appear to still be there when only the words are.

Run:  .venv\\Scripts\\python scripts/cleanup_legacy.py [--apply]
Without --apply it only reports. Renames are reversible in Agents & Providers.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

DEAD_KINDS = ('workflows', 'runs', 'agents', 'prompts')
# The job titles the old interface invited, which mean nothing to an agent that takes a turn.
ROLE_NAMES = {'developer', 'architect', 'project manager', 'planner', 'builder', 'reviewer',
              'discussion', 'engineer', 'tester', 'qa', 'analyst', 'designer'}
PROVIDER_LABELS = {'claude_cli': 'Claude', 'codex_cli': 'Codex', 'opencode_cli': 'OpenCode',
                   'anthropic': 'Anthropic', 'openai': 'OpenAI', 'custom_openai': 'Compatible', 'ollama': 'Local'}

apply = '--apply' in sys.argv
database = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith('--') \
    else Path(os.environ['LOCALAPPDATA'])/'dev.frontier.harness'/'harness.db'
if not database.is_file():
    raise SystemExit(f'No store at {database}. Pass the path to harness.db as the first argument.')


def suggested(model):
    """Name it after the model it actually reaches, which is the only durable fact about it."""
    label = PROVIDER_LABELS.get(model.get('provider'), model.get('provider') or 'Agent')
    identifier = (model.get('model_name') or '').split('/')[-1]
    return f'{label} {identifier}'.strip() if identifier else label


con = sqlite3.connect(database)
con.row_factory = sqlite3.Row
dead = {kind: con.execute('SELECT COUNT(*) FROM entities WHERE kind=?', (kind,)).fetchone()[0] for kind in DEAD_KINDS}
dead = {kind: count for kind, count in dead.items() if count}
renames = []
for row in con.execute("SELECT tenant_id,id,data FROM entities WHERE kind='models'"):
    model = json.loads(row['data'])
    if (model.get('name') or '').strip().lower() in ROLE_NAMES:
        renames.append((row['tenant_id'], row['id'], model, suggested(model)))

print(f'store: {database}')
print(f'dead rows from the removed engine: {dead or "none"}')
if renames:
    print('models still named after a team role:')
    for _, _, model, new in renames:
        print(f'  {model["name"]!r:20} -> {new!r}')
else:
    print('no models are named after a team role')

if not apply:
    print('\nReport only. Re-run with --apply to make these changes.')
    raise SystemExit(0)

with con:
    for kind in dead:
        con.execute('DELETE FROM entities WHERE kind=?', (kind,))
    for tenant_id, entity_id, model, new in renames:
        model['name'] = new
        con.execute('UPDATE entities SET data=? WHERE tenant_id=? AND kind=? AND id=?',
                    (json.dumps(model), tenant_id, 'models', entity_id))
print(f'\nRemoved {sum(dead.values())} dead rows and renamed {len(renames)} models.')
