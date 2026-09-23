"""Scheduled and webhook-triggered turns. Each run is an ordinary session the automation opens.

A schedule is either `every` N minutes or `daily_at` HH:MM local time. The loop wakes every
half minute, and a run that was missed while Frontier was closed happens once on the next
wake rather than being replayed for every missed slot.
"""
import asyncio
import json
from datetime import datetime, timedelta

from .store import now, uid

TICK_SECONDS = 30


def next_run(automation, after=None):
    after = after or datetime.now()
    if automation.get('every'):
        return after + timedelta(minutes=max(1, int(automation['every'])))
    if automation.get('daily_at'):
        hour, minute = (int(x) for x in automation['daily_at'].split(':'))
        candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return candidate if candidate > after else candidate + timedelta(days=1)
    return None  # Webhook only.


def stamp(moment):
    return moment.isoformat(timespec='seconds') if moment else None


def due(automation, at=None):
    at = at or datetime.now()
    if not automation.get('enabled') or not automation.get('next_run_at'):
        return False
    return datetime.fromisoformat(automation['next_run_at']) <= at


async def run(store, runner, tenant_id, automation, trigger='schedule'):
    """Open a session in the automation's project and send its prompt as one turn."""
    project = store.get(tenant_id, 'projects', automation['project_id'])
    name = f'⏱ {automation["name"]} · {datetime.now().strftime("%b %d %H:%M")}'
    session = store.put(tenant_id, 'sessions', {'id': uid(), 'project_id': project['id'], 'name': name, 'messages': [], 'commands': [],
                                                'created_at': now(), 'updated_at': now(), 'automation_id': automation['id'], 'auto_named': False})
    try:
        runner.send(tenant_id, project['id'], session['id'], automation['prompt'], automation['model_id'], automation['mode'])
        outcome = 'started'
    except (ValueError, FileNotFoundError) as exc:
        outcome = f'failed: {exc}'
        store.event(tenant_id, session['id'], 'turn.failed', str(exc))
    fresh = store.get(tenant_id, 'automations', automation['id'])
    fresh.update(last_run_at=now(), last_outcome=outcome, last_session_id=session['id'], last_trigger=trigger,
                 next_run_at=stamp(next_run(fresh)), runs=(fresh.get('runs') or 0) + 1)
    store.put(tenant_id, 'automations', fresh)
    return session


async def scheduler(store, runner, stop):
    """The background loop; `stop` is an asyncio.Event the app sets on shutdown."""
    while not stop.is_set():
        try:
            with store.db() as db:
                tenants = [row['id'] for row in db.execute('SELECT id FROM tenants').fetchall()]
            for tenant_id in tenants:
                for automation in store.list(tenant_id, 'automations'):
                    if due(automation) and not runner.busy_anywhere(tenant_id, automation['project_id']):
                        await run(store, runner, tenant_id, automation)
        except Exception:
            pass  # One bad automation must not stop the loop; its own run records the failure.
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
        except TimeoutError:
            continue
