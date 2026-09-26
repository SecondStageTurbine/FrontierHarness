"""Fixes for the problems the whole-codebase review found."""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import automations, sandbox
from backend.agent import AgentRunner
from backend.app import create_app
from backend.broker import AgentResult, ProviderError, strip_echo
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup, agent as connect_agent
from tests.test_batch12 import repo


@pytest.mark.skipif(os.name != 'nt', reason='Integrity levels are a Windows mechanism.')
def test_the_api_knows_a_low_integrity_caller_and_refuses_it_while_a_sandboxed_turn_runs(tmp_path, monkeypatch):
    server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(2)
    port = server.getsockname()[1]
    hold = f'import socket,time;s=socket.create_connection(("127.0.0.1",{port}));time.sleep(3)'
    levels = {}
    for name, argv in (('low', [*sandbox.launcher(), sys.executable, '-c', hold]), ('normal', [sys.executable, '-c', hold])):
        child = subprocess.Popen(argv)
        connection, (host, client_port) = server.accept()
        levels[name] = sandbox.peer_integrity(client_port, port)
        connection.close(); child.wait(timeout=10)
    server.close()
    assert levels['low'] == 0x1000 and levels['normal'] >= 0x2000
    # The middleware: with a sandboxed turn running, a low-integrity caller is refused; otherwise nothing changes.
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent()), client=('127.0.0.1', 50000)) as c:
        setup(c)
        monkeypatch.setattr(sandbox, 'peer_integrity', lambda client_port, server_port: 0x1000)
        assert c.get('/api/tenants').status_code == 200  # No sandboxed turn: no check.
        c.app.state.runner.broker.confined = 1
        refused = c.get('/api/tenants')
        assert refused.status_code == 403 and 'sandboxed' in refused.json()['detail']
        monkeypatch.setattr(sandbox, 'peer_integrity', lambda client_port, server_port: 0x2000)
        assert c.get('/api/tenants').status_code == 200


def test_automations_survive_a_removed_agent_or_project(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        automation = c.post(f'/api/t/{t}/automations', json={'name': 'Nightly', 'project_id': p['id'], 'model_id': a, 'prompt': 'Run the checks.', 'mode': 'read', 'every': 60, 'enabled': True}).json()
        store, runner = c.app.state.store, c.app.state.runner
        store.delete(t, 'models', a)  # The agent it uses is removed.
        before = len(store.list(t, 'sessions'))
        assert asyncio.run(automations.run(store, runner, t, store.get(t, 'automations', automation['id']))) is None
        after = store.get(t, 'automations', automation['id'])
        assert len(store.list(t, 'sessions')) == before  # No empty session left behind.
        assert after['last_outcome'].startswith('failed: its project or its agent was removed') and after['next_run_at'] > automations.stamp(automations.datetime.now())
        # Removing the project removes its automations, so nothing is left to fail.
        assert c.delete(f'/api/t/{t}/projects/{p["id"]}').status_code == 200
        assert not [x for x in store.list(t, 'automations') if x['project_id'] == p['id']]


def test_a_turn_keeps_what_the_user_changed_while_it_ran(tmp_path):
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    async def respond(config, prompt, mode, root):
        if config['id'] == 'claude_cli':
            # While this agent works, the user renames the conversation and queues a follow-up.
            live = store.get('tenant-a', 'sessions', 'session-tenant-a')
            live['name'] = 'Renamed meanwhile'
            live.setdefault('queue', []).append({'id': 'q1', 'content': 'And then docs.', 'model_id': 'claude_cli', 'mode': 'read', 'created_at': 'x', 'team': False, 'context': []})
            store.put('tenant-a', 'sessions', live)
            raise ProviderError('Claude Code has no usage left.', retryable=True, exhausted=True)
        return AgentResult('Done by the next agent.', 1, 1)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', folder)
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Build it.', 'claude_cli', 'edit')
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    final = store.get('tenant-a', 'sessions', session['id'])
    assert final['name'] == 'Renamed meanwhile'
    assert final['messages'][1]['content'] == 'Done by the next agent.'
    assert any(m['content'] == 'And then docs.' for m in final['messages']) or [q['content'] for q in final.get('queue') or []] == ['And then docs.']


def test_rewinding_past_the_tools_own_session_cuts_the_link(tmp_path):
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, ScriptedAgent())
    project, session = open_project(store, runner, 'tenant-a', folder)
    for text in ('One.', 'Two.'):
        session = asyncio.run(turn(runner, store, 'tenant-a', project, session, text, 'claude_cli', 'read'))
    session['native'] = {'provider': 'claude_cli', 'id': 'abc', 'upto': 4}
    store.put('tenant-a', 'sessions', session)
    asyncio.run(runner.rewind('tenant-a', project['id'], session['id'], session['messages'][2]['id'], restore_files=False))
    assert store.get('tenant-a', 'sessions', session['id'])['native'] is None
    # Rewinding only what came after the tool's own session keeps the link.
    session = asyncio.run(turn(runner, store, 'tenant-a', project, store.get('tenant-a', 'sessions', session['id']), 'Two again.', 'codex_cli', 'read'))
    session['native'] = {'provider': 'claude_cli', 'id': 'abc', 'upto': 2}
    store.put('tenant-a', 'sessions', session)
    asyncio.run(runner.rewind('tenant-a', project['id'], session['id'], session['messages'][2]['id'], restore_files=False))
    assert store.get('tenant-a', 'sessions', session['id'])['native'] == {'provider': 'claude_cli', 'id': 'abc', 'upto': 2}


def test_team_workers_build_on_earlier_tasks_and_a_fix_for_an_unknown_task_is_ignored(tmp_path):
    seen = {}
    async def respond(config, prompt, mode, root):
        if 'reply with ONLY a JSON object' in prompt and '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "Two steps.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt containing A.", "parallel": false},'
                               '{"id": "b", "title": "Write B", "instructions": "Create b.txt from a.txt.", "parallel": false, "depends_on": ["a"]}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "fix", "fixes": [{"task_id": "zz", "instructions": "Nothing real."}], "reply": "All good."}', 5, 5)
        if 'YOUR TASK — Write B' in prompt:
            seen['b saw a.txt'] = (Path(root)/'a.txt').exists()
            (Path(root)/'b.txt').write_text('B', encoding='utf-8')
            return AgentResult('Wrote b.txt.', 1, 1)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt, then b.txt from it.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['content'] == 'All good.'
    assert seen['b saw a.txt'] is True


def test_a_spent_single_login_rests_and_an_echoed_prompt_is_not_read_as_exhaustion(tmp_path):
    from backend.broker import ModelBroker, cooling
    store = setup_store(tmp_path/'state')
    broker = ModelBroker(store)
    async def spent(*args, **kwargs):
        raise ProviderError('no usage left', retryable=True, exhausted=True)
    broker.run_agent = spent
    config = store.get('tenant-a', 'models', 'claude_cli')
    with pytest.raises(ProviderError):
        asyncio.run(broker.invoke_agent('tenant-a', config, 'hi', 'read', str(tmp_path)))
    assert cooling(broker, 'tenant-a', 'claude_cli')
    prompt = 'USER:\nWhy do we hit the rate limit and the quota so often?'
    echoed = 'codex\nuser\n' + prompt + '\nERROR: stream disconnected'
    assert 'quota' not in strip_echo(echoed, prompt) and 'stream disconnected' in strip_echo(echoed, prompt)


def test_the_team_lead_sees_every_agent_with_where_it_runs_and_what_it_is_good_at(monkeypatch):
    from backend import team, localhealth
    monkeypatch.setattr(localhealth, 'local_providers', lambda: {'prometheus': 'http://127.0.0.1:8080/v1'})
    agents = [{'id': 'a', 'name': 'GPT-6-Astra', 'provider': 'codex_cli', 'model_name': 'gpt-6-astra'},
              {'id': 'q', 'name': 'Qwen 3.8 27B', 'provider': 'opencode_cli', 'model_name': 'prometheus/Prometheus'},
              {'id': 'h', 'name': 'Haiku', 'provider': 'claude_cli', 'model_name': 'haiku'}]
    text = team.roster(agents, {'offline': [{'name': 'Gemma local'}]}, agents[0])
    assert 'GPT-6-Astra: Codex, model gpt-6-astra; cloud, high cost' in text and '(you, the lead)' in text
    assert 'Qwen 3.8 27B: OpenCode, model prometheus/Prometheus; local on this computer, no usage cost' in text
    assert 'Haiku: Claude Code' in text and 'Offline right now, so not on the team: Gemma local.' in text
    assert 'not from your own model family' in team.PLAN_ASK and 'local agents' in team.PLAN_ASK


def test_opencode_failure_reports_opencode_own_error():
    """A local model that runs out of context must say so, not blame a subscription sign-in."""
    from backend.broker import ProviderError, read_output
    out = '\n'.join([
        '{"type":"text","part":{"type":"text","text":"Reading the log."}}',
        '{"type":"error","error":{"name":"APIError","data":{"message":"request (81972 tokens) exceeds the available context size (81920 tokens)"}}}',
    ])
    try:
        read_output('opencode_cli', 1, out, '', 'prompt', None)
    except ProviderError as error:
        assert 'exceeds the available context size' in str(error) and 'context window' in str(error)
        assert 'subscription' not in str(error)
    else:
        raise AssertionError('expected a ProviderError')


def test_a_task_too_big_for_a_local_model_goes_once_to_a_cloud_agent(tmp_path, monkeypatch):
    from backend import localhealth
    monkeypatch.setattr(localhealth, 'local_providers', lambda: {'opencode': 'http://127.0.0.1:9/v1'})
    async def all_up(models):
        return {m['id']: True for m in models}
    monkeypatch.setattr(localhealth, 'availability', all_up)
    ran = []
    async def respond(config, prompt, mode, root):
        if 'reply with ONLY a JSON object' in prompt and '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Read the logs", "instructions": "Summarise every log.", "agent": "OpenCode"}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        ran.append(config['provider'])
        if config['provider'] == 'opencode_cli':
            raise ProviderError("OpenCode stopped with an error: request exceeds the available context size. The conversation outgrew the model's context window.")
        return AgentResult('Summarised.', 1, 1)
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', repo(tmp_path))
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Summarise the logs.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    task = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]['team']['tasks'][0]
    assert ran[0] == 'opencode_cli' and len(ran) == 2 and ran[1] != 'opencode_cli'
    assert task['status'] == 'done' and task['model_id'] != 'opencode_cli'  # The worker's turn handed itself over.
    records = {r['id']: r for r in store.list('tenant-a', 'track_records')}
    assert records['opencode_cli']['failed'] == 1 and records['opencode_cli']['overflows'] == 1
    assert records[task['model_id']]['done'] == 1


def test_a_stopped_team_continues_with_only_its_unfinished_tasks(tmp_path):
    ran = []
    async def respond(config, prompt, mode, root):
        if 'reply with ONLY a JSON object' in prompt and '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "Two steps.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false},'
                               '{"id": "b", "title": "Write B", "instructions": "Create b.txt.", "parallel": false, "depends_on": ["a"]}]}', 5, 5)
        if 'REPORTS:' in prompt:
            ran.append('review')
            return AgentResult('{"verdict": "done", "reply": "Both written."}', 5, 5)
        name = 'a' if 'YOUR TASK — Write A' in prompt else 'b'
        ran.append(name)
        if name == 'b' and ran.count('b') == 1:
            await asyncio.sleep(30)  # Stopped here.
        (Path(root)/f'{name}.txt').write_text(name, encoding='utf-8')
        return AgentResult(f'Wrote {name}.txt.', 1, 1)
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', repo(tmp_path))
    key = ('tenant-a', session['id'])
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt, then b.txt.', 'claude_cli', 'edit', team=True)
        while ran.count('b') < 1:
            await asyncio.sleep(0.05)
        await runner.cancel(*key)
        stopped = store.get('tenant-a', 'sessions', session['id'])
        stopped['messages'][-1]['team'].pop('objective')  # As a team from before 0.20.8 was saved.
        store.put('tenant-a', 'sessions', stopped)
        assert store.get('tenant-a', 'sessions', session['id'])['messages'][-1]['status'] == 'cancelled'
        runner.continue_team('tenant-a', project['id'], session['id'])
        await asyncio.gather(runner.turns[key], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete' and reply['content'] == 'Both written.', reply.get('error')
    assert ran == ['a', 'b', 'b', 'review']  # A was not redone; the lead did not plan again.
    assert [t['status'] for t in reply['team']['tasks']] == ['done', 'done']
    assert reply['team']['objective'] == 'Create a.txt, then b.txt.'
    with pytest.raises(ValueError):
        runner.continue_team('tenant-a', project['id'], session['id'])


def test_an_agent_that_cannot_run_hands_the_turn_to_another(tmp_path):
    seen = []
    async def respond(config, prompt, mode, root):
        seen.append(config['provider'])
        if config['provider'] == 'claude_cli':
            raise ProviderError('Claude Code is not signed in.')
        return AgentResult('Done by the next agent.', 1, 1)
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', repo(tmp_path))
    reply = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Fix it.', 'claude_cli'))['messages'][-1]
    assert reply['status'] == 'complete' and reply['content'] == 'Done by the next agent.'
    assert seen[0] == 'claude_cli' and seen[1] != 'claude_cli' and reply['model_id'] != 'claude_cli'
    assert reply['routing']['attempts'][0]['outcome'] == 'could not run'


def test_an_agent_stopped_after_working_is_not_handed_over(tmp_path):
    seen = []
    async def respond(config, prompt, mode, root):
        seen.append(config['provider'])
        raise ProviderError('Claude Code was still working after 90 minutes and was stopped.', worked=True)
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', repo(tmp_path))
    reply = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Fix it.', 'claude_cli'))['messages'][-1]
    assert reply['status'] == 'failed' and seen == ['claude_cli']


def test_a_team_worker_and_lead_that_cannot_run_are_replaced(tmp_path):
    ran = []
    async def respond(config, prompt, mode, root):
        if config['provider'] == 'gemini_cli':
            raise ProviderError('Antigravity CLI stopped with an error: invalid project ID.')
        if 'reply with ONLY a JSON object' in prompt and '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "agent": "Gemini"}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        ran.append(config['provider'])
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    store = setup_store(tmp_path/'state')
    store.put('tenant-a', 'models', {'id': 'gemini', 'name': 'Gemini', 'provider': 'gemini_cli', 'model_name': 'gemini-3.8-flash-high'})
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', repo(tmp_path))
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'gemini', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['lead'] != 'Gemini'  # The lead's seat moved.
    task = reply['team']['tasks'][0]
    assert task['status'] == 'done' and task['model_id'] != 'gemini' and ran and 'gemini_cli' not in ran


def test_the_live_view_turns_each_tools_output_into_steps():
    from backend.broker import live_line
    claude = ('{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"npm test"}},'
              '{"type":"text","text":"Running the tests."}]}}')
    assert live_line('claude_cli', claude, '') == '▸ Bash: npm test\nRunning the tests.'
    import json
    result = json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': '\n3 passed\nok'}]}})
    assert live_line('claude_cli', result, '') == '  ↳ 3 passed'
    assert live_line('opencode_cli', '{"type":"tool_use","part":{"type":"tool","tool":"edit","state":{"input":{"filePath":"src/a.ts"}}}}', '') == '▸ edit: src/a.ts'
    assert live_line('gemini_cli', '{"event":"step_update","step_update":{"step_type":"tool","state":"ACTIVE","tool_name":"run_command","tool_info":{"parameters":{"CommandLine":"dir"}}}}', '') == '▸ run_command: dir'
    assert live_line('codex_cli', '\x1b[1mexec\x1b[0m npm test', 'USER: fix it') == 'exec npm test'
    assert live_line('codex_cli', 'USER: fix it', 'USER: fix it') is None  # Its echo of the conversation is not news.
    assert live_line('claude_cli', '{"type":"system","subtype":"init"}', '') is None


def test_the_live_view_route_returns_new_lines_and_a_workers_lines_reach_its_team(tmp_path):
    store = setup_store(tmp_path/'state')
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        runner = c.app.state.runner if hasattr(c.app.state, 'runner') else None
        project = c.post(f'/api/t/{t}/projects', json={'name': 'p', 'root': str(repo(tmp_path))}).json()
        parent = c.post(f'/api/t/{t}/projects/{project["id"]}/sessions', json={'name': 'Team'}).json()
        worker = c.post(f'/api/t/{t}/projects/{project["id"]}/sessions', json={'name': 'Write A · Codex'}).json()
        state = c.app.state
        run = next(v for v in vars(state).values() if hasattr(v, 'watch')) if runner is None else runner
        stored = run.store.get(t, 'sessions', worker['id'])
        run.store.put(t, 'sessions', {**stored, 'team_parent': parent['id']})
        feed = run.watch(t, worker['id'], 'Codex')
        feed('▸ bash: npm test')
        feed('3 passed')
        live = c.get(f'/api/t/{t}/projects/{project["id"]}/sessions/{worker["id"]}/live').json()
        assert [l['text'] for l in live['lines']] == ['▸ bash: npm test', '3 passed'] and live['agent'] == 'Codex' and live['running'] is False
        assert [l['text'] for l in c.get(f'/api/t/{t}/projects/{project["id"]}/sessions/{worker["id"]}/live?since=1').json()['lines']] == ['3 passed']
        team = c.get(f'/api/t/{t}/projects/{project["id"]}/sessions/{parent["id"]}/live').json()
        assert [l['text'] for l in team['lines']] == ['[Write A] ▸ bash: npm test', '[Write A] 3 passed']
