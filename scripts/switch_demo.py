r"""Watch a spent subscription hand the turn over, without needing two real subscriptions.

Run:  .venv\Scripts\python switch_demo.py
"""
import asyncio, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tests.harness import setup_store
from backend import broker
from backend.schemas import ModelConfig

# Stands in for the Claude CLI. 'seat-1' answers the way a spent subscription answers:
# the reason inside the JSON, and a nonzero exit. 'seat-2' answers normally.
FAKE_CLI = """
import json, os, sys
sys.stdin.read()
home = os.environ.get('CLAUDE_CONFIG_DIR', '(machine default)')
if home.endswith('seat-1'):
    print(json.dumps({'is_error': True, 'result': 'Claude usage limit reached. Your limit will reset at 5pm.'}))
    sys.exit(1)
print(json.dumps({'result': 'Answered by ' + os.path.basename(home),
                  'usage': {'input_tokens': 12, 'output_tokens': 4}, 'is_error': False}))
"""

async def main():
    with tempfile.TemporaryDirectory() as directory:
        store = setup_store(pathlib.Path(directory))
        for account in ('seat-1', 'seat-2'):
            config = ModelConfig(name=account, provider='claude_cli', model_name='opus', account=account)
            store.put('tenant-a', 'models', {'id': account, **config.model_dump(exclude={'api_key'})})
        broker.resolve_cli = lambda provider: [sys.executable, '-c', FAKE_CLI]
        agents = broker.ModelBroker(store)

        print('Asking seat-1, which has nothing left...')
        config = store.get('tenant-a', 'models', 'seat-1')
        result = await agents.invoke_agent('tenant-a', config, 'Prompt.', 'edit', directory)
        print('  answered by :', result.served_by)
        print('  text        :', result.text)
        print('  cooling off :', [account for (_, account) in agents.cooldowns])
        print('  next order  :', [m['id'] for m in agents.accounts_for('tenant-a', config)])

        print()
        print('Credential directories, one per account:')
        for path in sorted((pathlib.Path(directory)/'subscriptions'/'tenant-a'/'claude_cli').iterdir()):
            print('  ', path)

asyncio.run(main())
