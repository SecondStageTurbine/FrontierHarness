"""Frontier's sandbox: low-integrity file confinement on Windows, and the network filter."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
import pytest
from backend import sandbox
from backend.agent import sandbox_note


def test_host_patterns_and_policy_defaults():
    rules = ['*.anthropic.com', 'pypi.org']
    assert sandbox.allowed('api.anthropic.com', rules) and sandbox.allowed('anthropic.com', rules) and sandbox.allowed('PyPI.org.', rules)
    assert not sandbox.allowed('evil-anthropic.com', rules) and not sandbox.allowed('files.pythonhosted.org', rules)
    assert sandbox.allowed('localhost', []) and sandbox.allowed('127.0.0.1', [])
    assert sandbox.policy({}) == {'files': False, 'network': 'open', 'allow': []}
    assert sandbox.policy({'sandbox': {'network': 'wide open'}})['network'] == 'open'
    assert sandbox.tool_hosts([{'url': 'https://mcp.example.com/sse'}, {'command': 'cmd', 'args': ['/c', 'npx', '-y', 'x']}])[:2] == ['mcp.example.com', 'registry.npmjs.org']
    note = sandbox_note({'files': True, 'network': 'allowlist', 'allow': ['pypi.org']})
    assert 'only inside the project folder' in note and 'pypi.org' in note and sandbox_note({'files': False, 'network': 'open', 'allow': []}) is None


def test_the_network_filter_passes_allowed_hosts_and_records_the_rest():
    async def scenario():
        async def origin(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            await writer.drain(); writer.close()
        upstream = await asyncio.start_server(origin, '127.0.0.1', 0)
        up_port = upstream.sockets[0].getsockname()[1]
        net = sandbox.NetworkFilter(['*.anthropic.com'])
        port = await net.start()
        async def ask(line):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(line); await writer.drain()
            data = await reader.read(4096); writer.close()
            return data
        allowed = await ask(f'GET http://127.0.0.1:{up_port}/x HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n'.encode())
        refused = await ask(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n')
        refused_plain = await ask(b'GET http://tracker.example.net/ HTTP/1.1\r\nHost: tracker.example.net\r\n\r\n')
        await net.stop(); upstream.close()
        return allowed, refused, refused_plain, net.blocked
    allowed, refused, refused_plain, blocked = asyncio.run(scenario())
    assert allowed.endswith(b'ok') and refused.startswith(b'HTTP/1.1 403') and refused_plain.startswith(b'HTTP/1.1 403')
    assert blocked == ['example.com', 'tracker.example.net']


@pytest.mark.skipif(os.name != 'nt', reason='Low integrity is a Windows mechanism.')
def test_a_confined_process_writes_only_where_frontier_labelled(tmp_path):
    project = tmp_path/'project'; project.mkdir()
    elsewhere = tmp_path/'elsewhere'; elsewhere.mkdir()
    assert sandbox.label(project)
    script = ('import sys\nfrom pathlib import Path\n'
              'for target in sys.argv[1:]:\n'
              '    try:\n        Path(target).write_text("x"); print("wrote", Path(target).parent.name)\n'
              '    except OSError:\n        print("denied", Path(target).parent.name)\n')
    out = subprocess.run([*sandbox.launcher(), sys.executable, '-c', script, str(project/'a.txt'), str(elsewhere/'b.txt')],
                         capture_output=True, text=True, timeout=60).stdout
    assert 'wrote project' in out and 'denied elsewhere' in out
    assert (project/'a.txt').exists() and not (elsewhere/'b.txt').exists()


def test_codex_hands_its_own_sandbox_to_frontiers_and_telemetry_refusals_stay_quiet(tmp_path):
    from backend.broker import agent_argv
    confined = agent_argv('codex_cli', ['codex'], 'gpt-6-sol', 'edit', tmp_path, tmp_path/'f', {'outer_sandbox': True})
    assert confined[confined.index('--sandbox')+1] == 'danger-full-access'
    plain = agent_argv('codex_cli', ['codex'], 'gpt-6-sol', 'edit', tmp_path, tmp_path/'f', {})
    assert plain[plain.index('--sandbox')+1] == 'workspace-write'
    net = sandbox.NetworkFilter([])
    net.refuse('http-intake.logs.us5.datadoghq.com'); net.refuse('example.com')
    assert net.blocked == ['example.com']


def test_a_named_gemini_login_gets_its_own_home_and_signs_in(tmp_path):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from backend.broker import account_env
    from tests.harness import ScriptedAgent
    from tests.test_api import setup
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        model = c.post(f'/api/t/{t}/models', json={'name': 'Gemini (work)', 'provider': 'gemini_cli', 'model_name': 'gemini-3.8-flash-high', 'account': 'work'}).json()
        signin = c.post(f'/api/t/{t}/models/{model["id"]}/signin')
        assert signin.status_code == 200 and signin.json()['variable'] == 'USERPROFILE' and signin.json()['command'].endswith('agy')
    home = tmp_path/'state'/'subscriptions'/t/'gemini_cli'/'work'
    assert home.is_dir()
    assert sandbox.tool_state('gemini_cli', str(home)) == [home] and sandbox.tool_state('claude_cli')[0].name == '.claude'


def test_agent_calls_outside_a_turn_carry_the_projects_protections(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from tests.harness import ScriptedAgent
    from tests.test_api import setup, agent as connect_agent
    agent = ScriptedAgent(reply='- Uses tabs.')
    with TestClient(create_app(str(tmp_path/'state'), agent)) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        c.put(f'/api/t/{t}/projects/{p["id"]}/settings', json={'sandbox': {'network': 'allowlist', 'allow': ['pypi.org']}})
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Work'}).json()
        c.post(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Format the code.', 'model_id': a, 'mode': 'edit'})
        for _ in range(200):
            if c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}').json()['messages'][-1]['status'] != 'running':
                break
            time.sleep(0.02)
        c.post(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}/remember')
    helper = agent.calls[-1]
    assert helper['mode'] == 'read' and helper['extras']['protect_env'] is True
    assert helper['extras']['sandbox'] == {'files': False, 'network': 'allowlist', 'allow': ['pypi.org']}
