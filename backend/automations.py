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


async def housekeeping(store, runner, tenant_id):
    """Wake snoozed sessions whose time has come, and archive ones idle past the workspace's limit."""
    tenant = store.tenant_internal(tenant_id) or {}
    limit = tenant.get('auto_archive_days')
    moment = datetime.now().astimezone()
    for session in store.list(tenant_id, 'sessions'):
        changed = False
        until = session.get('snoozed_until')
        if until:
            try:
                if datetime.fromisoformat(until.replace('Z', '+00:00')) <= moment:
                    session.pop('snoozed_until', None)
                    changed = True
            except ValueError:
                session.pop('snoozed_until', None)
                changed = True
        if limit and not session.get('archived') and not session.get('pinned') and session.get('messages') and not runner.busy(tenant_id, session['id']):
            try:
                updated = datetime.fromisoformat(session['updated_at'].replace('Z', '+00:00'))
            except (ValueError, KeyError):
                updated = None
            if updated and updated.tzinfo is None:
                updated = updated.replace(tzinfo=moment.tzinfo)
            if updated and updated < moment - timedelta(days=int(limit)):
                if tenant.get('memory_auto') and len(session['messages']) >= 2:
                    try:
                        await runner.remember(tenant_id, session['project_id'], session['id'])
                    except Exception:
                        pass  # Memory is a nicety; archiving is the point.
                session['archived'] = True
                session['archived_reason'] = f'idle for {limit} days'
                changed = True
        cleanup = tenant.get('worktree_cleanup_days')
        if cleanup is not None and session.get('archived') and session.get('worktree') and not runner.busy(tenant_id, session['id']):
            try:
                archived_at = datetime.fromisoformat(session['updated_at'].replace('Z', '+00:00'))
                if archived_at.tzinfo is None:
                    archived_at = archived_at.replace(tzinfo=moment.tzinfo)
            except (ValueError, KeyError):
                archived_at = None
            if archived_at and archived_at <= moment - timedelta(days=int(cleanup)):
                from . import gitops
                try:
                    project = store.get(tenant_id, 'projects', session['project_id'])
                    await gitops.worktree_remove(project['root'], session['worktree']['path'])
                    session['worktree_removed'] = session['worktree']['branch']
                    session['worktree'] = None
                    changed = True
                except Exception:
                    pass  # A missing project or a locked folder is tried again next tick.
        if changed:
            store.put(tenant_id, 'sessions', session)


async def scheduler(store, runner, stop):
    """The background loop; `stop` is an asyncio.Event the app sets on shutdown."""
    while not stop.is_set():
        try:
            with store.db() as db:
                tenants = [row['id'] for row in db.execute('SELECT id FROM tenants').fetchall()]
            for tenant_id in tenants:
                await housekeeping(store, runner, tenant_id)
                for automation in store.list(tenant_id, 'automations'):
                    if due(automation) and not runner.busy_anywhere(tenant_id, automation['project_id']):
                        await run(store, runner, tenant_id, automation)
        except Exception:
            pass  # One bad automation must not stop the loop; its own run records the failure.
        try:
            await asyncio.wait_for(stop.wait(), TICK_SECONDS)
        except TimeoutError:
            continue
