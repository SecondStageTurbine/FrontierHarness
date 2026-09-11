import asyncio
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.projects import ProjectFiles
from backend.store import TenantIsolationViolationException
from backend.engine import Engine
from tests.test_api import setup,model
from tests.test_engine import setup_store,ScriptedBroker,REJECTED,APPROVED,BUILD

def test_project_file_boundaries_and_secret_exclusion(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir();(root/'readme.md').write_text('Local project');(root/'.env').write_text('SECRET=value')
    project=files.create('tenant-a','Project',str(root))
    assert files.tree('tenant-a',project['id'])==[{'path':'readme.md','size':13,'text':True}]
    for path in ['../state/secret.key','/etc/passwd','C:/Windows/test','readme.md/../.env','.env','.ENV.local','SECRET.KEY','NODE_MODULES/test.js','file:stream','node_modules/test.js']:
        with pytest.raises((ValueError,TenantIsolationViolationException)):files.resolve('tenant-a',project['id'],path)
    with pytest.raises(TenantIsolationViolationException):files.read('tenant-b',project['id'],'readme.md')
    with pytest.raises(TenantIsolationViolationException):files.create('tenant-b','Same root',str(root))

@pytest.mark.asyncio
async def test_real_file_edits_commands_and_review_replan(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store);root=tmp_path/'project';root.mkdir()
    project=files.create('tenant-a','Project',str(root))
    w=store.get('tenant-a','workflows','workflow-tenant-a');w.update(project_id=project['id'],execution_mode='execute');store.put('tenant-a','workflows',w)
    broker=ScriptedBroker([REJECTED,APPROVED]);engine=Engine(store,broker)
    run=engine.start('tenant-a',w['id']);await engine.tasks[('tenant-a',run['id'])]
    r=store.get('tenant-a','runs',run['id'])
    assert r['status']=='complete' and r['iteration']==2
    assert (root/'result.py').read_text()=='def answer():\n    return 42\n'
    assert len(r['changes'])==2 and all(c['status']=='applied' for c in r['changes'])
    assert r['commands'][0]['exit_code']==0 and 'OK' in r['commands'][0]['output']
    assert 'ACTUAL COMMAND RESULT' in broker.calls[3]['prompt'] and 'OK' in broker.calls[3]['prompt']

@pytest.mark.asyncio
async def test_propose_mode_does_not_write_or_execute(tmp_path):
    store=setup_store(tmp_path/'state');root=tmp_path/'project';root.mkdir();project=ProjectFiles(store).create('tenant-a','Project',str(root))
    w=store.get('tenant-a','workflows','workflow-tenant-a');w.update(project_id=project['id'],execution_mode='propose');store.put('tenant-a','workflows',w)
    engine=Engine(store,ScriptedBroker());run=engine.start('tenant-a',w['id']);await engine.tasks[('tenant-a',run['id'])]
    r=store.get('tenant-a','runs',run['id'])
    assert not list(root.iterdir()) and not r['commands']
    assert len(r['changes'])==2 and all(c['status']=='proposed' for c in r['changes'])

def test_concurrent_external_edit_is_not_overwritten(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store);root=tmp_path/'project';root.mkdir();(root/'result.py').write_text('user work')
    p=files.create('tenant-a','Project',str(root))
    with pytest.raises(ValueError,match='not overwritten'):
        files.apply('tenant-a',{'workflow':{'project_id':p['id']},'file_hashes':{}},{'id':'stage'},BUILD['files'])
    assert (root/'result.py').read_text()=='user work'

def test_commands_reject_shells_and_path_escape(tmp_path):
    files=ProjectFiles(setup_store(tmp_path/'state'))
    escapes=['python -m pytest ../other','python -m pytest /Windows/Temp/evil_test.py',
             'python -m unittest discover -sC:\\Users\\Public','pytest --rootdir=/etc',
             'python -m compileall \\\\server\\share','pytest -s/Windows/Temp']
    for cmd in ['powershell -Command anything','python -c "print(1)"','pytest; whoami','npm run build && echo ok','curl https://example.com','rm -rf .',*escapes]:
        with pytest.raises(ValueError):files.command_argv(tmp_path,cmd)
    # Relative arguments, option flags and pytest node ids stay usable.
    assert files.command_argv(tmp_path,'python -m pytest tests/test_a.py::test_fn -q --tb=short')[-3:]==['tests/test_a.py::test_fn','-q','--tb=short']

@pytest.mark.asyncio
async def test_manual_project_check_requires_execute_mode(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir()
    project=files.create('tenant-a','Project',str(root))
    run={'id':'manual-check','workflow':{'project_id':project['id'],'execution_mode':'edit'},'iteration':1,'commands':[]}
    record=await files.run_command('tenant-a',run,'python -m compileall .')
    assert record['status']=='failed' and record['exit_code'] is None and 'Build & test' in record['output']

def test_project_session_api_continues_and_isolates(tmp_path):
    app=create_app(str(tmp_path/'state'),ScriptedBroker())
    with TestClient(app) as c:
        t=setup(c);m=model(c,t);root=tmp_path/'project';root.mkdir()
        p=c.post(f'/api/t/{t}/projects',json={'name':'API project','root':str(root)}).json()
        assert p['name']=='API project'
        s=c.post(f'/api/t/{t}/projects/{p["id"]}/sessions',json={}).json()
        route=f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        payload={'content':'Build a test module.','team':{r:m for r in ['discussion','planning','building','review']},'execution_mode':'execute'}
        r=c.post(route+'/instructions',json=payload)
        assert r.status_code==200,r.text
        run_id=r.json()['run']['id']
        c.get(f'/api/t/{t}/runs/{run_id}/events')
        detail=c.get(f'/api/t/{t}/runs/{run_id}').json()
        assert detail['status']=='complete',detail['error']
        assert c.get(f'/api/t/{t}/projects/{p["id"]}/file',params={'path':'result.py'}).json()['content'].startswith('def answer')
        payload['content']='Review the work again.'
        r2=c.post(route+'/instructions',json=payload)
        assert r2.status_code==200
        c.get(f'/api/t/{t}/runs/{r2.json()["run"]["id"]}/events')
        session=c.get(route).json();assert len(session['messages'])==2 and len(session['run_ids'])==2
        b=c.post('/api/tenants',json={'name':'Other'}).json()['id']
        assert c.get(f'/api/t/{b}/projects/{p["id"]}/files').status_code==403
        assert c.get(route.replace(f'/t/{t}/',f'/t/{b}/')).status_code==403

def test_desktop_ticket_is_one_use_and_does_not_bypass_tenant_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_TOKEN','one-time-desktop-ticket')
    monkeypatch.delenv('HARNESS_IMPORT_ENV',raising=False)
    with TestClient(create_app(str(tmp_path/'state'),ScriptedBroker())) as c:
        assert c.get('/desktop/bootstrap?ticket=wrong').status_code==403
        response=c.get('/desktop/bootstrap?ticket=one-time-desktop-ticket',follow_redirects=False)
        assert response.status_code==303
        assert c.get('/api/tenants').json()[0]['name']=='Personal'
        assert c.get('/desktop/bootstrap?ticket=one-time-desktop-ticket').status_code==403

@pytest.mark.asyncio
async def test_failed_actual_check_overrules_model_approval_and_stops_at_limit(tmp_path):
    from backend.broker import ModelResult
    class IncorrectBuilder(ScriptedBroker):
        async def invoke(self,*args,**kwargs):
            result=await super().invoke(*args,**kwargs)
            if args[4]['role']=='building':
                build=json.loads(result.text)
                build['files'][0]['content']='def answer():\n    return 0\n'
                return ModelResult(json.dumps(build),100,50)
            return result
    store=setup_store(tmp_path/'state');project=ProjectFiles(store).create('tenant-a','Failing check')
    workflow=store.get('tenant-a','workflows','workflow-tenant-a')
    workflow.update(project_id=project['id'],execution_mode='execute',max_workflow_iterations=1)
    store.put('tenant-a','workflows',workflow)
    engine=Engine(store,IncorrectBuilder());run=engine.start('tenant-a',workflow['id'])
    await engine.tasks[('tenant-a',run['id'])]
    result=store.get('tenant-a','runs',run['id'])
    assert result['status']=='limit_reached' and result['iteration']==1
    assert result['commands'][0]['exit_code']!=0
    assert result['stages'][-1]['artifact']['approved'] is False

def test_manual_checks_are_live_cancellable_runs(tmp_path):
    import time
    app=create_app(str(tmp_path/'state'),ScriptedBroker())
    with TestClient(app) as c:
        tenant=setup(c);m=model(c,tenant)
        p=c.post(f'/api/t/{tenant}/projects',json={'name':'Cancellation'}).json()
        session=c.post(f'/api/t/{tenant}/projects/{p["id"]}/sessions',json={}).json()
        route=f'/api/t/{tenant}/projects/{p["id"]}/sessions/{session["id"]}'
        result=c.post(route+'/instructions',json={'content':'Build a tested function.','team':{r:m for r in ['discussion','planning','building','review']}}).json()
        c.get(f'/api/t/{tenant}/runs/{result["run"]["id"]}/events')
        (Path(p['root'])/'test_wait.py').write_text('import time\ntime.sleep(30)\n')
        check=c.post(route+'/commands',json={'command':'python -m unittest test_wait'}).json()
        assert check['status']=='running'
        for _ in range(100):
            saved=c.get(f'/api/t/{tenant}/runs/{check["id"]}').json()
            if saved['commands']:break
            time.sleep(.01)
        cancelled=c.post(f'/api/t/{tenant}/runs/{check["id"]}/cancel')
        assert cancelled.status_code==200
        assert cancelled.json()['status']=='cancelled'
        assert cancelled.json()['commands'][0]['status']=='cancelled'

def test_source_artifact_keeps_leading_and_trailing_whitespace():
    from backend.schemas import ArtifactFile
    content='  indented fragment\n\n'
    assert ArtifactFile(name='fragment.txt',content=content).content==content

def test_frozen_command_routing_uses_bundled_python(tmp_path,monkeypatch):
    import sys
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    files=ProjectFiles(setup_store(tmp_path/'state'))
    assert files.command_argv(tmp_path,'python -m unittest discover')==[sys.executable,'--project-check','unittest','discover']
    assert files.command_argv(tmp_path,'pytest -q')==[sys.executable,'--project-check','pytest','-q']

@pytest.mark.skipif(os.name!='nt',reason='Windows filename identity')
def test_windows_file_casing_preserves_conflict_detection(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir();(root/'readme.md').write_text('original',encoding='utf-8')
    project=files.create('tenant-a','Windows paths',str(root))
    _,hashes=files.snapshot('tenant-a',project['id'])
    run={'id':'case-test','workflow':{'project_id':project['id'],'execution_mode':'edit'},'file_hashes':hashes,'changes':[],'iteration':1}
    files.apply('tenant-a',run,{'id':'builder'},[{'name':'README.md','content':'updated'}])
    assert (root/'readme.md').read_text()=='updated'
    assert len(run['file_hashes'])==1
    with pytest.raises(ValueError,match='same file twice'):
        files.apply('tenant-a',run,{'id':'builder'},[{'name':'readme.md','content':'one'},{'name':'README.md','content':'two'}])
    (root/'readme.md').write_text('external edit',encoding='utf-8')
    with pytest.raises(ValueError,match='not overwritten'):
        files.apply('tenant-a',run,{'id':'builder'},[{'name':'README.md','content':'agent edit'}])
    assert (root/'readme.md').read_text()=='external edit'

def test_desktop_specialists_and_prompts_are_applied_and_tenant_checked(tmp_path):
    broker=ScriptedBroker()
    app=create_app(str(tmp_path/'state'),broker)
    with TestClient(app) as c:
        t=setup(c);m=model(c,t)
        agent=c.post(f'/api/t/{t}/agents',json={'name':'Careful planner','role':'planning','model_id':m,'system_prompt':'Use incremental changes.','context_rules':'Respect existing files.'}).json()
        prompt=c.post(f'/api/t/{t}/prompts',json={'name':'Review standards','role':'review','content':'Check every acceptance criterion.'}).json()
        project=c.post(f'/api/t/{t}/projects',json={'name':'Specialist project'}).json()
        session=c.post(f'/api/t/{t}/projects/{project["id"]}/sessions',json={}).json()
        route=f'/api/t/{t}/projects/{project["id"]}/sessions/{session["id"]}/instructions'
        payload={'content':'Prepare a tested module.','team':{r:m for r in ['discussion','planning','building','review']},'agent_ids':{'planning':agent['id']},'prompt_ids':{'review':prompt['id']},'execution_mode':'propose'}
        result=c.post(route,json=payload).json()
        c.get(f'/api/t/{t}/runs/{result["run"]["id"]}/events')
        assert 'Use incremental changes.' in broker.calls[1]['system']
        assert 'Check every acceptance criterion.' in broker.calls[3]['system']
        b=c.post('/api/tenants',json={'name':'Other'}).json()['id']
        foreign=c.post(f'/api/t/{b}/prompts',json={'name':'Private','role':'review','content':'Private prompt'}).json()
        payload['prompt_ids']['review']=foreign['id']
        assert c.post(route,json=payload).status_code==403

def test_files_outside_the_context_budget_stay_editable(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir()
    (root/'app.rb').write_text('puts 1\n',encoding='utf-8')      # extension absent from TEXT_EXTENSIONS
    (root/'notes.md').write_text('x'*60000,encoding='utf-8')     # larger than the per-file context cap
    project=files.create('tenant-a','Polyglot',str(root))
    context,hashes=files.snapshot('tenant-a',project['id'])
    assert {'app.rb','notes.md'}<=set(hashes)
    assert any('app.rb' in c for c in context) and not any(c.startswith('FILE notes.md') for c in context)
    assert any(c.startswith('FILES PRESENT BUT NOT PROVIDED') and 'notes.md' in c for c in context)  # declared, not silently dropped
    run={'id':'budget','workflow':{'project_id':project['id'],'execution_mode':'edit'},'file_hashes':hashes,'changes':[],'iteration':1}
    files.apply('tenant-a',run,{'id':'builder'},[{'name':'app.rb','content':'puts 2\n'},{'name':'notes.md','content':'shorter\n'}])
    assert (root/'app.rb').read_text()=='puts 2\n' and (root/'notes.md').read_text()=='shorter\n'

def test_crlf_content_survives_a_second_iteration(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir()
    project=files.create('tenant-a','Line endings',str(root))
    run={'id':'crlf','workflow':{'project_id':project['id'],'execution_mode':'edit'},'file_hashes':{},'changes':[],'iteration':1}
    produced=[{'name':'a.py','content':'x = 1\r\ny = 2\r\n'}]
    files.apply('tenant-a',run,{'id':'builder'},produced)
    assert (root/'a.py').read_bytes()==b'x = 1\r\ny = 2\r\n'  # the model's own line endings are kept
    run['iteration']=2
    files.apply('tenant-a',run,{'id':'builder'},produced)     # unchanged text is neither a conflict nor a rewrite
    assert len(run['changes'])==1

@pytest.mark.skipif(os.name!='nt',reason='Windows path separators')
def test_windows_path_separators_survive_command_parsing(tmp_path):
    files=ProjectFiles(setup_store(tmp_path/'state'))
    assert files.command_argv(tmp_path,'python -m pytest tests\\unit\\test_a.py')[-1]=='tests\\unit\\test_a.py'

def test_framework_file_names_are_accepted(tmp_path):
    from backend.schemas import ArtifactFile
    for name in ['app/[id]/page.tsx','app/(group)/layout.tsx','src/routes/+page.svelte','@types/index.d.ts']:
        assert ArtifactFile(name=name,content='x').name==name
    for name in ['C:/outside.txt','null\x00byte','star*name','tab\tname']:
        with pytest.raises(ValueError):ArtifactFile(name=name,content='x')

def make_pdf(text):
    """A minimal single-page PDF with one extractable text run."""
    stream=('BT /F1 12 Tf 20 100 Td ('+text+') Tj ET').encode()
    objects=[b'<</Type/Catalog/Pages 2 0 R>>',
             b'<</Type/Pages/Kids[3 0 R]/Count 1>>',
             b'<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>',
             b'<</Length '+str(len(stream)).encode()+b'>>stream\n'+stream+b'\nendstream',
             b'<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>']
    out=bytearray(b'%PDF-1.4\n');offsets=[]
    for i,body in enumerate(objects,1):
        offsets.append(len(out))
        out+=str(i).encode()+b' 0 obj'+body+b'endobj\n'
    start=len(out)
    out+=b'xref\n0 '+str(len(objects)+1).encode()+b'\n0000000000 65535 f \n'
    for offset in offsets:
        out+=('%010d 00000 n \n'%offset).encode()
    out+=b'trailer<</Size '+str(len(objects)+1).encode()+b'/Root 1 0 R>>\nstartxref\n'+str(start).encode()+b'\n%%EOF\n'
    return bytes(out)

def test_project_pdfs_are_read_and_unreadable_files_are_declared(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir()
    (root/'report.pdf').write_bytes(make_pdf('Quarterly revenue rose 12 percent'))
    (root/'logo.png').write_bytes(bytes.fromhex('89504e470d0a1a0a')+bytes(20))
    project=files.create('tenant-a','Project',str(root))
    assert 'Quarterly revenue rose 12 percent' in files.read('tenant-a',project['id'],'report.pdf')['content']
    context,hashes=files.snapshot('tenant-a',project['id'])
    assert any('Quarterly revenue rose 12 percent' in entry for entry in context)
    assert 'report.pdf' in hashes and 'logo.png' not in hashes
    notice=[entry for entry in context if entry.startswith('FILES PRESENT BUT NOT PROVIDED')]
    assert len(notice)==1 and 'logo.png' in notice[0] and 'report.pdf' not in notice[0]
    run={'id':'run-1','iteration':1,'changes':[],'file_hashes':hashes,'workflow':{'project_id':project['id'],'execution_mode':'execute'}}
    with pytest.raises(ValueError):  # a build must never overwrite a PDF with its extracted text
        files.apply('tenant-a',run,{'id':'stage-1'},[{'name':'report.pdf','content':'overwritten'}])
