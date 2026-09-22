"""Working beside the agent: editing a file, searching the project, searching past conversations."""
from fastapi.testclient import TestClient
from backend.app import create_app
from tests.harness import ScriptedAgent
from tests.test_api import setup, agent as connect_agent


def test_a_file_can_be_edited_and_searched_inside_the_project_boundary(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'project'
        root.mkdir()
        (root/'app.py').write_text('def main():\n    print("Hello")\n', encoding='utf-8')
        (root/'notes.md').write_text('hello notes\n', encoding='utf-8')
        (root/'.env').write_text('SECRET=hello', encoding='utf-8')
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        base = f'/api/t/{t}/projects/{p["id"]}'
        saved = c.put(base+'/file', params={'path': 'app.py'}, json={'content': 'def main():\n    print("Changed")\n'})
        assert saved.status_code == 200 and (root/'app.py').read_text(encoding='utf-8') == 'def main():\n    print("Changed")\n'
        assert c.put(base+'/file', params={'path': '../outside.txt'}, json={'content': 'x'}).status_code in (403, 422)
        assert c.put(base+'/file', params={'path': '.env'}, json={'content': 'x'}).status_code == 422
        assert c.put(base+'/file', params={'path': 'missing.py'}, json={'content': 'x'}).status_code == 422
        found = c.get(base+'/search', params={'q': 'HELLO'}).json()
        assert {(h['path'], h['line']) for h in found['hits']} == {('notes.md', 1)} and found['files'] == 2
        assert c.get(base+'/search', params={'q': 'h'}).status_code == 422
        b = c.post('/api/tenants', json={'name': 'Other'}).json()['id']
        assert c.get(f'/api/t/{b}/projects/{p["id"]}/search', params={'q': 'hello'}).status_code == 403


def test_past_conversations_are_searchable_by_their_messages(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='The widget is in widgets.py.'))) as c:
        t = setup(c)
        a = connect_agent(c, t)
        root = tmp_path/'project'
        root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Widgets'}).json()
        route = f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        c.post(route+'/instructions', json={'content': 'Where is the widget?', 'model_id': a, 'mode': 'read'})
        with c.stream('GET', route+'/events') as stream:
            ''.join(stream.iter_text())
        hits = c.get(f'/api/t/{t}/search/sessions', params={'q': 'widgets.py'}).json()
        assert [h['session_id'] for h in hits] == [s['id']] and 'widgets.py' in hits[0]['snippet'] and hits[0]['role'] == 'assistant'
        assert c.get(f'/api/t/{t}/search/sessions', params={'q': 'nothing here'}).json() == []
        b = c.post('/api/tenants', json={'name': 'Other'}).json()['id']
        assert c.get(f'/api/t/{b}/search/sessions', params={'q': 'widget'}).json() == []
