import json
from fastapi.testclient import TestClient
from backend.app import create_app
from tests.test_engine import ScriptedBroker, APPROVED, REJECTED

def setup(client):
    assert client.post('/api/auth/setup',json={'username':'tester','password':'a-secure-test-password'}).status_code==200
    return client.post('/api/tenants',json={'name':'Workspace A'}).json()['id']

def model(client,t):
    return client.post(f'/api/t/{t}/models',json={'name':'Test endpoint','provider':'custom_openai','model_name':'test-model','base_url':'http://127.0.0.1:9999/v1','api_key':'secret-test-value'}).json()['id']

def workflow(client,t,m):
    return client.post(f'/api/t/{t}/workflows',json={'name':'API workflow','objective':'Create a tested, isolated backend.','stages':[{'id':r,'role':r,'model_id':m} for r in ['discussion','planning','building','review']]}).json()

def test_auth_required_and_first_setup_only(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedBroker())) as c:
        assert c.get('/api/tenants').status_code==401
        setup(c)
        assert c.post('/api/auth/setup',json={'username':'other','password':'other-strong-password'}).status_code==409
        c.post('/api/logout')
        assert c.get('/api/tenants').status_code==401

def test_credentials_are_encrypted_and_never_returned(tmp_path):
    app=create_app(str(tmp_path),ScriptedBroker())
    with TestClient(app) as c:
        t=setup(c);m=model(c,t)
        assert 'secret-test-value' not in c.get(f'/api/t/{t}/models').text
        assert 'encrypted_key' not in c.get(f'/api/t/{t}/models').text
        assert 'secret-test-value' not in (tmp_path/'harness.db').read_bytes().decode('utf-8',errors='ignore')
        assert c.get(f'/api/t/{t}/models').json()[0]['key_hint']=='••••alue'

def test_foreign_ids_blocked_across_endpoints(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedBroker())) as c:
        a=setup(c);b=c.post('/api/tenants',json={'name':'Workspace B'}).json()['id'];m=model(c,a);w=workflow(c,a,m)
        assert c.get(f'/api/t/{b}/workflows').json()==[]
        assert c.get(f'/api/t/{b}/workflows/{w["id"]}').status_code==403
        assert c.post(f'/api/t/{b}/workflows/{w["id"]}/runs').status_code==403
        assert c.delete(f'/api/t/{b}/models/{m}').status_code==403
        bad=workflow(c,b,m)
        assert bad['code']=='TenantIsolationViolationException'

def test_sse_replays_persisted_events_and_tenant_access(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedBroker([REJECTED,APPROVED]))) as c:
        t=setup(c);m=model(c,t);w=workflow(c,t,m);r=c.post(f'/api/t/{t}/workflows/{w["id"]}/runs').json()
        with c.stream('GET',f'/api/t/{t}/runs/{r["id"]}/events') as stream:
            text=''.join(stream.iter_text())
        assert 'workflow.replanning' in text and 'workflow.complete' in text and 'event: done' in text
        detail=c.get(f'/api/t/{t}/runs/{r["id"]}').json()
        assert detail['status']=='complete' and detail['iteration']==2
        assert len(detail['stages'])==7
        b=c.post('/api/tenants',json={'name':'B'}).json()['id']
        assert c.get(f'/api/t/{b}/runs/{r["id"]}/events').status_code==403
        assert c.get(f'/api/t/{b}/runs/{r["id"]}').status_code==403
        last=detail['events'][-1]['seq']
        replay=c.get(f'/api/t/{t}/runs/{r["id"]}/events',headers={'Last-Event-ID':str(last)})
        assert 'workflow.started' not in replay.text and 'event: done' in replay.text

def test_cross_origin_blocked(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedBroker())) as c:
        assert c.post('/api/auth/setup',json={'username':'tester','password':'secure-password-for-test'},headers={'Origin':'https://attacker.example'}).status_code==403

def test_attachment_validation_and_references(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedBroker())) as c:
        t=setup(c);m=model(c,t);w=workflow(c,t,m)
        assert c.delete(f'/api/t/{t}/models/{m}').status_code==422
        a=c.post(f'/api/t/{t}/attachments',files={'file':('context.md',b'# Private context','text/markdown')}).json()
        assert a['name']=='context.md' and 'content' not in a
        bad=c.post(f'/api/t/{t}/attachments',files={'file':('binary.bin',b'\x00\xff','application/octet-stream')})
        assert bad.status_code==422

def test_unowned_tenant_rejected_even_with_valid_session(tmp_path):
    app=create_app(str(tmp_path),ScriptedBroker())
    with TestClient(app) as c:
        t=setup(c)
        with app.state.store.db() as db:
            db.execute('UPDATE tenants SET owner=? WHERE id=?',('another-owner',t))
        assert c.get('/api/tenants').json()==[]
        assert c.get(f'/api/t/{t}/models').status_code==403
        assert c.put(f'/api/tenants/{t}',json={'name':'Take over'}).status_code==403
