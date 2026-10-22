"""A turn is one message answered by one agent, in the project folder."""
import asyncio
import pytest
from backend.agent import AgentRunner, asks_to_proceed, commits_to_work, build_prompt, changes_between, fingerprint, turn_record
from backend.broker import AgentResult, ProviderError
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
async def test_a_turn_interrupted_by_a_restart_is_marked_and_resumed_only_on_request(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(delay=5))
    runner.send('tenant-a', project['id'], session['id'], 'work', 'claude_cli', 'edit')
    runner.turns[('tenant-a', session['id'])].cancel()
    await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    stuck = store.get('tenant-a', 'sessions', session['id'])
    stuck['messages'][-1].update(status='running', error=None)  # As a killed process would leave it.
    store.put('tenant-a', 'sessions', stuck)
    calls = []
    async def respond(config, prompt, mode, root):
        calls.append(prompt)
        return AgentResult('Picked it back up.', 5, 5)
    restarted = AgentRunner(store, ScriptedAgent(respond=respond))
    restarted.recover()
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'interrupted' and 'closed while this turn was running' in reply['error']
    assert any(e['type'] == 'turn.interrupted' for e in store.events('tenant-a', session['id']))
    assert not restarted.turns and not calls  # Nothing reruns until the user asks.
    restarted.resume('tenant-a', project['id'], session['id'], reply['id'])
    await asyncio.gather(restarted.turns[('tenant-a', session['id'])], return_exceptions=True)
    messages = store.get('tenant-a', 'sessions', session['id'])['messages']
    assert len(messages) == 2 and messages[-1]['id'] == reply['id'] and messages[-1]['status'] == 'complete'
    assert 'USER:\nwork' in calls[0] and 'closed while this turn' not in calls[0]  # Replayed from the stored transcript.
    with pytest.raises(ValueError):
        restarted.resume('tenant-a', project['id'], session['id'], reply['id'])  # Only an interrupted turn.


def test_each_part_of_a_prompt_is_counted_and_the_parts_add_up():
    messages = [{'role': 'user', 'content': 'first'}, {'role': 'assistant', 'content': 'ok', 'model_name': 'Claude'},
                {'role': 'user', 'content': 'next'}]
    composition = {}
    prompt = build_prompt(messages, False, handoff='Left off at step 2.', mode='edit', summary='Earlier talk.', first=True,
                          rules='Be brief.', memory='Uses pytest.', environment='Python 3.12.', board='- [ ] ship', composition=composition)
    chars = composition['chars']
    assert set(chars) == {'instructions', 'handoff', 'rules', 'notes', 'environment', 'board', 'summary', 'transcript'}
    assert sum(chars.values()) == len(prompt) and composition['dropped'] == 0


def test_a_turn_that_dropped_messages_records_the_drop():
    messages = [{'role': 'user', 'content': 'x'*70_000},
                {'role': 'assistant', 'content': 'y'*70_000, 'model_name': 'Claude'},
                {'role': 'user', 'content': 'the latest question'}]
    composition = {}
    prompt = build_prompt(messages, switched=False, composition=composition)
    assert composition['dropped'] >= 1 and sum(composition['chars'].values()) == len(prompt)
    record = turn_record({'summary': {'count': 4}}, {'id': 'm1', 'status': 'complete', 'model_name': 'Claude', 'finished_at': '2026-10-21T10:00:30+00:00',
                                                     'routing': {'mode': 'adaptive', 'escalations': 1}},
                         {'started_at': '2026-10-21T10:00:00+00:00', 'model': 'opus', **composition})
    assert record['dropped'] == composition['dropped'] and record['prompt_chars'] == len(prompt)
    assert record['seconds'] == 30 and record['compacted'] == 4 and record['escalated']


@pytest.mark.asyncio
async def test_a_finished_turn_is_written_to_the_session_ledger(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(reply='Done.'))
    session = await turn(runner, store, 'tenant-a', project, session, 'Do it.')
    [record] = session['ledger']
    assert record['message_id'] == session['messages'][-1]['id'] and record['status'] == 'complete'
    assert record['chars']['transcript'] > 0 and record['prompt_chars'] == sum(record['chars'].values())
    assert record['dropped'] == 0 and record['escalated'] is False and record['seconds'] is not None


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
    # An agent asked to reinstall Frontier once killed the app hosting its own turn.
    assert 'run the installer themselves' in build_prompt(messages, switched=False, mode='auto')


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


def test_the_agent_is_told_it_cannot_report_back_after_its_turn():
    prompt = build_prompt([{'role': 'user', 'content': 'run the gate'}], switched=False, mode='auto')
    assert 'cannot send anything afterwards' in prompt and 'stopped when your turn ends' in prompt


@pytest.mark.asyncio
async def test_a_project_can_give_its_turns_more_time(tmp_path):
    agent = ScriptedAgent()
    store, runner, project, session, _ = make(tmp_path, agent)
    project = store.get('tenant-a', 'projects', project['id'])
    project['turn_minutes'] = 150
    store.put('tenant-a', 'projects', project)
    await turn(runner, store, 'tenant-a', project, session, 'Long build.', 'claude_cli', 'read')
    assert agent.calls[-1]['extras']['timeout'] == 150*60
    project['turn_minutes'] = 20  # Set before the floor existed: raised to the floor, never lower.
    store.put('tenant-a', 'projects', project)
    await turn(runner, store, 'tenant-a', project, session, 'Again.', 'claude_cli', 'read')
    assert agent.calls[-1]['extras']['timeout'] == 90*60


def test_only_a_plain_request_to_start_counts_as_asking_leave():
    assert asks_to_proceed("I haven't re-verified the branch. Should I start the rebase and tests now?")
    assert asks_to_proceed('Want me to open the draft PR?')
    assert not asks_to_proceed('Should I use Postgres or SQLite?')  # A choice is the user's.
    assert not asks_to_proceed('Should I merge it?')  # So is anything that cannot be taken back.
    assert not asks_to_proceed('Done. Should I start? No, it is finished.')
    assert not asks_to_proceed('What should the title be?')


@pytest.mark.asyncio
async def test_a_turn_that_asks_whether_to_start_is_told_to_go_ahead_once(tmp_path):
    prompts = []
    async def respond(config, prompt, mode, root):
        prompts.append(prompt)
        return AgentResult('The rebase is next. Should I start it now?', 5, 5)  # Asks every time.
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(respond=respond))
    runner.send('tenant-a', project['id'], session['id'], 'Do you have work to do?', 'claude_cli', 'edit')
    for _ in range(20):
        task = runner.turns.get(('tenant-a', session['id']))
        if task is None:
            break
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    messages = store.get('tenant-a', 'sessions', session['id'])['messages']
    assert [m['role'] for m in messages] == ['user', 'assistant', 'user', 'assistant']  # One yes, never a loop.
    assert messages[2]['auto_continue'] and 'go ahead' in messages[2]['content']
    assert any(e['type'] == 'turn.carried_on' for e in store.events('tenant-a', session['id']))


def test_saying_the_work_is_starting_counts_as_committing_to_it():
    assert commits_to_work('yes, jcBld3. I’m starting P9.21. Its branch is builder3/p9-21.')
    assert commits_to_work("I'll build the scoring first, then the table.")
    assert commits_to_work("I'm moving to **P9.21**, then P9.18.")
    assert commits_to_work('Branch: `builder2/p9-1-acceptance`. Estimated first head: within 90 minutes of resuming acceptance work.')
    assert not commits_to_work('P9.21 is merged and tested. Nothing is left on it.')


@pytest.mark.asyncio
async def test_a_slack_answer_is_followed_by_one_turn_to_do_the_work_it_names(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(reply='P9.2 acceptance in draft #696; full validation remains unfinished. This is my next work.'))
    runner.send('tenant-a', project['id'], session['id'], 'From Slack: what are you on?', 'claude_cli', 'edit', unattended=True)
    for _ in range(20):
        task = runner.turns.get(('tenant-a', session['id']))
        if task is None:
            break
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    messages = store.get('tenant-a', 'sessions', session['id'])['messages']
    assert [m['role'] for m in messages] == ['user', 'assistant', 'user', 'assistant']  # Once, though it says so again.
    assert messages[2]['auto_continue'] and messages[2]['unattended'] and 'unfinished and not blocked' in messages[2]['content']


@pytest.mark.asyncio
async def test_a_read_only_turn_waits(tmp_path):
    store, runner, project, session, _ = make(tmp_path, ScriptedAgent(reply="Should I start it now? I'm starting P9.21."))
    await turn(runner, store, 'tenant-a', project, session, 'Look only.', mode='read')
    await asyncio.sleep(0)
    assert len(store.get('tenant-a', 'sessions', session['id'])['messages']) == 2 and not runner.turns
