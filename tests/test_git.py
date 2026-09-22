"""Git as Frontier uses it: status and staging, a worktree per session, and reverting a turn."""
import subprocess
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import gitops
from backend.agent import AgentRunner
from backend.app import create_app
from backend.projects import ProjectFiles
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup, agent as connect_agent

pytestmark = pytest.mark.skipif(not gitops.available(), reason='git is not installed')


def git(root, *args):
    return subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', *args], cwd=root, check=True, capture_output=True, text=True).stdout


def repo(tmp_path, name='work'):
    root = tmp_path/name
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.name', 'T')
    git(root, 'config', 'user.email', 't@x')
    (root/'notes.md').write_text('start\n', encoding='utf-8')
    (root/'logo.bin').write_bytes(b'\x00\x01\x02')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'first')
    return root


def make(tmp_path, agent):
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', root)
    return store, runner, project, session, root


@pytest.mark.asyncio
async def test_status_staging_and_commit_reflect_the_working_tree(tmp_path):
    root = repo(tmp_path)
    (root/'notes.md').write_text('start\nmore\n', encoding='utf-8')
    (root/'new.txt').write_text('hello', encoding='utf-8')
    current = await gitops.status(root)
    assert current['repo'] and current['branch'] == 'main' and current['has_head']
    assert {(e['path'], e['status'], e['staged']) for e in current['entries']} == {('notes.md', 'modified', False), ('new.txt', 'untracked', False)}
    assert '+more' in await gitops.diff(root, 'notes.md') and '+hello' in await gitops.diff(root, 'new.txt')
    await gitops.stage(root, ['notes.md'])
    staged = [e for e in (await gitops.status(root))['entries'] if e['staged']]
    assert [e['path'] for e in staged] == ['notes.md'] and '+more' in await gitops.diff(root, 'notes.md', staged=True)
    await gitops.stage(root, ['notes.md'], staged=False)
    assert not [e for e in (await gitops.status(root))['entries'] if e['staged']]
    await gitops.stage(root, ['notes.md', 'new.txt'])
    await gitops.commit(root, 'Add more notes')
    assert (await gitops.status(root))['entries'] == [] and 'Add more notes' in git(root, 'log', '--oneline')
    with pytest.raises(gitops.GitError):
        await gitops.commit(root, '   ')
    assert (await gitops.status(tmp_path))['repo'] is False


@pytest.mark.asyncio
async def test_a_turn_in_a_repository_is_checkpointed_and_can_be_put_back_exactly(tmp_path):
    agent = ScriptedAgent(reply='Changed things.', writes={'notes.md': 'rewritten\n', 'added.txt': 'new', 'logo.bin': 'text now'})
    store, runner, project, session, root = make(tmp_path, agent)
    session = await turn(runner, store, 'tenant-a', project, session, 'Change things.')
    reply = session['messages'][-1]
    assert reply['checkpoint'] and reply['checkpoint']['before'] != reply['checkpoint']['after']
    assert {'notes.md', 'added.txt'} <= {c['path'] for c in reply['changes']}
    assert git(root, 'status', '--porcelain').strip()  # The turn's edits are ordinary working-tree changes.
    session = await runner.revert('tenant-a', project['id'], session['id'], reply['id'])
    assert session['messages'][-1]['reverted_at']
    assert (root/'notes.md').read_text(encoding='utf-8') == 'start\n' and not (root/'added.txt').exists()
    assert (root/'logo.bin').read_bytes() == b'\x00\x01\x02'
    assert git(root, 'status', '--porcelain').strip() == ''  # Back to the commit: nothing left staged either.
    with pytest.raises(ValueError):
        await runner.revert('tenant-a', project['id'], session['id'], reply['id'])


@pytest.mark.asyncio
async def test_a_turn_outside_any_repository_reverts_from_its_recorded_changes(tmp_path):
    store = setup_store(tmp_path/'state')
    folder = tmp_path/'plain'
    folder.mkdir()
    (folder/'notes.md').write_text('start\n', encoding='utf-8')
    runner = AgentRunner(store, ScriptedAgent(writes={'notes.md': 'changed\n', 'extra.txt': 'x'}))
    project, session = open_project(store, runner, 'tenant-a', folder)
    session = await turn(runner, store, 'tenant-a', project, session, 'Change things.')
    assert session['messages'][-1]['checkpoint'] is None
    await runner.revert('tenant-a', project['id'], session['id'], session['messages'][-1]['id'])
    assert (folder/'notes.md').read_text(encoding='utf-8') == 'start\n' and not (folder/'extra.txt').exists()


def test_a_session_in_a_worktree_keeps_its_edits_off_the_project_folder(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(writes={'feature.txt': 'built'}))) as c:
        t = setup(c)
        a = connect_agent(c, t)
        root = repo(tmp_path)
        p = c.post(f'/api/t/{t}/projects', json={'name': 'Repo', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Add the feature', 'worktree': True}).json()
        assert s['worktree']['branch'].startswith('frontier/add-the-feature-')
        worktree = s['worktree']['path']
        assert 'Frontier Worktrees' in worktree and (tmp_path/'state').resolve() not in Path(worktree).resolve().parents
        route = f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        assert c.post(route+'/instructions', json={'content': 'Build it.', 'model_id': a, 'mode': 'edit'}).status_code == 200
        for _ in range(100):
            if c.get(route).json()['messages'][-1]['status'] != 'running':
                break
            time.sleep(0.05)
        assert c.get(route).json()['messages'][-1]['status'] == 'complete'
        assert (Path(worktree)/'feature.txt').is_file() and not (root/'feature.txt').exists()
        # Files, changes and git status for this session all read the worktree, not the project folder.
        assert [f['path'] for f in c.get(f'/api/t/{t}/projects/{p["id"]}/files', params={'session_id': s['id']}).json()] == ['feature.txt', 'logo.bin', 'notes.md']
        assert 'feature.txt' not in [f['path'] for f in c.get(f'/api/t/{t}/projects/{p["id"]}/files').json()]
        status = c.get(f'/api/t/{t}/projects/{p["id"]}/git', params={'session_id': s['id']}).json()
        assert status['branch'] == s['worktree']['branch'] and [e['path'] for e in status['entries']] == ['feature.txt']
        assert c.get(f'/api/t/{t}/projects/{p["id"]}/git').json()['branch'] == 'main'
        assert c.post(f'/api/t/{t}/projects/{p["id"]}/git/stage', json={'paths': ['feature.txt']}, params={'session_id': s['id']}).json()['entries'][0]['staged']
        assert c.post(f'/api/t/{t}/projects/{p["id"]}/git/commit', json={'message': 'Add the feature'}, params={'session_id': s['id']}).json()['entries'] == []
        assert 'Add the feature' in git(root, 'log', '--oneline', s['worktree']['branch'])
        assert c.post(f'/api/t/{t}/projects/{p["id"]}/git/stage', json={'paths': ['../outside']}, params={'session_id': s['id']}).status_code in (400, 422)
        b = c.post('/api/tenants', json={'name': 'Other'}).json()['id']
        assert c.get(f'/api/t/{b}/projects/{p["id"]}/git').status_code == 403
        dropped = c.delete(route+'/worktree').json()
        assert dropped['worktree'] is None and not Path(worktree).exists()
        assert s['worktree']['branch'] in git(root, 'branch', '--list', s['worktree']['branch'])
