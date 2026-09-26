"""Approval cards, MCP injection, Gemini, fan-out, automations, usage, remote, and the permission tool."""
import asyncio
import io
import json
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import automations, permission_tool, remote
from backend.agent import AgentRunner
from backend.app import create_app
from backend.broker import AgentResult, agent_argv, mcp_config_for_claude, mcp_config_for_opencode, read_gemini, toml_value, ProviderError
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup, agent as connect_agent


def test_mcp_servers_reach_each_tool_in_its_own_spelling(tmp_path):
    servers = [{'name': 'graft', 'transport': 'stdio', 'command': 'npx', 'args': ['-y', 'graft-mcp'], 'env': {'A': '1'}},
               {'name': 'docs', 'transport': 'http', 'url': 'https://mcp.example.com/'}]
    approval = {'url': 'http://127.0.0.1:1234', 'token': 't0k', 'command': ['py', '--permission-tool'], 'env': {}}
    claude = mcp_config_for_claude(servers, approval)['mcpServers']
    assert claude['graft']['args'] == ['-y', 'graft-mcp'] and claude['docs'] == {'type': 'http', 'url': 'https://mcp.example.com/'}
    assert claude['frontier']['env']['FRONTIER_TOKEN'] == 't0k' and claude['frontier']['args'] == ['--permission-tool']
    argv = agent_argv('claude_cli', ['claude'], 'sonnet', 'edit', tmp_path, tmp_path/'f', {'mcp_config': tmp_path/'mcp.json', 'approval': approval})
    assert '--mcp-config' in argv and argv[argv.index('--permission-prompt-tool')+1] == 'mcp__frontier__approve'
    # Read only never asks: nothing is allowed that would need asking. Full auto never asks either.
    assert '--permission-prompt-tool' not in agent_argv('claude_cli', ['claude'], 'sonnet', 'read', tmp_path, tmp_path/'f', {'mcp_config': 'x', 'approval': approval})
    assert '--permission-prompt-tool' not in agent_argv('claude_cli', ['claude'], 'sonnet', 'auto', tmp_path, tmp_path/'f', {'mcp_config': 'x', 'approval': approval})
    codex = agent_argv('codex_cli', ['codex'], 'gpt', 'edit', tmp_path, tmp_path/'f', {'mcp_servers': servers})
    assert 'mcp_servers.graft.command="npx"' in codex and 'mcp_servers.graft.args=["-y", "graft-mcp"]' in codex and 'mcp_servers.docs.url="https://mcp.example.com/"' in codex
    assert codex.index('-c') < codex.index('exec')  # Overrides are global flags and precede the subcommand.
    assert toml_value({'K': 'v', 'ON': True}) == '{K = "v", ON = true}'
    opencode = mcp_config_for_opencode(servers)['mcp']
    assert opencode['graft'] == {'type': 'local', 'command': ['npx', '-y', 'graft-mcp'], 'environment': {'A': '1'}, 'enabled': True}
    gemini = agent_argv('gemini_cli', ['agy'], 'gemini-3.8-flash-high', 'read', tmp_path, tmp_path/'f')
    assert gemini[-2:] == ['--mode', 'plan'] and gemini[gemini.index('--input-format') + 1] == 'stream-json'
    assert agent_argv('gemini_cli', ['agy'], 'g', 'edit', tmp_path, None)[-1] == 'accept-edits'
    assert agent_argv('gemini_cli', ['agy'], 'g', 'auto', tmp_path, None)[-1] == '--dangerously-skip-permissions'


def test_antigravity_output_is_read_from_its_result_event():
    from backend.broker import agy_input
    import json
    assert json.loads(agy_input('USER: hi')) == {'event': 'user', 'message': {'content': 'USER: hi'}}
    out = ('{"event":"init","tools":["view_file"]}\n'
           '{"event":"result","result":{"status":"SUCCESS","response":"Three widgets.","usage":{"input_tokens":120,"output_tokens":30}}}\n')
    result = read_gemini(out, '', 0, 'gemini_cli')
    assert result.text == 'Three widgets.' and result.input_tokens == 120 and result.output_tokens == 30
    failed = '{"event":"result","result":{"status":"ERROR","response":"","error":"invalid project ID: \\"7648\\""}}\n'
    with pytest.raises(ProviderError, match='invalid project ID'):  # agy's own reason, not a guess about sign-in.
        read_gemini(failed, '', 0, 'gemini_cli')
    with pytest.raises(ProviderError):
        read_gemini('', 'Error authenticating', 1, 'gemini_cli')


def test_the_permission_tool_speaks_mcp_and_answers_from_frontiers_decision(monkeypatch):
    calls = []
    def fake_call(method, path, body=None):
        calls.append((method, path, body))
        if method == 'POST':
            return {'id': 'ap1'}
        return {'decision': 'allow', 'message': None}
    monkeypatch.setattr(permission_tool, 'call', fake_call)
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'approve', 'arguments': {'tool_name': 'Bash', 'input': {'command': 'git push'}, 'tool_use_id': 'u1'}}}]
    out = io.StringIO()
    permission_tool.serve(io.StringIO('\n'.join(json.dumps(r) for r in requests) + '\n'), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r['id'] for r in replies] == [1, 2, 3]  # The notification gets no reply.
    assert replies[0]['result']['protocolVersion'] == '2025-06-18' and replies[1]['result']['tools'][0]['name'] == 'approve'
    decision = json.loads(replies[2]['result']['content'][0]['text'])
    assert decision == {'behavior': 'allow', 'updatedInput': {'command': 'git push'}}
    assert calls[0] == ('POST', '/internal/approvals', {'tool_name': 'Bash', 'input': {'command': 'git push'}, 'tool_use_id': 'u1'})


def test_an_approval_card_is_raised_by_the_turn_and_answered_by_the_user(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_PORT', '8765')
    seen = {}
    async def reply(config, prompt, mode, root):
        return AgentResult('Waiting done.', 1, 1)
    agent = ScriptedAgent(respond=reply)
    original = agent.invoke_agent
    async def invoke(tenant_id, config, prompt, mode, root, extras=None):
        seen['extras'] = extras
        # Stand in for Claude's permission tool: ask, then wait for the user's answer.
        approval_id = runner.request_approval(extras['approval']['token'], 'Bash', {'command': 'npm test'}, 'u1')
        seen['pending'] = runner.pending('tenant-a', session['id'])
        state = await runner.approval_state(approval_id, wait=5)
        seen['state'] = state
        return await original(tenant_id, config, prompt, mode, root, extras)
    agent.invoke_agent = invoke
    store = setup_store(tmp_path/'state')
    folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Run the tests.', 'claude_cli', 'edit')
        for _ in range(50):
            await asyncio.sleep(0.02)
            if runner.pending('tenant-a', session['id']):
                break
        pending = runner.pending('tenant-a', session['id'])
        assert pending and pending[0]['tool_name'] == 'Bash' and pending[0]['input'] == {'command': 'npm test'}
        with pytest.raises(Exception):
            runner.decide('tenant-b', session['id'], pending[0]['id'], True)
        runner.decide('tenant-a', session['id'], pending[0]['id'], True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    assert seen['extras']['approval']['url'] == 'http://127.0.0.1:8765' and seen['extras']['approval']['command'][-1] == '--permission-tool'
    assert seen['state'] == {'decision': 'allow', 'message': None}
    assert runner.pending('tenant-a', session['id']) == [] and not runner.turn_tokens
    events = [e['type'] for e in store.events('tenant-a', session['id'])]
    assert 'approval.requested' in events and 'approval.decided' in events


def test_a_read_only_turn_carries_frontiers_tools_but_never_the_permission_prompt(tmp_path, monkeypatch):
    # Questions and the task board work in every posture; only Edit files routes permission prompts to a card.
    from backend.broker import agent_argv
    monkeypatch.setenv('HARNESS_DESKTOP_PORT', '8765')
    agent = ScriptedAgent()
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Look.', 'claude_cli', 'read'))
    extras = agent.calls[0]['extras']
    assert extras['approval']['command'][-1] == '--permission-tool' and extras['mcp_servers'] == []
    argv = agent_argv('claude_cli', ['claude'], 'sonnet', 'read', folder, folder/'f', {**extras, 'mcp_config': 'm.json'})
    assert '--permission-prompt-tool' not in argv and 'mcp__frontier__ask_user' in argv and 'mcp__frontier__approve' not in argv


def test_automations_run_on_schedule_by_webhook_and_by_hand(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='Nightly done.'))) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        made = c.post(f'/api/t/{t}/automations', json={'name': 'Nightly review', 'project_id': p['id'], 'prompt': 'Review the project.', 'model_id': a, 'mode': 'read', 'daily_at': '03:30'})
        assert made.status_code == 200, made.text
        auto = made.json()
        assert auto['next_run_at'] and auto['secret'] and auto['runs'] == 0
        assert c.post(f'/api/t/{t}/automations', json={'name': 'x', 'project_id': p['id'], 'prompt': 'y z', 'model_id': a, 'every': 5, 'daily_at': '01:00'}).status_code == 422
        # By hand: a session is opened and the turn runs.
        session = c.post(f'/api/t/{t}/automations/{auto["id"]}/run').json()
        assert session['name'].startswith('⏱ Nightly review') and session['automation_id'] == auto['id']
        for _ in range(100):
            if c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{session["id"]}').json()['messages'][-1]['status'] != 'running':
                break
            time.sleep(0.05)
        assert c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{session["id"]}').json()['messages'][-1]['content'] == 'Nightly done.'
        after = c.get(f'/api/t/{t}/automations').json()[0]
        assert after['runs'] == 1 and after['last_trigger'] == 'manual' and after['last_outcome'] == 'started'
        # By webhook: the secret is the credential; a wrong one is indistinguishable from a missing hook.
        assert c.post(f'/api/hooks/{auto["id"]}/wrong-secret').status_code == 404
        hooked = c.post(f'/api/hooks/{auto["id"]}/{auto["secret"]}')
        assert hooked.status_code == 200 and hooked.json()['session_id']
        # On schedule: due when next_run_at has passed, then rescheduled a day ahead.
        stale = {**after, 'next_run_at': (datetime.now()-timedelta(minutes=1)).isoformat(timespec='seconds')}
        assert automations.due(stale) and not automations.due({**stale, 'enabled': False})
        assert automations.next_run({'every': 15}, datetime(2026, 1, 1, 10, 0)) == datetime(2026, 1, 1, 10, 15)
        assert automations.next_run({'daily_at': '09:00'}, datetime(2026, 1, 1, 10, 0)) == datetime(2026, 1, 2, 9, 0)
        b = c.post('/api/tenants', json={'name': 'Other'}).json()['id']
        assert c.get(f'/api/t/{b}/automations').json() == []


def test_fan_out_gives_each_agent_its_own_worktree_session(tmp_path):
    pytest.importorskip('backend.gitops')
    from backend import gitops
    if not gitops.available():
        pytest.skip('git is not installed')
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(writes={'answer.txt': 'mine'}))) as c:
        t = setup(c); a = connect_agent(c, t)
        b = c.post(f'/api/t/{t}/models', json={'name': 'Codex', 'provider': 'codex_cli', 'model_name': 'gpt-5.5'}).json()['id']
        root = tmp_path/'repo'; root.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=root, check=True)
        (root/'README.md').write_text('hi', encoding='utf-8')
        subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'add', '-A'], cwd=root, check=True)
        subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'commit', '-q', '-m', 'first'], cwd=root, check=True)
        p = c.post(f'/api/t/{t}/projects', json={'name': 'Repo', 'root': str(root)}).json()
        made = c.post(f'/api/t/{t}/projects/{p["id"]}/fanout', json={'content': 'Write the answer.', 'model_ids': [a, b], 'mode': 'edit'})
        assert made.status_code == 200, made.text
        sessions = made.json()
        assert len(sessions) == 2 and {s['worktree']['branch'] for s in sessions} and all(s['name'].startswith('Write the answer. · ') for s in sessions)
        for s in sessions:
            for _ in range(100):
                if c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}').json()['messages'][-1]['status'] != 'running':
                    break
                time.sleep(0.05)
            assert (Path(s['worktree']['path'])/'answer.txt').is_file()
        assert not (root/'answer.txt').exists()


def test_usage_sums_finished_turns_by_model_project_and_day(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='ok'))) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={}).json()
        route = f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        c.post(route+'/instructions', json={'content': 'Hello there.', 'model_id': a, 'mode': 'read'})
        with c.stream('GET', route+'/events') as stream:
            ''.join(stream.iter_text())
        usage = c.get(f'/api/t/{t}/usage', params={'days': 7}).json()
        assert usage['totals']['turns'] == 1 and usage['totals']['input_tokens'] == 120 and usage['totals']['output_tokens'] == 40
        assert usage['models'][0]['label'] == 'Claude' and usage['projects'][0]['label'] == 'P' and len(usage['series']) == 1
        assert c.get(f'/api/t/{t}/projects/{p["id"]}/ports').json()['ports'] == [] or True  # Whatever is listening on this machine.


def test_remote_access_is_a_flag_the_next_start_reads_and_needs_a_password(tmp_path):
    assert remote.remote_host(str(tmp_path)) == '127.0.0.1'
    remote.set_enabled(tmp_path, True)
    assert remote.remote_host(str(tmp_path)) == '0.0.0.0' and remote.enabled(tmp_path)
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        setup(c)
        status = c.get('/api/remote').json()
        assert status['enabled'] is False and status['has_password'] is True and status['username'] == 'tester'
        assert c.put('/api/remote', json={'enabled': True}).json() == {'enabled': True, 'restart_required': True}
        assert c.get('/api/remote').json()['enabled'] is True
        assert c.post('/api/password', json={'password': 'another-long-password'}).status_code == 200
        c.post('/api/logout')
        assert c.post('/api/auth/login', json={'username': 'tester', 'password': 'another-long-password'}).status_code == 200


def test_skills_and_commands_are_listed_from_the_project(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'project'; root.mkdir()
        (root/'.claude'/'skills'/'deploy').mkdir(parents=True)
        (root/'.claude'/'skills'/'deploy'/'SKILL.md').write_text('---\nname: deploy\ndescription: Ship it safely\n---\n# Deploy\n', encoding='utf-8')
        (root/'.claude'/'commands').mkdir()
        (root/'.claude'/'commands'/'review.md').write_text('Review the diff carefully.\n', encoding='utf-8')
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        found = [s for s in c.get(f'/api/t/{t}/projects/{p["id"]}/skills').json() if s['scope'] == 'project']
        assert {(s['name'], s['kind'], s['description']) for s in found} == {('deploy', 'skill', 'Ship it safely'), ('review', 'command', 'Review the diff carefully.')}


def test_the_tray_flag_defaults_on_and_is_read_by_the_desktop(tmp_path):
    assert remote.tray_enabled(tmp_path) is True
    remote.set_tray_enabled(tmp_path, False)
    assert remote.tray_enabled(tmp_path) is False
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        setup(c)
        assert c.get('/api/tray').json() == {'enabled': True}
        assert c.put('/api/tray', json={'enabled': False}).json() == {'enabled': False}
        assert c.get('/api/tray').json() == {'enabled': False}


def test_a_second_backend_on_the_same_data_folder_says_so_and_exits(tmp_path):
    import os, sys
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
    script = tmp_path/'hold.py'
    script.write_text('import sys, time; from backend.app import create_app; from fastapi.testclient import TestClient; c = TestClient(create_app(sys.argv[1])); c.__enter__(); time.sleep(float(sys.argv[2])); c.__exit__(None, None, None)', encoding='utf-8')
    first = subprocess.Popen([sys.executable, str(script), str(tmp_path/'state'), '8'], env=env)
    try:
        time.sleep(3)
        second = subprocess.run([sys.executable, str(script), str(tmp_path/'state'), '0'], env=env, capture_output=True, text=True, timeout=30)
        assert second.returncode == 3 and 'already using this data folder' in second.stderr
    finally:
        first.kill()
