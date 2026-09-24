"""Using another machine's Frontier from this window: pairing, then every call passed through."""
import time
import httpx
from fastapi.testclient import TestClient
from backend import environments
from backend.app import create_app
from tests.harness import ScriptedAgent
from tests.test_api import setup, agent as connect_agent


def test_pair_with_another_machine_and_run_a_session_there(tmp_path, monkeypatch):
    other = create_app(str(tmp_path/'other'), ScriptedAgent(reply='Built on the other machine.'))
    monkeypatch.setattr(environments, 'client', lambda timeout=15.0: httpx.AsyncClient(transport=httpx.ASGITransport(app=other), timeout=timeout, follow_redirects=False))
    with TestClient(other) as there, TestClient(create_app(str(tmp_path/'here'), ScriptedAgent(reply='Built here.'))) as here:
        t_there = setup(there); model = connect_agent(there, t_there)
        root = tmp_path/'on-the-other-machine'; root.mkdir()
        there.post(f'/api/t/{t_there}/projects', json={'name': 'Render farm', 'root': str(root)})
        setup(here)
        assert here.post('/api/environments', json={'name': 'Studio', 'link': 'http://studio.local:8765/somewhere'}).status_code == 422
        code = there.post('/api/remote/pair').json()['token']
        env = here.post('/api/environments', json={'name': 'Studio', 'link': f'http://studio.local:8765/pair?code={code}'}).json()
        assert env['name'] == 'Studio' and env['url'] == 'http://studio.local:8765' and 'token' not in env
        assert here.post('/api/environments', json={'name': 'Again', 'link': f'http://studio.local:8765/pair?code={code}'}).status_code == 422  # Used once.
        [listed] = here.get('/api/environments').json()
        assert listed['reachable'] and listed['signed_in']
        via = f'/api/env/{env["id"]}'
        [tenant] = here.get(f'{via}/tenants').json()
        assert tenant['id'] == t_there  # The other machine's workspace, not this one's.
        [project] = here.get(f'{via}/t/{t_there}/projects').json()
        assert project['name'] == 'Render farm'
        s = here.post(f'{via}/t/{t_there}/projects/{project["id"]}/sessions', json={'name': 'Remote build'}).json()
        here.post(f'{via}/t/{t_there}/projects/{project["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Build it.', 'model_id': model, 'mode': 'edit'})
        for _ in range(200):
            reply = there.get(f'/api/t/{t_there}/projects/{project["id"]}/sessions/{s["id"]}').json()['messages'][-1]
            if reply['status'] != 'running':
                break
            time.sleep(0.02)
        assert reply['content'] == 'Built on the other machine.'
        assert here.get(f'{via}/t/{t_there}/projects/{project["id"]}/sessions/{s["id"]}').json()['messages'][-1]['content'] == 'Built on the other machine.'
        assert TestClient(here.app).get(f'{via}/tenants').status_code == 401  # Only this machine's signed-in user may pass calls through.
        here.delete(f'/api/environments/{env["id"]}')
        assert here.get(f'{via}/tenants').status_code == 404
