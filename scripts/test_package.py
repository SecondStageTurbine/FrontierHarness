"""Exercise the frozen backend with no Python/Node/repository on its PATH."""
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import httpx

root=Path(__file__).resolve().parents[1]
live='--live' in sys.argv
arguments=[a for a in sys.argv[1:] if a!='--live']
executable=Path(arguments[0]).resolve() if arguments else root/'src-tauri/binaries/frontier-backend-x86_64-pc-windows-msvc.exe'
if not executable.is_file():raise SystemExit('Build the bundled backend first.')
env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA'}}
env['PATH']=str(Path(os.environ['SYSTEMROOT'])/'System32')
env['PYTHONUTF8']='1'
with tempfile.TemporaryDirectory(prefix='frontier-package-') as directory:
    cwd=Path(directory)
    (cwd/'test_runtime.py').write_text('import sys\nimport unittest\nclass BundledRuntime(unittest.TestCase):\n    def test_python_is_bundled(self):\n        self.assertTrue(getattr(sys, "frozen", False))\n    def test_standard_library(self):\n        self.assertEqual(sum([1, 2, 3]), 6)\n',encoding='utf-8')
    checks=[]
    for module,args in [('unittest',['discover']),('pytest',['-q']),('compileall',['-q','.'])]:
        result=subprocess.run([str(executable),'--project-check',module,*args],cwd=cwd,env=env,capture_output=True,text=True,timeout=120,creationflags=0x08000000)
        assert result.returncode==0,(module,result.stdout,result.stderr)
        checks.append({'module':module,'exit_code':result.returncode,'output':(result.stdout+result.stderr).strip()})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    ticket=secrets.token_urlsafe(32)
    env.update(HARNESS_DATA_DIR=str(cwd/'state'),HARNESS_DESKTOP_PORT=str(port),HARNESS_DESKTOP_TOKEN=ticket,HARNESS_PARENT_PID=str(os.getpid()))
    if live:
        import shutil
        tool=os.environ.get('HARNESS_LIVE_TOOL','claude')
        found=shutil.which(tool)
        if not found:raise SystemExit(f'The optional live test needs {tool} installed and signed in.')
        env['PATH']=str(Path(found).parent)+os.pathsep+env['PATH']
        node=shutil.which('node')
        if node:env['PATH']=str(Path(node).parent)+os.pathsep+env['PATH']
    live_result=None
    with (cwd/'backend.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(executable)],cwd=cwd,env=env,stdout=log,stderr=log,creationflags=0x08000000)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=5) as client:
                for _ in range(180):
                    if proc.poll() is not None:raise AssertionError((cwd/'backend.log').read_text())
                    try:
                        if client.get('/api/health').status_code==200:break
                    except httpx.RequestError:pass
                    time.sleep(.25)
                else:raise AssertionError('Packaged backend failed to become healthy.')
                assert client.get('/api/tenants').status_code==401
                assert client.get('/desktop/bootstrap',params={'ticket':ticket}).status_code==303
                tenants=client.get('/api/tenants').json();tenant=tenants[0]['id']
                index=client.get('/');assert index.status_code==200
                script=re.search(r'src="(/assets/[^\"]+\.js)"',index.text).group(1)
                assert 'javascript' in client.get(script).headers['content-type']
                (cwd/'project').mkdir()
                p=client.post(f'/api/t/{tenant}/projects',json={'name':'Packaged project','root':str(cwd/'project')}).json()
                (Path(p['root'])/'readme.md').write_text('Packaged file access works.',encoding='utf-8')
                file=client.get(f'/api/t/{tenant}/projects/{p["id"]}/file',params={'path':'readme.md'})
                assert file.json()['content']=='Packaged file access works.'
                if live:
                    provider={'claude':'claude_cli','codex':'codex_cli','opencode':'opencode_cli'}[os.environ.get('HARNESS_LIVE_TOOL','claude')]
                    connected=client.post(f'/api/t/{tenant}/models',json={'name':'Live agent','provider':provider,
                        'model_name':os.environ.get('HARNESS_LIVE_MODEL','sonnet')})
                    assert connected.status_code==200,connected.text
                other=client.post('/api/tenants',json={'name':'Isolated'}).json()['id']
                assert client.get(f'/api/t/{other}/projects/{p["id"]}/files').status_code==403
                assert client.get('/desktop/bootstrap',params={'ticket':ticket}).status_code==403
                if live:
                    model=client.get(f'/api/t/{tenant}/models').json()[0]
                    session=client.post(f'/api/t/{tenant}/projects/{p["id"]}/sessions',json={}).json()
                    route=f'/api/t/{tenant}/projects/{p["id"]}/sessions/{session["id"]}'
                    started=client.post(route+'/instructions',json={
                        'content':'Create add.py with an add(a, b) function and test_add.py with two unittest cases. Standard library only, under 30 lines. Then reply with one short sentence.',
                        'model_id':model['id'],'mode':'auto'})
                    assert started.status_code==200,started.text
                    for _ in range(900):
                        reply=client.get(route).json()['messages'][-1]
                        if reply['status']!='running':break
                        time.sleep(2)
                    assert reply['status']=='complete',(reply['status'],reply.get('error'))
                    # The agent wrote to the folder itself, so the folder is what proves it ran.
                    assert reply['changes'],'the turn reported no file changes'
                    for change in reply['changes']:
                        if change['status']!='removed':
                            assert (Path(p['root'])/change['path']).read_text(encoding='utf-8')==change['after']
                    live_result={'status':reply['status'],'provider':model['provider'],'model':model['model_name'],
                        'mode':reply['mode'],'reply':reply['content'][:400],
                        'changed_files':[{'path':c['path'],'status':c['status']} for c in reply['changes']],
                        'input_tokens':reply['input_tokens'],'output_tokens':reply['output_tokens']}
        finally:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
            proc.wait(timeout=15)
    with executable.open('rb') as binary:checksum=hashlib.file_digest(binary,'sha256').hexdigest()
    report={'executable':executable.name,'executable_sha256':checksum,'no_system_python_or_node_on_path':True,'checks':checks,'packaged_assets':True,'authentication':True,'project_file_access':True,'tenant_isolation':True}
    if live_result:report['real_agent_turn']=live_result
    (root/'docs/packaged-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Packaged runtime, unittest, pytest, compileall, frontend assets, authentication, and tenant file isolation passed.')
