"""The projects dashboard and the skills catalog."""
import subprocess
from fastapi.testclient import TestClient
from backend import gitops, skill_catalog
from backend.app import create_app
from tests.harness import ScriptedAgent
from tests.test_api import setup, agent as connect_agent


def test_the_dashboard_shows_each_project_with_its_state_branch_board_and_last_activity(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='Added the login page.'))) as c:
        t = setup(c); a = connect_agent(c, t)
        busy, quiet = tmp_path/'busy', tmp_path/'quiet'
        busy.mkdir(); quiet.mkdir()
        if gitops.available():
            subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=busy, check=True)
            (busy/'x.txt').write_text('x', encoding='utf-8')
        p1 = c.post(f'/api/t/{t}/projects', json={'name': 'Busy', 'root': str(busy)}).json()
        p2 = c.post(f'/api/t/{t}/projects', json={'name': 'Quiet', 'root': str(quiet)}).json()
        c.post(f'/api/t/{t}/projects/{p1["id"]}/board', json={'title': 'Ship it', 'status': 'doing'})
        s = c.post(f'/api/t/{t}/projects/{p1["id"]}/sessions', json={'name': 'Login'}).json()
        c.post(f'/api/t/{t}/projects/{p1["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Add a login page.', 'model_id': a, 'mode': 'edit'})
        for _ in range(200):
            if c.get(f'/api/t/{t}/projects/{p1["id"]}/sessions/{s["id"]}').json()['messages'][-1]['status'] != 'running':
                break
            __import__('time').sleep(0.02)
        rows = c.get(f'/api/t/{t}/dashboard').json()
    assert [r['name'] for r in rows][0] == 'Busy'  # Most recent activity first.
    busy_row = rows[0]
    assert busy_row['state'] == 'done' and busy_row['sessions'] == 1 and busy_row['last_session']['snippet'] == 'Added the login page.'
    assert busy_row['board']['doing'] == 1 and busy_row['dev']['running'] is False and not busy_row['missing']
    if gitops.available():
        assert busy_row['git']['branch'] == 'main' and busy_row['git']['changes'] >= 1
    assert rows[1]['name'] == 'Quiet' and rows[1]['state'] is None and rows[1]['git'] is None


def test_catalog_skills_install_for_every_agent_and_edited_copies_are_left_alone(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        base = f'/api/t/{t}/projects/{p["id"]}/skill-catalog'
        listing = c.get(base).json()
        assert len(listing) == len(skill_catalog.CATALOG) and not any(s['installed'] for s in listing)
        assert c.post(f'{base}/write-tests').json()['installed']
        for folder in skill_catalog.FOLDERS:
            text = (root/folder/'write-tests'/'SKILL.md').read_text(encoding='utf-8')
            assert text.startswith('---\nname: write-tests\ndescription: ')
        found = c.get(f'/api/t/{t}/projects/{p["id"]}/skills').json()
        assert {(s['name'], s['provider']) for s in found if s['name'] == 'write-tests'} == {('write-tests', 'claude'), ('write-tests', 'codex')}
        (root/'.claude/skills/write-tests/SKILL.md').write_text('mine now', encoding='utf-8')
        assert next(s for s in c.get(base).json() if s['name'] == 'write-tests')['edited']
        assert c.delete(f'{base}/write-tests').status_code == 422 and (root/'.claude/skills/write-tests/SKILL.md').read_text(encoding='utf-8') == 'mine now'
        assert c.post(f'{base}/write-tests').status_code == 422
        c.post(f'{base}/review-changes')
        assert not c.delete(f'{base}/review-changes').json()['installed'] and not (root/'.agents/skills/review-changes').exists()
        assert c.post(f'{base}/nope').status_code == 404


def test_the_dashboard_sees_and_controls_a_projects_dev_server(tmp_path):
    import sys, time
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'site'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'Site', 'root': str(root)}).json()
        command = f'"{sys.executable}" -c "import time;print(\'Local: http://localhost:5199/\', flush=True);time.sleep(60)"'
        c.post(f'/api/t/{t}/projects/{p["id"]}/devserver', json={'action': 'start', 'command': command})
        for _ in range(100):
            dev = c.get(f'/api/t/{t}/dashboard').json()[0]['dev']
            if dev.get('port'):
                break
            time.sleep(0.1)
        assert dev['running'] and dev['port'] == 5199 and dev['command'] == command
        c.post(f'/api/t/{t}/projects/{p["id"]}/devserver', json={'action': 'stop'})
        assert c.get(f'/api/t/{t}/dashboard').json()[0]['dev']['running'] is False
