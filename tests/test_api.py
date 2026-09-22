import json
from fastapi.testclient import TestClient
from backend.app import create_app
from tests.harness import ScriptedAgent

def setup(client):
    assert client.post('/api/auth/setup',json={'username':'tester','password':'a-secure-test-password'}).status_code==200
    return client.post('/api/tenants',json={'name':'Workspace A'}).json()['id']

def model(client,t):
    return client.post(f'/api/t/{t}/models',json={'name':'Test endpoint','provider':'custom_openai','model_name':'test-model','base_url':'http://127.0.0.1:9999/v1','api_key':'secret-test-value'}).json()['id']

def agent(client,t):
    return client.post(f'/api/t/{t}/models',json={'name':'Claude','provider':'claude_cli','model_name':'sonnet'}).json()['id']

def test_auth_required_and_first_setup_only(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        assert c.get('/api/tenants').status_code==401
        setup(c)
        assert c.post('/api/auth/setup',json={'username':'other','password':'other-strong-password'}).status_code==409
        c.post('/api/logout')
        assert c.get('/api/tenants').status_code==401

def test_credentials_are_encrypted_and_never_returned(tmp_path):
    app=create_app(str(tmp_path),ScriptedAgent())
    with TestClient(app) as c:
        t=setup(c);m=model(c,t)
        assert 'secret-test-value' not in c.get(f'/api/t/{t}/models').text
        assert 'encrypted_key' not in c.get(f'/api/t/{t}/models').text
        assert 'secret-test-value' not in (tmp_path/'harness.db').read_bytes().decode('utf-8',errors='ignore')
        assert c.get(f'/api/t/{t}/models').json()[0]['key_hint']=='••••alue'

def test_foreign_ids_blocked_across_endpoints(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'),ScriptedAgent())) as c:
        a=setup(c);b=c.post('/api/tenants',json={'name':'Workspace B'}).json()['id']
        m=model(c,a);first=agent(c,a);root=tmp_path/'work';root.mkdir()
        p=c.post(f'/api/t/{a}/projects',json={'name':'P','root':str(root)}).json()
        s=c.post(f'/api/t/{a}/projects/{p["id"]}/sessions',json={}).json()
        assert c.get(f'/api/t/{b}/projects').json()==[]
        assert c.get(f'/api/t/{b}/projects/{p["id"]}/files').status_code==403
        assert c.get(f'/api/t/{b}/projects/{p["id"]}/sessions/{s["id"]}').status_code==403
        assert c.post(f'/api/t/{b}/projects/{p["id"]}/sessions/{s["id"]}/instructions',json={'content':'hi','model_id':first,'mode':'read'}).status_code==403
        assert c.delete(f'/api/t/{b}/models/{m}').status_code==403
        assert c.post(f'/api/t/{b}/models/{first}/signin').status_code==403

def test_a_conversation_streams_its_turn_and_stays_inside_its_workspace(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'),ScriptedAgent())) as c:
        t=setup(c);a=agent(c,t);root=tmp_path/'work';root.mkdir()
        p=c.post(f'/api/t/{t}/projects',json={'name':'P','root':str(root)}).json()
        s=c.post(f'/api/t/{t}/projects/{p["id"]}/sessions',json={}).json()
        route=f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        assert c.post(route+'/instructions',json={'content':'Do the thing.','model_id':a,'mode':'edit'}).status_code==200
        with c.stream('GET',route+'/events') as stream:
            text=''.join(stream.iter_text())
        assert 'turn.started' in text and 'event: done' in text
        session=c.get(route).json()
        assert session['messages'][-1]['status']=='complete'
        b=c.post('/api/tenants',json={'name':'B'}).json()['id']
        assert c.get(route.replace(f'/t/{t}/',f'/t/{b}/')+'/events').status_code==403
        # A session can be renamed, pinned and archived in place; another workspace cannot touch it.
        renamed=c.patch(route,json={'name':'Auth work','pinned':True}).json()
        assert renamed['name']=='Auth work' and renamed['pinned'] is True and renamed['messages'][-1]['status']=='complete'
        assert c.patch(route,json={'archived':True}).json()['archived'] is True
        assert c.patch(route,json={'name':''}).status_code==422
        assert c.patch(route.replace(f'/t/{t}/',f'/t/{b}/'),json={'name':'X'}).status_code==403

def test_cross_origin_blocked(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        assert c.post('/api/auth/setup',json={'username':'tester','password':'secure-password-for-test'},headers={'Origin':'https://attacker.example'}).status_code==403

def test_attachment_validation_and_references(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        t=setup(c);m=model(c,t)
        a=c.post(f'/api/t/{t}/attachments',files={'file':('context.md',b'# Private context','text/markdown')}).json()
        assert a['name']=='context.md' and 'content' not in a
        bad=c.post(f'/api/t/{t}/attachments',files={'file':('binary.bin',b'\x00\xff','application/octet-stream')})
        assert bad.status_code==422
        from tests.test_projects import make_pdf
        scan=c.post(f'/api/t/{t}/attachments',files={'file':('scan.pdf',make_pdf(''),'application/pdf')})
        assert scan.status_code==422 and 'OCR' in scan.json()['detail']  # says what is missing, not that the file is broken
        doc=c.post(f'/api/t/{t}/attachments',files={'file':('minutes.docx',b'PK\x03\x04binary','application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
        assert doc.status_code==422 and 'Export to PDF' in doc.json()['detail']

def test_unowned_tenant_rejected_even_with_valid_session(tmp_path):
    app=create_app(str(tmp_path),ScriptedAgent())
    with TestClient(app) as c:
        t=setup(c)
        with app.state.store.db() as db:
            db.execute('UPDATE tenants SET owner=? WHERE id=?',('another-owner',t))
        assert c.get('/api/tenants').json()==[]
        assert c.get(f'/api/t/{t}/models').status_code==403
        assert c.put(f'/api/tenants/{t}',json={'name':'Take over'}).status_code==403

def test_session_slides_on_use_so_an_open_app_never_expires(tmp_path):
    import sqlite3,time
    from backend.app import SESSION_LIFETIME
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        setup(c)
        db=sqlite3.connect(tmp_path/'harness.db')
        db.execute('UPDATE sessions SET expires=?',(time.time()+60,));db.commit()
        assert c.get('/api/tenants').status_code==200
        remaining=db.execute('SELECT expires FROM sessions').fetchone()[0]-time.time()
        db.close()
        assert remaining>SESSION_LIFETIME/2

def test_desktop_managed_login_names_the_recovery(tmp_path):
    import sqlite3
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        setup(c)
        db=sqlite3.connect(tmp_path/'harness.db')
        db.execute("INSERT INTO users VALUES('desktop-owner','Local user','desktop-managed')");db.commit();db.close()
        r=c.post('/api/auth/login',json={'username':'Local user','password':'any-password-at-all'})
        assert r.status_code==401 and 'signs in automatically' in r.json()['detail']

def test_a_named_subscription_gets_its_own_directory_and_sign_in_command(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        t=setup(c)
        second=c.post(f'/api/t/{t}/models',json={'name':'Claude seat two','provider':'claude_cli','model_name':'opus','account':'seat-2'}).json()
        default=c.post(f'/api/t/{t}/models',json={'name':'Claude default','provider':'claude_cli','model_name':'opus'}).json()
        command=c.post(f'/api/t/{t}/models/{second["id"]}/signin').json()
        # The directory is the credential boundary: it is inside this workspace, and the command
        # points the tool at it rather than at the sign-in already on the machine.
        assert command['variable']=='CLAUDE_CONFIG_DIR' and command['directory'].endswith('seat-2')
        assert t in command['directory'] and (tmp_path/'subscriptions'/t/'claude_cli'/'seat-2').is_dir()
        assert command['directory'] in command['command']
        # Without a named account there is no separate credential to sign in to.
        assert c.post(f'/api/t/{t}/models/{default["id"]}/signin').status_code==422
        assert c.get(f'/api/t/{t}/models').json()[0]['account'] in ('seat-2',None)

def test_dictation_refuses_a_model_that_cannot_transcribe(tmp_path):
    with TestClient(create_app(str(tmp_path),ScriptedAgent())) as c:
        t=setup(c)
        m=c.post(f'/api/t/{t}/models',json={'name':'Claude','provider':'claude_cli','model_name':'opus'}).json()['id']
        response=c.post(f'/api/t/{t}/transcribe',data={'model_id':m},files={'file':('speech.webm',b'not audio','audio/webm')})
        assert response.status_code==422 and 'compatible' in response.json()['detail']
