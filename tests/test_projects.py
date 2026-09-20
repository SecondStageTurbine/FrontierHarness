"""The project folder: what Frontier itself may read, and the checks the user can run."""
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.agent import fingerprint
from backend.projects import ProjectFiles
from backend.store import TenantIsolationViolationException
from tests.harness import ScriptedAgent, setup_store
from tests.test_api import setup, agent as connect_agent


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


def test_project_file_boundaries_and_secret_exclusion(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir();(root/'readme.md').write_text('Local project');(root/'.env').write_text('SECRET=value')
    project=files.create('tenant-a','Project',str(root))
    assert files.tree('tenant-a',project['id'])==[{'path':'readme.md','size':13,'text':True}]
    for path in ['../state/secret.key','/etc/passwd','C:/Windows/test','readme.md/../.env','.env','.ENV.local','SECRET.KEY','NODE_MODULES/test.js','file:stream','node_modules/test.js']:
        with pytest.raises((ValueError,TenantIsolationViolationException)):files.resolve('tenant-a',project['id'],path)
    with pytest.raises(TenantIsolationViolationException):files.read('tenant-b',project['id'],'readme.md')
    with pytest.raises(TenantIsolationViolationException):files.create('tenant-b','Same root',str(root))


def test_commands_reject_shells_and_path_escape(tmp_path):
    files=ProjectFiles(setup_store(tmp_path/'state'))
    escapes=['python -m pytest ../other','python -m pytest /Windows/Temp/evil_test.py',
             'python -m unittest discover -sC:\\Users\\Public','pytest --rootdir=/etc',
             'python -m compileall \\\\server\\share','pytest -s/Windows/Temp']
    for cmd in ['powershell -Command anything','python -c "print(1)"','pytest; whoami','npm run build && echo ok','curl https://example.com','rm -rf .',*escapes]:
        with pytest.raises(ValueError):files.command_argv(tmp_path,cmd)
    # Relative arguments, option flags and pytest node ids stay usable.
    assert files.command_argv(tmp_path,'python -m pytest tests/test_a.py::test_fn -q --tb=short')[-3:]==['tests/test_a.py::test_fn','-q','--tb=short']


def test_frozen_command_routing_uses_bundled_python(tmp_path,monkeypatch):
    import sys
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    files=ProjectFiles(setup_store(tmp_path/'state'))
    assert files.command_argv(tmp_path,'python -m unittest discover')==[sys.executable,'--project-check','unittest','discover']
    assert files.command_argv(tmp_path,'pytest -q')==[sys.executable,'--project-check','pytest','-q']


@pytest.mark.skipif(os.name!='nt',reason='Windows filename identity')
def test_windows_path_separators_survive_command_parsing(tmp_path):
    files=ProjectFiles(setup_store(tmp_path/'state'))
    assert files.command_argv(tmp_path,'python -m pytest tests\\unit\\test_a.py')[-1]=='tests\\unit\\test_a.py'


def test_an_unavailable_folder_is_reported_not_a_server_error(tmp_path):
    """A disconnected drive or a deleted folder is a user condition; OSError has no handler."""
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    with pytest.raises(ValueError):
        files.create('tenant-a','Missing',str(tmp_path/'not-there'))
    root=tmp_path/'project';root.mkdir();(root/'notes.md').write_text('x',encoding='utf-8')
    project=files.create('tenant-a','Project',str(root))
    (root/'notes.md').unlink();root.rmdir()
    with pytest.raises(ValueError):files.tree('tenant-a',project['id'])
    with pytest.raises(ValueError):files.read('tenant-a',project['id'],'notes.md')
    with pytest.raises(ValueError):files.resolve('tenant-a',project['id'],'notes.md')


def test_project_pdfs_are_read_and_binary_files_stay_out_of_the_diff(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';root.mkdir()
    (root/'report.pdf').write_bytes(make_pdf('Quarterly revenue rose 12 percent'))
    (root/'logo.png').write_bytes(bytes.fromhex('89504e470d0a1a0a')+bytes(20))
    project=files.create('tenant-a','Project',str(root))
    assert 'Quarterly revenue rose 12 percent' in files.read('tenant-a',project['id'],'report.pdf')['content']
    # A PDF is readable text and belongs in the before-and-after; an image is neither, and a
    # diff that claimed to show one would be showing bytes it never had.
    captured=fingerprint(files,'tenant-a',project['id'])
    assert 'report.pdf' in captured and 'logo.png' not in captured


def test_the_walk_refuses_by_name_and_says_which_entries_it_refused(tmp_path):
    store=setup_store(tmp_path/'state');files=ProjectFiles(store)
    root=tmp_path/'project';(root/'data').mkdir(parents=True);(root/'node_modules').mkdir()
    (root/'data'/'report.md').write_text('Q3 revenue',encoding='utf-8')
    (root/'node_modules'/'pkg.js').write_text('noise',encoding='utf-8')
    (root/'.env').write_text('SECRET=value',encoding='utf-8')
    (root/'notes.md').write_text('visible',encoding='utf-8')
    project=files.create('tenant-a','Documents',str(root))
    entries,refused=files.walk('tenant-a',project['id'])
    assert [f['path'] for f in entries]==['notes.md']
    assert any(line.startswith('data/') for line in refused) and any(line.startswith('node_modules/') for line in refused)
    assert any(line.startswith('.env') for line in refused)
    assert 'SECRET=value' not in ' '.join(refused)   # named only; never its contents
    # The agent reads the folder with its own tools, so this only bounds what Frontier shows.
    assert list(fingerprint(files,'tenant-a',project['id']))==['notes.md']


def test_project_and_session_routes_isolate_across_workspaces(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'),ScriptedAgent())) as c:
        t=setup(c);connect_agent(c,t);root=tmp_path/'project';root.mkdir()
        p=c.post(f'/api/t/{t}/projects',json={'name':'API project','root':str(root)}).json()
        s=c.post(f'/api/t/{t}/projects/{p["id"]}/sessions',json={}).json()
        route=f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        b=c.post('/api/tenants',json={'name':'Other'}).json()['id']
        assert c.get(f'/api/t/{b}/projects/{p["id"]}/files').status_code==403
        assert c.get(route.replace(f'/t/{t}/',f'/t/{b}/')).status_code==403
        assert c.post(route.replace(f'/t/{t}/',f'/t/{b}/')+'/commands',json={'command':'pytest -q'}).status_code==403


def test_a_project_check_records_its_own_output_in_the_conversation(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'),ScriptedAgent())) as c:
        t=setup(c);connect_agent(c,t)
        root=tmp_path/'project';root.mkdir()
        (root/'test_ok.py').write_text('import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n',encoding='utf-8')
        p=c.post(f'/api/t/{t}/projects',json={'name':'Checks','root':str(root)}).json()
        s=c.post(f'/api/t/{t}/projects/{p["id"]}/sessions',json={}).json()
        route=f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        # A check needs no turn first: it is the user's own command, not something an agent asked for.
        record=c.post(route+'/commands',json={'command':'python -m unittest discover'}).json()
        assert record['status']=='completed' and record['exit_code']==0
        assert c.get(route).json()['commands'][0]['id']==record['id']


def test_desktop_ticket_is_one_use_and_does_not_bypass_workspace_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_TOKEN','one-time-desktop-ticket')
    monkeypatch.delenv('HARNESS_IMPORT_ENV',raising=False)
    with TestClient(create_app(str(tmp_path/'state'),ScriptedAgent())) as c:
        assert c.get('/desktop/bootstrap?ticket=wrong').status_code==403
        response=c.get('/desktop/bootstrap?ticket=one-time-desktop-ticket',follow_redirects=False)
        assert response.status_code==303
        assert c.get('/api/tenants').json()[0]['name']=='Personal'
        assert c.get('/desktop/bootstrap?ticket=one-time-desktop-ticket').status_code==403
