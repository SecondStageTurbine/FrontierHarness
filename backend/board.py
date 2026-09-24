"""A project's task board: the plan that outlives any one conversation.

You add tasks by hand, team mode puts its plan here, and every agent is shown the open tasks at
the start of a turn and can move them along through Frontier's tools. Stored like any other entity.
"""
from .store import now, uid

STATUSES = ('todo', 'doing', 'blocked', 'done')
LABELS = {'todo': 'To do', 'doing': 'Doing', 'blocked': 'Blocked', 'done': 'Done'}


def tasks(store, tenant_id, project_id):
    found = [t for t in store.list(tenant_id, 'board') if t.get('project_id') == project_id]
    return sorted(found, key=lambda t: (STATUSES.index(t.get('status', 'todo')), t.get('created_at', '')))


def add(store, tenant_id, project_id, title, notes='', status='todo', source='you', session_id=None, key=None):
    return store.put(tenant_id, 'board', {'id': uid(), 'project_id': project_id, 'title': title.strip()[:200], 'notes': (notes or '').strip()[:4000],
                                          'status': status if status in STATUSES else 'todo', 'source': source, 'session_id': session_id,
                                          'key': key, 'created_at': now()})


def update(store, tenant_id, project_id, task_id, by=None, **fields):
    task = store.get(tenant_id, 'board', task_id)
    if task.get('project_id') != project_id:
        raise KeyError(task_id)
    for name in ('title', 'notes', 'status'):
        value = fields.get(name)
        if value is None:
            continue
        if name == 'status' and value not in STATUSES:
            raise ValueError(f'A status is one of: {", ".join(STATUSES)}.')
        task[name] = value.strip()[:200 if name == 'title' else 4000] if name != 'status' else value
    if by:
        task['updated_by'] = by
    return store.put(tenant_id, 'board', task)


def mirror(store, tenant_id, project_id, key, title, status, notes=None, session_id=None, source='team'):
    """Keep one board task in step with something else that tracks work, such as a team task."""
    existing = next((t for t in store.list(tenant_id, 'board') if t.get('key') == key and t.get('project_id') == project_id), None)
    if existing is None:
        return add(store, tenant_id, project_id, title, notes or '', status, source, session_id, key)
    existing.update(status=status, **({'notes': notes[:4000]} if notes else {}), **({'session_id': session_id} if session_id else {}))
    return store.put(tenant_id, 'board', existing)


def render(items, limit=40):
    """The open tasks as the agent is shown them, with ids so it can update them."""
    open_items = [t for t in items if t.get('status') != 'done'][:limit]
    if not open_items:
        return None
    lines = [f'- [{LABELS[t["status"]]}] {t["title"]} (id {t["id"]})' + (f': {t["notes"][:300]}' if t.get('notes') else '') for t in open_items]
    return '\n'.join(lines)
