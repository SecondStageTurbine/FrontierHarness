"""Deterministic test doubles live only in tests; production always launches a real agent."""
import asyncio
import json
from backend.broker import AgentResult
from backend.schemas import TenantInput, ModelConfig
from backend.store import Store


class ScriptedAgent:
    """Stands in for an agent command line tool: it answers, and optionally edits the folder.

    `writes` maps a relative path to its new content, applied when the turn is allowed to edit,
    which is how a test exercises the before-and-after a real agent produces by writing files.
    """
    def __init__(self, reply='Done.', writes=None, delay=0, fail=None, respond=None):
        self.reply, self.writes, self.delay, self.fail, self.respond = reply, writes or {}, delay, fail, respond
        self.calls = []
        self.cooldowns = {}  # The runner consults the broker's cooldowns when ranking Adaptive candidates.

    async def invoke_agent(self, tenant_id, config, prompt, mode, root, extras=None):
        from pathlib import Path
        self.calls.append({'tenant_id': tenant_id, 'model_id': config['id'], 'provider': config['provider'],
                           'mode': mode, 'root': str(root), 'prompt': prompt, 'extras': extras})
        await asyncio.sleep(self.delay)
        if self.respond:
            # A per-agent script, for tests where which agent answers is the point.
            return await self.respond(config, prompt, mode, root)
        if self.fail:
            raise self.fail
        if mode != 'read':
            for name, content in self.writes.items():
                target = Path(root)/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8')
        return AgentResult(self.reply, 120, 40)


def setup_store(tmp_path):
    """Two workspaces, each with the three agents and one keyed model that cannot take a turn."""
    store = Store(str(tmp_path))
    for tenant in ['tenant-a', 'tenant-b']:
        value = {'id': tenant, **TenantInput(name=tenant).model_dump()}
        with store.db() as db:
            db.execute('INSERT INTO tenants VALUES(?,?,?)', (tenant, 'owner-'+tenant, json.dumps(value)))
        for name, provider, model_name in [('Claude', 'claude_cli', 'sonnet'), ('Codex', 'codex_cli', 'gpt-5.6-sol'),
                                           ('OpenCode', 'opencode_cli', 'opencode/free')]:
            config = ModelConfig(name=name, provider=provider, model_name=model_name)
            store.put(tenant, 'models', {'id': provider, **config.model_dump(exclude={'api_key'}),
                                         'encrypted_key': None, 'key_hint': 'Subscription login', 'status': 'untested'})
        keyed = ModelConfig(name='Keyed', provider='openai', model_name='gpt-5.6-terra')
        store.put(tenant, 'models', {'id': 'keyed', **keyed.model_dump(exclude={'api_key'}),
                                     'encrypted_key': store.encrypt('private-'+tenant), 'key_hint': '••••test', 'status': 'untested'})
    return store


def open_project(store, runner, tenant, folder, name='Project'):
    project = store.put(tenant, 'projects', {'id': 'project-'+tenant, 'name': name, 'root': str(folder), 'created_at': '2026-01-01T00:00:00Z'})
    session = store.put(tenant, 'sessions', {'id': 'session-'+tenant, 'project_id': project['id'], 'name': 'New session',
                                             'messages': [], 'commands': [], 'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z'})
    return project, session


async def turn(runner, store, tenant, project, session, content, model_id='claude_cli', mode='edit'):
    """Send one message and wait for its turn to finish, as the interface does."""
    runner.send(tenant, project['id'], session['id'], content, model_id, mode)
    task = runner.turns.get((tenant, session['id']))
    if task:
        await asyncio.gather(task, return_exceptions=True)
    return store.get(tenant, 'sessions', session['id'])
