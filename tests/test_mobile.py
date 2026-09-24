"""A phone: signing in by a one-time QR link, and push notifications through ntfy."""
import time
from fastapi.testclient import TestClient
from backend import push
from backend.app import create_app
from tests.harness import ScriptedAgent
from tests.test_api import setup, agent as connect_agent


def test_a_pairing_link_signs_a_phone_in_once_and_only_while_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_PORT', '8765')
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as desktop:
        setup(desktop)
        paired = desktop.post('/api/remote/pair').json()
        assert paired['expires_in'] == 600 and all(u.startswith('http://') and ':8765/pair?code=' in u for u in paired['urls'])
        phone = TestClient(desktop.app)
        assert phone.get('/api/tenants').status_code == 401
        landing = phone.get(f'/pair?code={paired["token"]}', follow_redirects=False)
        assert landing.status_code == 303 and landing.headers['location'] == '/'
        assert phone.get('/api/tenants').status_code == 200  # Signed in by the link alone.
        again = TestClient(desktop.app)
        assert again.get(f'/pair?code={paired["token"]}', follow_redirects=False).status_code == 410  # Used once.
        assert again.get('/pair?code=guess', follow_redirects=False).status_code == 410
        assert TestClient(desktop.app).post('/api/remote/pair').status_code == 401  # Only a signed-in user can make one.


def test_push_notifications_publish_to_a_private_topic_when_a_turn_finishes(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(push, 'post', lambda server, payload: sent.append((server, payload)) or 200)
    class Now:  # Deliver inline, so the test sees it at once.
        def __init__(self, target, daemon=True): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(push, 'threading', __import__('types').SimpleNamespace(Thread=Now))
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='All tests pass now.'))) as c:
        t = setup(c); a = connect_agent(c, t)
        assert c.get('/api/push').json()['enabled'] is False
        settings = c.put('/api/push', json={'enabled': True}).json()
        topic = settings['topic']
        assert topic.startswith('frontier-') and len(topic) > 25 and settings['subscribe_url'] == f'https://ntfy.sh/{topic}'
        assert c.put('/api/push', json={'server': 'not a url'}).status_code == 422
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'Shop', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Fix checkout'}).json()
        c.post(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Fix the tests.', 'model_id': a, 'mode': 'edit'})
        for _ in range(200):
            if sent:
                break
            time.sleep(0.02)
        server, payload = sent[-1]
        assert server == 'https://ntfy.sh' and payload['topic'] == topic and payload['title'] == 'Claude finished in Shop'
        assert payload['message'] == 'Fix checkout'  # The reply stays off the push service unless asked for.
        c.put('/api/push', json={'details': True, 'events': ['failed']})
        c.post(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Again.', 'model_id': a, 'mode': 'edit'})
        time.sleep(0.5)
        assert len(sent) == 1  # Finished turns are no longer among the chosen events.
        new = c.put('/api/push', json={'new_topic': True}).json()
        assert new['topic'] != topic and new['events'] == ['failed'] and new['details'] is True
        assert c.post('/api/push/test').json()['sent'] and sent[-1][1]['title'] == 'Frontier test notification'


def test_several_logins_for_one_tool_pool_into_one_allowance(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from backend import maintenance
    home = tmp_path/'home'; (home/'.claude').mkdir(parents=True)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False); monkeypatch.delenv('CODEX_HOME', raising=False)
    (home/'.claude'/'.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'personal'}}), encoding='utf-8')
    usage = {'personal': [('session', 80, '2026-09-24T10:00:00Z'), ('weekly_all', 60, '2026-09-27T00:00:00Z')],
             'work': [('session', 20, '2026-09-24T08:00:00Z'), ('weekly_all', 40, '2026-09-28T00:00:00Z')]}
    def fetch(url, headers):
        token = headers['Authorization'].split()[-1]
        return {'limits': [{'kind': k, 'percent': p, 'resets_at': r} for k, p, r in usage[token]]}, None, None
    monkeypatch.setattr(maintenance, 'fetch_json', fetch); monkeypatch.setattr(maintenance, 'LIMITS_CACHE', {})
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        c.post(f'/api/t/{t}/models', json={'name': 'Claude (work)', 'provider': 'claude_cli', 'model_name': 'sonnet', 'account': 'work'})
        work_home = tmp_path/'state'/'subscriptions'/t/'claude_cli'/'work'; work_home.mkdir(parents=True, exist_ok=True)
        (work_home/'.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'work'}}), encoding='utf-8')
        [claude] = c.get(f'/api/t/{t}/providers/limits').json()
    assert [l['name'] for l in claude['logins']] == ['Default sign-in', 'work']
    pooled = {l['label']: l for l in claude['limits']}
    assert pooled['Current session']['percent'] == 50 and pooled['Current session']['logins'] == 2
    assert pooled['Current session']['resets_at'] == '2026-09-24T08:00:00Z'  # The first window to reset.
    assert pooled['Week, all models']['percent'] == 50
    # One login refused: the pool is the one that answered, and the refusal stays on its own row.
    monkeypatch.setattr(maintenance, 'LIMITS_CACHE', {})
    monkeypatch.setattr(maintenance, 'fetch_json', lambda url, headers: (None, 'rate limited', 60) if 'work' in headers['Authorization'] else fetch(url, headers))
    [solo] = maintenance.subscription_limits(logins=[{'provider': 'claude_cli', 'name': 'Default sign-in', 'home': None}, {'provider': 'claude_cli', 'name': 'work', 'home': str(work_home)}])
    assert solo['error'] is None and {l['label']: l['percent'] for l in solo['limits']}['Current session'] == 80 and solo['logins'][1]['error'] == 'rate limited'


def test_pairing_offers_addresses_a_phone_can_reach_and_leaves_out_vpn_and_virtual_ones():
    from backend import remote
    assert remote.classify('192.168.1.33', 'Ethernet 2', 'Realtek PCIe 2.5GbE Family Controller') == 'lan'
    assert remote.classify('100.120.202.35', 'Tailscale', 'Tailscale Tunnel') == 'tailnet'
    assert remote.classify('10.2.0.2', 'ProtonVPN', 'WireGuard Tunnel') == 'vpn'
    assert remote.classify('192.168.56.1', 'Ethernet 3', 'VirtualBox Host-Only Ethernet Adapter') is None
    assert remote.classify('172.26.32.1', 'vEthernet (WSL (Hyper-V firewall))', 'Hyper-V Virtual Ethernet Adapter') is None
    assert remote.classify('169.254.55.35', 'Wi-Fi 5') is None
