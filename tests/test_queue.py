"""Messages sent while a turn is running: queued for after it, or steered in by stopping it."""
import asyncio
import pytest
from backend.agent import AgentRunner
from backend.broker import AgentResult
from tests.harness import ScriptedAgent, open_project, setup_store, turn


def make(tmp_path, agent):
    store = setup_store(tmp_path/'state')
    folder = tmp_path/'work'
    folder.mkdir()
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    return store, runner, project, session


async def settled(runner, key):
    """Wait until no turn is running for this conversation, including any queued ones."""
    for _ in range(200):
        task = runner.turns.get(key)
        if task is None:
            return
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)  # Let the done callbacks drain the queue before checking again.


@pytest.mark.asyncio
async def test_a_queued_message_takes_its_turn_after_the_running_one(tmp_path):
    store, runner, project, session = make(tmp_path, ScriptedAgent(delay=0.05))
    key = ('tenant-a', session['id'])
    runner.send('tenant-a', project['id'], session['id'], 'First.', 'claude_cli', 'read')
    with pytest.raises(ValueError):
        runner.send('tenant-a', project['id'], session['id'], 'Second.', 'claude_cli', 'read')
    queued = runner.send('tenant-a', project['id'], session['id'], 'Second.', 'codex_cli', 'edit', queue=True)
    assert [q['content'] for q in queued['queue']] == ['Second.'] and len(queued['messages']) == 2
    await settled(runner, key)
    session = store.get('tenant-a', 'sessions', session['id'])
    assert [m['content'] for m in session['messages'] if m['role'] == 'user'] == ['First.', 'Second.']
    assert session['messages'][-1]['status'] == 'complete' and session['messages'][-1]['model_id'] == 'codex_cli'
    assert session['queue'] == []


@pytest.mark.asyncio
async def test_removing_a_queued_message_and_stopping_drops_the_queue(tmp_path):
    store, runner, project, session = make(tmp_path, ScriptedAgent(delay=0.3))
    runner.send('tenant-a', project['id'], session['id'], 'First.', 'claude_cli', 'read')
    a = runner.send('tenant-a', project['id'], session['id'], 'Drop me.', 'claude_cli', 'read', queue=True)['queue'][0]['id']
    runner.send('tenant-a', project['id'], session['id'], 'Keep me for now.', 'claude_cli', 'read', queue=True)
    assert [q['content'] for q in runner.unqueue('tenant-a', session['id'], a)['queue']] == ['Keep me for now.']
    await asyncio.sleep(0.05)  # The turn is underway, as it is by the time a stop request arrives.
    await runner.cancel('tenant-a', session['id'])
    await asyncio.sleep(0)
    session = store.get('tenant-a', 'sessions', session['id'])
    assert session['queue'] == [] and session['messages'][-1]['status'] == 'cancelled'
    assert [m['content'] for m in session['messages'] if m['role'] == 'user'] == ['First.']


@pytest.mark.asyncio
async def test_a_turn_records_its_cost_from_the_models_rates(tmp_path):
    async def priced(config, prompt, mode, root):
        return AgentResult('Done.', 1_000_000, 500_000)
    store, runner, project, session = make(tmp_path, ScriptedAgent(respond=priced))
    model = store.get('tenant-a', 'models', 'claude_cli')
    model.update(input_price=3.0, output_price=15.0)
    store.put('tenant-a', 'models', model)
    session = await turn(runner, store, 'tenant-a', project, session, 'Price this.', 'claude_cli', 'read')
    assert session['messages'][-1]['cost'] == pytest.approx(3.0 + 7.5)
    # A subscription login carries zero rates: the turn is covered, and the message says so with 0 rather than unknown.
    session = await turn(runner, store, 'tenant-a', project, session, 'Covered by the subscription.', 'codex_cli', 'read')
    assert session['messages'][-1]['cost'] == 0.0
    model = store.get('tenant-a', 'models', 'codex_cli')
    model.update(input_price=None, output_price=None)
    store.put('tenant-a', 'models', model)
    session = await turn(runner, store, 'tenant-a', project, session, 'No rates here.', 'codex_cli', 'read')
    assert session['messages'][-1]['cost'] is None
