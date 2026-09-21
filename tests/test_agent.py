"""A turn is one message answered by one agent, in the project folder."""
import asyncio
import pytest
from backend.agent import AgentRunner, build_prompt, changes_between, fingerprint
from backend.broker import ProviderError
from backend.projects import ProjectFiles
from tests.harness import ScriptedAgent, open_project, setup_store, turn


def make(tmp_path, agent=None):
    store = setup_store(tmp_path/'state')
    folder = tmp_path/'work'
    folder.mkdir()
    (folder/'notes.md').write_text('start\n', encoding='utf-8')
    runner = AgentRunner(store, agent or ScriptedAgent())
    project, session = open_project(store, runner, 'tenant-a', folder)
    return store, runner, project, session, folder


@pytest.mark.asyncio
async def test_a_turn_answers_and_records_what_it_changed(tmp_path):
    agent = ScriptedAgent(reply='Added the note.', writes={'notes.md': 'start\nadded\n', 'new.txt': 'hello'})
    store, runner, project, session, folder = make(tmp_path, agent)
    session = await turn(runner, store, 'tenant-a', project, session, 'Add a note.')
    user, reply = session['messages']
    assert user['role'] == 'user' and reply['status'] == 'complete' and reply['content'] == 'Added the note.'
    # The agent writes to the folder itself, so the before-and-after is the only record of it.
    assert {(c['path'], c['status']) for c in reply['changes']} == {('notes.md', 'modified'), ('new.txt', 'added')}
    assert next(c for c in reply['changes'] if c['path'] == 'notes.md')['before'] == 'start\n'
    assert (folder/'new.txt').read_text(encoding='utf-8') == 'hello'
    assert agent.calls[0]['root'] == str(folder) and agent.calls[0]['mode'] == 'edit'


@pytest.mark.asyncio
async def test_switching_agent_carries_the_conversation_and_says_so(tmp_path):
    agent = ScriptedAgent(reply='Understood.')
    store, runner, project, session, _ = make(tmp_path, agent)
    session = await turn(runner, store, 'tenant-a', project, session, 'Remember the number 41.', 'claude_cli')
    session = await turn(runner, store, 'tenant-a', project, session, 'What number?', 'codex_cli')
    second = session['messages'][-1]
    # The second agent is told it is taking over, and is given everything said so far: the two
    # tools share no session store, so this replay is the whole of the handover.
    assert second['switched_from'] == 'Claude' and second['model_name'] == 'Codex'
    handover = agent.calls[-1]['prompt']
    assert 'continuing a conversation that a different agent' in handover
    assert 'Remember the number 41.' in handover and 'Understood.' in handover
    # Staying on the same agent is not a handover and costs no preamble.
    session = await turn(runner, store, 'tenant-a', project, session, 'Still there?', 'codex_cli')
    assert session['messages'][-1]['switched_from'] is None
    assert 'continuing a conversation that a different agent' not in agent.calls[-1]['prompt']


@pytest.mark.asyncio
async def test_read_only_neither_touches_the_folder_nor_reports_changes(tmp_path):
    agent = ScriptedAgent(reply='It says start.', writes={'notes.md': 'should not be written'})
    store, runner, project, session, folder = make(tmp_path, agent)
    session = await turn(runner, store, 'tenant-a', project, session, 'What is in notes.md?', 'claude_cli', 'read')
    assert session['messages'][-1]['changes'] == []
    assert (folder/'notes.md').read_text(encoding='utf-8') == 'start\n'
    assert agent.calls[0]['mode'] == 'read'


@pytest.mark.asyncio
async def test_a_failed_turn_closes_its_message_instead_of_hanging_the_conversation(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(fail=ProviderError('The subscription is not signed in.')))
    session = await turn(runner, store, 'tenant-a', project, session, 'Do something.')
    reply = session['messages'][-1]
    assert reply['status'] == 'failed' and 'not signed in' in reply['error']
    assert not runner.busy('tenant-a', session['id'])  # A stuck message would block the conversation forever.


@pytest.mark.asyncio
async def test_an_api_key_model_is_refused_before_a_turn_starts(tmp_path):
    store, runner, project, session, _ = make(tmp_path)
    with pytest.raises(ValueError, match='API key'):
        runner.send('tenant-a', project['id'], session['id'], 'hello', 'keyed', 'edit')
    assert store.get('tenant-a', 'sessions', session['id'])['messages'] == []


@pytest.mark.asyncio
async def test_one_conversation_runs_one_turn_at_a_time(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(delay=0.3))
    runner.send('tenant-a', project['id'], session['id'], 'first', 'claude_cli', 'edit')
    with pytest.raises(ValueError, match='still working'):
        runner.send('tenant-a', project['id'], session['id'], 'second', 'claude_cli', 'edit')
    await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    assert store.get('tenant-a', 'sessions', session['id'])['messages'][-1]['status'] == 'complete'


@pytest.mark.asyncio
async def test_stopping_a_turn_keeps_what_the_agent_already_wrote(tmp_path):
    store, runner, project, session, folder = make(tmp_path, ScriptedAgent(delay=5))
    runner.send('tenant-a', project['id'], session['id'], 'long job', 'claude_cli', 'edit')
    await asyncio.sleep(0.1)
    (folder/'partial.txt').write_text('half done', encoding='utf-8')  # What an agent leaves behind mid-turn.
    await runner.cancel('tenant-a', session['id'])
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'cancelled' and (folder/'partial.txt').is_file()
    assert [c['path'] for c in reply['changes']] == ['partial.txt']


@pytest.mark.asyncio
async def test_a_turn_interrupted_by_a_restart_is_closed_out_not_replayed(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(delay=5))
    runner.send('tenant-a', project['id'], session['id'], 'work', 'claude_cli', 'edit')
    runner.turns[('tenant-a', session['id'])].cancel()
    await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    stuck = store.get('tenant-a', 'sessions', session['id'])
    stuck['messages'][-1].update(status='running', error=None)  # As a killed process would leave it.
    store.put('tenant-a', 'sessions', stuck)
    AgentRunner(store, ScriptedAgent()).recover()
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'failed' and 'closed while this turn was running' in reply['error']


def test_an_overlong_conversation_drops_its_oldest_turns_rather_than_refusing():
    messages = [{'role': 'user', 'content': 'x'*70_000},
                {'role': 'assistant', 'content': 'y'*70_000, 'model_name': 'Claude'},
                {'role': 'user', 'content': 'the latest question'}]
    prompt = build_prompt(messages, switched=False)
    # Refusing to continue is worse than forgetting the beginning, and the model is told.
    assert 'the latest question' in prompt and 'dropped to fit' in prompt
    assert len(prompt) < 130_000


def test_the_agent_is_told_its_posture_so_it_asks_for_full_auto_instead_of_manual_git():
    messages = [{'role': 'user', 'content': 'commit and push this'}]
    edit = build_prompt(messages, switched=False, mode='edit')
    assert 'Full auto' in edit and 'commit' in edit
    assert 'push' in build_prompt(messages, switched=False, mode='auto')
    assert 'Full auto' not in build_prompt(messages, switched=False)


def test_a_folder_read_twice_reports_only_what_actually_moved(tmp_path):
    store = setup_store(tmp_path/'state')
    folder = tmp_path/'work'
    folder.mkdir()
    (folder/'keep.txt').write_text('same', encoding='utf-8')
    (folder/'gone.txt').write_text('bye', encoding='utf-8')
    store.put('tenant-a', 'projects', {'id': 'p', 'name': 'P', 'root': str(folder), 'created_at': '2026-01-01T00:00:00Z'})
    files = ProjectFiles(store)
    before = fingerprint(files, 'tenant-a', 'p')
    (folder/'gone.txt').unlink()
    (folder/'new.txt').write_text('hi', encoding='utf-8')
    changes = changes_between(before, fingerprint(files, 'tenant-a', 'p'))
    assert {(c['path'], c['status']) for c in changes} == {('gone.txt', 'removed'), ('new.txt', 'added')}
