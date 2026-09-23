"""Cross-agent fallback, archive on merge, worktree cleanup, clone from a URL, the Python environment note."""
import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import automations, gitops
from backend.agent import AgentRunner, build_prompt
from backend.app import create_app
from backend.broker import AgentResult, ProviderError
from backend.projects import ProjectFiles
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup

needs_git = pytest.mark.skipif(not gitops.available(), reason='git is not installed')


def test_a_spent_agent_hands_the_turn_to_another_agent(tmp_path):
    calls = []
    async def respond(config, prompt, mode, root):
        calls.append((config['id'], prompt))
        if config['id'] == 'claude_cli':
            raise ProviderError('The Claude Code subscription has no usage left in its current window.', retryable=True, exhausted=True)
        (Path(root)/'done.txt').write_text('by ' + config['name'], encoding='utf-8')
        return AgentResult('Finished for you.', 5, 5)
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', folder)
    session = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Do the thing.', 'claude_cli', 'edit'))
    reply = session['messages'][-1]
    assert reply['status'] == 'complete' and reply['content'] == 'Finished for you.'
    assert reply['model_id'] != 'claude_cli' and reply['switched_from'] is None  # No earlier agent in this conversation to be switched from.
    assert reply['routing']['attempts'][0] == {'id': 'claude_cli', 'name': 'Claude', 'outcome': 'out of usage'} and reply['routing']['fallbacks'] == 1
    assert 'objective' in calls[1][1].lower() or 'Continue the task' in calls[1][1]  # The second agent got a handoff.
    events = [e['message'] for e in store.events('tenant-a', session['id'])]
    assert any('out of usage' in m and 'continues this turn' in m for m in events)


def test_with_no_other_agent_a_spent_subscription_still_fails_the_turn(tmp_path):
    async def respond(config, prompt, mode, root):
        raise ProviderError('spent', retryable=True, exhausted=True)
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', folder)
    session = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Do the thing.', 'claude_cli', 'read'))
    reply = session['messages'][-1]
    assert reply['status'] == 'failed' and reply['routing'].get('fallbacks') == 2  # Every agent was tried, each spent.


def test_the_python_environment_is_detected_and_told_to_the_agent(tmp_path):
    root = tmp_path/'proj'; (root/'.venv'/'Scripts').mkdir(parents=True); (root/'.venv'/'bin').mkdir(parents=True)
    (root/'.venv'/'Scripts'/'python.exe').write_bytes(b''); (root/'.venv'/'bin'/'python').write_bytes(b'')
    env = ProjectFiles.python_env(root)
    assert env['kind'] == 'venv' and 'python' in env['python'].lower() and env['activate']
    assert ProjectFiles.python_env(tmp_path) is None
    (tmp_path/'uv.lock').write_text('', encoding='utf-8')
    assert ProjectFiles.python_env(tmp_path)['kind'] == 'uv'
    prompt = build_prompt([{'role': 'user', 'content': 'hi'}], False, environment=env['note'])
    assert 'PROJECT ENVIRONMENT' in prompt and '.venv' in prompt


@needs_git
def test_clone_from_a_url_makes_a_managed_project(tmp_path):
    origin = tmp_path/'origin'; origin.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=origin, check=True)
    (origin/'README.md').write_text('# Cloned\n', encoding='utf-8')
    subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'add', '-A'], cwd=origin, check=True)
    subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'commit', '-q', '-m', 'first'], cwd=origin, check=True)
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        assert c.post(f'/api/t/{t}/projects/clone', json={'url': 'not a url'}).status_code == 422
        made = c.post(f'/api/t/{t}/projects/clone', json={'url': 'file:///' + str(origin).replace('\\', '/')})
        assert made.status_code == 422  # file:// is not an accepted scheme.
        made = c.post(f'/api/t/{t}/projects/clone', json={'url': 'https://127.0.0.1:1/nothing/here.git', 'name': 'Nope'})
        assert made.status_code in (400, 422) and 'Clone failed' in made.text
        assert c.get(f'/api/t/{t}/projects').json() == []  # A failed clone leaves no project behind.


@pytest.mark.asyncio
async def test_worktrees_of_archived_sessions_are_removed_after_the_set_days(tmp_path):
    if not gitops.available():
        pytest.skip('git is not installed')
    root = tmp_path/'repo'; root.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=root, check=True)
    (root/'a.txt').write_text('a', encoding='utf-8')
    subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'add', '-A'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', 'commit', '-q', '-m', 'first'], cwd=root, check=True)
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        p = c.post(f'/api/t/{t}/projects', json={'name': 'Repo', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Branch', 'worktree': True}).json()
        wt = Path(s['worktree']['path'])
        assert wt.is_dir()
        c.patch(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}', json={'archived': True})
        store, runner = c.app.state.store, c.app.state.runner
        c.put(f'/api/tenants/{t}', json={'name': 'Workspace A', 'worktree_cleanup_days': 0})
        await automations.housekeeping(store, runner, t)
        after = store.get(t, 'sessions', s['id'])
        assert after['worktree'] is None and after['worktree_removed'] == s['worktree']['branch'] and not wt.exists()
        assert s['worktree']['branch'] in subprocess.run(['git', 'branch', '--list', s['worktree']['branch']], cwd=root, capture_output=True, text=True).stdout


def test_a_merged_pull_request_archives_its_session(tmp_path, monkeypatch):
    from backend import pullrequests
    async def merged(root):
        return {'number': 9, 'title': 'Done', 'url': 'https://github.com/o/r/pull/9', 'state': 'merged', 'draft': False, 'review': 'approved', 'checks': {'success': 1, 'failure': 0, 'pending': 0}}
    monkeypatch.setattr(pullrequests, 'available', lambda: True)
    monkeypatch.setattr(pullrequests, 'current', merged)
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Feature'}).json()
        status = c.get(f'/api/t/{t}/projects/{p["id"]}/pr', params={'session_id': s['id']}).json()
        assert status['pr']['state'] == 'merged'
        after = c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}').json()
        assert after['archived'] is True and after['archived_reason'] == 'pull request #9 merged' and after['pull_request']['number'] == 9
