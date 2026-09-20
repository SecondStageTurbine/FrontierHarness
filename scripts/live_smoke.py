"""Opt-in real-agent integration test against the same harness the interface uses.

Run:  .venv\\Scripts\\python scripts/live_smoke.py [claude_cli|codex_cli|opencode_cli] [model]

It takes one real agentic turn in a throwaway project folder and reports what the agent
actually wrote there. Nothing is stubbed: the agent tool must be installed and signed in, which
is the only thing this proves that the test suite cannot. Only a redacted report is saved.
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.agent import AgentRunner
from backend.schemas import ModelConfig, TenantInput
from backend.store import Store, now, uid

INSTRUCTION = ('Create add.py containing an add(a, b) function that returns the sum of two integers, '
               'and test_add.py with two unittest cases covering positive and negative integers. '
               'Standard library only, under 30 lines in total. Then reply with one short sentence.')
DEFAULTS = {'claude_cli': 'sonnet', 'codex_cli': 'gpt-5.6-sol', 'opencode_cli': 'opencode/nemotron-3.5-lightning-free'}


async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else 'claude_cli'
    model_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULTS[provider]
    with tempfile.TemporaryDirectory(prefix='frontier-live-', ignore_cleanup_errors=True) as directory:
        work = Path(directory)/'project'
        work.mkdir()
        store = Store(str(Path(directory)/'state'))
        tenant = {'id': uid(), **TenantInput(name='Live').model_dump(), 'created_at': now()}
        with store.db() as db:
            db.execute('INSERT INTO tenants VALUES(?,?,?)', (tenant['id'], 'live-owner', json.dumps(tenant)))
        config = ModelConfig(name=provider, provider=provider, model_name=model_name)
        model = store.put(tenant['id'], 'models', {'id': uid(), **config.model_dump(exclude={'api_key'}),
                                                   'encrypted_key': None, 'key_hint': 'Subscription login', 'status': 'untested'})
        project = store.put(tenant['id'], 'projects', {'id': uid(), 'name': 'Live project', 'root': str(work), 'created_at': now()})
        session = store.put(tenant['id'], 'sessions', {'id': uid(), 'project_id': project['id'], 'name': 'Live',
                                                       'messages': [], 'commands': [], 'created_at': now(), 'updated_at': now()})
        runner = AgentRunner(store)
        runner.send(tenant['id'], project['id'], session['id'], INSTRUCTION, model['id'], 'auto')
        await asyncio.gather(runner.turns[(tenant['id'], session['id'])], return_exceptions=True)
        reply = store.get(tenant['id'], 'sessions', session['id'])['messages'][-1]
        report = {'provider': provider, 'model': model_name, 'status': reply['status'], 'error': reply['error'],
                  'reply': reply['content'][:400],
                  'changed_files': [{'path': c['path'], 'status': c['status'], 'bytes': len(c['after'] or '')} for c in reply['changes']],
                  'input_tokens': reply['input_tokens'], 'output_tokens': reply['output_tokens'],
                  'files_on_disk': sorted(p.name for p in work.rglob('*') if p.is_file())}
        Path(__file__).resolve().parent.parent.joinpath('docs/live-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
        if reply['status'] != 'complete':
            raise SystemExit(1)

asyncio.run(main())
