from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import Store
from tests.harness import ScriptedAgent


def setup(client):
    assert client.post('/api/auth/setup', json={'username': 'tester', 'password': 'a-secure-test-password'}).status_code == 200
    return client.post('/api/tenants', json={'name': 'Workspace A'}).json()['id']


def test_delete_removes_the_chat_and_its_team_workers_only(tmp_path):
    state = tmp_path/'state'
    with TestClient(create_app(str(state), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'work'
        root.mkdir()
        (root/'notes.txt').write_text('keep me', encoding='utf-8')
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        base = f'/api/t/{t}/projects/{p["id"]}/sessions'
        doomed = c.post(base, json={}).json()
        kept = c.post(base, json={}).json()
        store = Store(str(state))
        worker = store.put(t, 'sessions', {**c.post(base, json={}).json(), 'team_parent': doomed['id']})

        response = c.delete(f'{base}/{doomed["id"]}')
        assert response.status_code == 200
        assert set(response.json()['deleted']) == {doomed['id'], worker['id']}

        # A missing session answers like a foreign one, so ids cannot be probed.
        assert c.get(f'{base}/{doomed["id"]}').status_code == 403
        assert c.get(f'{base}/{worker["id"]}').status_code == 403
        assert [s['id'] for s in c.get(base).json()] == [kept['id']]
        assert (root/'notes.txt').read_text(encoding='utf-8') == 'keep me'


def test_another_workspace_cannot_delete(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        a = setup(c)
        b = c.post('/api/tenants', json={'name': 'Workspace B'}).json()['id']
        root = tmp_path/'work'
        root.mkdir()
        p = c.post(f'/api/t/{a}/projects', json={'name': 'P', 'root': str(root)}).json()
        s = c.post(f'/api/t/{a}/projects/{p["id"]}/sessions', json={}).json()
        assert c.delete(f'/api/t/{b}/projects/{p["id"]}/sessions/{s["id"]}').status_code == 403
        assert c.get(f'/api/t/{a}/projects/{p["id"]}/sessions/{s["id"]}').status_code == 200
