"""Frontier as an MCP server, agents driving a browser, catching up on a project, and first-run detection."""
import asyncio
import io
import json
import subprocess
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import frontier_mcp, gitops, maintenance
from backend.agent import AgentRunner, browser_server
from backend.app import create_app
from backend.broker import AgentResult, agent_argv
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup, agent as connect_agent


class Through:
    """Frontier's MCP client, pointed at a TestClient instead of a port."""
    def __init__(self, client, token):
        self.client, self.token = client, token

    def __call__(self):
        frontier = frontier_mcp.Frontier.__new__(frontier_mcp.Frontier)
        frontier.port, frontier.token, frontier.tenant = '0', self.token, None
        def call(method, path, body=None, timeout=60):
            response = self.client.request(method, '/api'+path, json=body, headers={'X-Frontier-Token': self.token})
            if response.status_code >= 400:
                raise RuntimeError(response.json().get('detail'))
            return response.json()
        frontier.call = call
        return frontier


def rpc(factory, *requests):
    out = io.StringIO()
    frontier_mcp.serve(io.StringIO('\n'.join(json.dumps(r) for r in requests) + '\n'), out, factory)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_frontier_answers_as_an_mcp_server_and_drives_a_conversation(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='The widget lives in widget.py.')), client=('127.0.0.1', 5000)) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        c.post(f'/api/t/{t}/projects', json={'name': 'Widgets', 'root': str(root)})
        token = (tmp_path/'state'/'mcp-token').read_text(encoding='utf-8')
        factory = Through(TestClient(c.app, client=('127.0.0.1', 5000)), token)
        replies = rpc(factory,
                      {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                      {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                      {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'list_projects', 'arguments': {}}},
                      {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'send_message', 'arguments': {'project': 'widgets', 'message': 'Where is the widget?', 'agent': 'Claude', 'mode': 'read'}}},
                      {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'list_projects', 'arguments': {'workspace': 'nope'}}})
        names = [tool['name'] for tool in replies[1]['result']['tools']]
        assert {'list_projects', 'send_message', 'read_session', 'catch_up', 'project_changes'} <= set(names)
        assert json.loads(replies[2]['result']['content'][0]['text'])[0]['name'] == 'Widgets'
        sent = json.loads(replies[3]['result']['content'][0]['text'])
        assert sent['status'] == 'complete' and sent['content'] == 'The widget lives in widget.py.' and sent['agent'] == 'Claude'
        assert replies[4]['result']['isError'] and 'No workspace named nope' in replies[4]['result']['content'][0]['text']
        # The token is honoured from loopback only; a LAN client with it is still refused.
        remote = TestClient(c.app, client=('192.168.1.20', 5000))
        assert remote.get('/api/tenants', headers={'X-Frontier-Token': token}).status_code == 401
        assert TestClient(c.app, client=('127.0.0.1', 5000)).get('/api/tenants', headers={'X-Frontier-Token': 'wrong'}).status_code == 401


def test_the_agent_gets_a_browser_when_the_project_allows_it(tmp_path):
    project = {'root': str(tmp_path), 'agent_browser': True}
    server = browser_server(project)
    assert server['name'] == 'frontier-browser' and '@playwright/mcp@latest' in server['args'] and '--headless' in server['args']
    assert str(Path(tmp_path)/'.frontier'/'browser') in server['args']
    assert '--headless' not in browser_server({**project, 'agent_browser_visible': True})['args']
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    agent = ScriptedAgent()
    runner = AgentRunner(store, agent)
    p, session = open_project(store, runner, 'tenant-a', folder)
    p['agent_browser'] = True; store.put('tenant-a', 'projects', p)
    asyncio.run(turn(runner, store, 'tenant-a', p, session, 'Check the page.', 'claude_cli', 'edit'))
    assert any(s['name'] == 'frontier-browser' for s in agent.calls[-1]['extras']['mcp_servers'])
    assert 'frontier-browser tools' in agent.calls[-1]['prompt']
    (folder/'.frontier'/'browser').mkdir(parents=True)
    (folder/'.frontier'/'browser'/'page.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    with TestClient(create_app(str(tmp_path/'state2'), ScriptedAgent())) as c:
        t = setup(c)
        pr = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(folder)}).json()
        shots = c.get(f'/api/t/{t}/projects/{pr["id"]}/browser-shots').json()
        assert [s['name'] for s in shots] == ['page.png']
        assert c.get(f'/api/t/{t}/projects/{pr["id"]}/browser-shot', params={'name': 'page.png'}).content.startswith(b'\x89PNG')
        assert c.get(f'/api/t/{t}/projects/{pr["id"]}/browser-shot', params={'name': '../secret.png'}).status_code in (400, 422)


def test_catch_up_gathers_what_happened_since_a_moment_and_summarises_it(tmp_path):
    prompts = []
    async def respond(config, prompt, mode, root):
        prompts.append((mode, prompt))
        if 'coming back to this project' in prompt:
            return AgentResult('**Since then:** Claude added notes.', 1, 1)
        (Path(root)/'notes.md').write_text('new', encoding='utf-8')
        return AgentResult('Added notes.', 1, 1)
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(respond=respond))) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        if gitops.available():
            subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=root, check=True)
            (root/'a.txt').write_text('a', encoding='utf-8')
            subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'add', '-A'], cwd=root, check=True)
            subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'commit', '-q', '-m', 'Start the project'], cwd=root, check=True)
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        before = '2000-01-01T00:00:00Z'
        empty = c.post(f'/api/t/{t}/projects/{p["id"]}/catchup', json={'since': '2999-01-01T00:00:00Z', 'summarize': True}).json()
        assert empty['counts']['turns'] == 0 and empty['summary'] is None and not prompts
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Notes'}).json()
        c.post(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Add notes.', 'model_id': a, 'mode': 'edit'})
        for _ in range(100):
            if c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}').json()['messages'][-1]['status'] != 'running':
                break
            time.sleep(0.05)
        facts = c.post(f'/api/t/{t}/projects/{p["id"]}/catchup', json={'since': before}).json()
        assert facts['counts']['turns'] == 1 and facts['turns'][0]['asked'] == 'Add notes.' and facts['files'] == ['notes.md']
        if gitops.available():
            assert facts['commits'][0]['subject'] == 'Start the project'
        summary = c.post(f'/api/t/{t}/projects/{p["id"]}/catchup', json={'since': before, 'summarize': True}).json()
        assert summary['summary'].startswith('**Since then:**') and prompts[-1][0] == 'read' and 'Add notes.' in prompts[-1][1]


def test_first_run_detection_lists_every_tool_with_its_install_command(monkeypatch):
    async def probe(provider, env=None):
        return '2.1.280 (Claude Code)' if provider == 'claude_cli' else (_ for _ in ()).throw(maintenance.ProviderError('missing'))
    monkeypatch.setattr(maintenance, 'resolve_cli', lambda provider: ['C:/bin/claude.exe'] if provider == 'claude_cli' else (_ for _ in ()).throw(maintenance.ProviderError('missing')))
    monkeypatch.setattr(maintenance, 'probe_cli', probe)
    rows = {r['provider']: r for r in asyncio.run(maintenance.detect())}
    assert rows['claude_cli']['installed'] and rows['claude_cli']['version'] == '2.1.280' and 'sonnet' in rows['claude_cli']['models']
    assert not rows['codex_cli']['installed'] and rows['codex_cli']['install'] == 'npm install -g @openai/codex' and rows['codex_cli']['signin'] == 'codex login'
    assert set(rows) == {'claude_cli', 'codex_cli', 'opencode_cli', 'gemini_cli'}


def test_the_browser_tools_never_wait_on_an_approval_card(tmp_path):
    server = browser_server({'root': str(tmp_path), 'agent_browser': True})
    for mode in ('read', 'edit', 'auto'):
        argv = agent_argv('claude_cli', ['claude'], 'sonnet', mode, tmp_path, tmp_path/'f', {'mcp_servers': [server], 'mcp_config': 'm.json', 'approval': {'command': ['x'], 'url': 'u', 'token': 't'}})
        assert argv[argv.index('--allowedTools')+1] == 'mcp__frontier-browser'
    assert '--allowedTools' not in agent_argv('claude_cli', ['claude'], 'sonnet', 'edit', tmp_path, tmp_path/'f', {'mcp_servers': []})


def test_agents_can_use_the_users_own_browser_with_its_token_kept_secret(tmp_path):
    project = {'root': str(tmp_path), 'agent_browser': True, 'agent_browser_mode': 'mine', 'agent_browser_channel': 'msedge'}
    server = browser_server(project, token='tok-123')
    assert '--extension' in server['args'] and server['args'][server['args'].index('--browser')+1] == 'msedge' and '--isolated' not in server['args']
    assert server['env'] == {'PLAYWRIGHT_MCP_EXTENSION_TOKEN': 'tok-123'} and server['trusted']
    # Codex runs the browser's tools unasked, as it does Frontier's own; otherwise `exec` would refuse every one.
    argv = agent_argv('codex_cli', ['codex'], 'gpt-6-sol', 'edit', tmp_path, tmp_path/'f', {'mcp_servers': [server]})
    assert 'mcp_servers.frontier-browser.default_tools_approval_mode="approve"' in argv
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        saved = c.put(f'/api/t/{t}/projects/{p["id"]}/settings', json={'agent_browser': True, 'agent_browser_mode': 'mine', 'agent_browser_token': 'tok-123'}).json()
        assert 'agent_browser_token' not in saved and saved['agent_browser_token_set'] is True
        listed = c.get(f'/api/t/{t}/projects').json()[0]
        assert 'agent_browser_token' not in listed and listed['agent_browser_token_set']
        stored = c.app.state.store.get(t, 'projects', p['id'])
        assert stored['agent_browser_token'] != 'tok-123' and c.app.state.store.decrypt(stored['agent_browser_token']) == 'tok-123'
        extras = c.app.state.runner.extras(t, 'turn-token', 'edit', stored)
        browser = next(s for s in extras['mcp_servers'] if s['name'] == 'frontier-browser')
        assert browser['env']['PLAYWRIGHT_MCP_EXTENSION_TOKEN'] == 'tok-123'
        assert not c.put(f'/api/t/{t}/projects/{p["id"]}/settings', json={'agent_browser_token': ''}).json()['agent_browser_token_set']
