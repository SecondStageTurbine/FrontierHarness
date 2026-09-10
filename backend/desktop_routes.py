"""Project/session API and a one-use native-launch authentication exchange."""
import asyncio
import hmac
import json
import os
from pathlib import Path
from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse
from .schemas import ProjectInput, SessionInput, InstructionInput, TenantInput, WorkflowInput, CommandInput
from .store import now, uid, TenantIsolationViolationException
from .projects import ProjectFiles
from .engine import TERMINAL

def install_desktop_routes(app,store,engine,user,scoped,create_session):
    files=ProjectFiles(store)
    ticket_used=False

    @app.get('/desktop/bootstrap',include_in_schema=False)
    def bootstrap(ticket:str):
        nonlocal ticket_used
        expected=os.environ.get('HARNESS_DESKTOP_TOKEN')
        if ticket_used or not expected or not hmac.compare_digest(ticket,expected):
            raise HTTPException(403,'Launch Frontier from the desktop application.')
        ticket_used=True
        owner='desktop-owner'
        with store.db() as db:
            if not db.execute('SELECT id FROM users WHERE id=?',(owner,)).fetchone():
                db.execute('INSERT INTO users VALUES(?,?,?)',(owner,'Local user','desktop-managed'))
            row=db.execute('SELECT data FROM tenants WHERE owner=? ORDER BY rowid LIMIT 1',(owner,)).fetchone()
            if row:
                tenant=json.loads(row['data'])
            else:
                tenant={'id':uid(),**TenantInput(name='Personal',environment='Personal').model_dump(),'created_at':now()}
                db.execute('INSERT INTO tenants VALUES(?,?,?)',(tenant['id'],owner,json.dumps(tenant)))
        if os.environ.get('HARNESS_IMPORT_ENV')=='1' and os.environ.get('ANTHROPIC_API_KEY') and not store.list(tenant['id'],'models'):
            model_name=os.environ.get('HARNESS_DEFAULT_MODEL','claude-haiku-4-5-20251001')
            store.put(tenant['id'],'models',{'id':uid(),'name':'Claude · local environment','provider':'anthropic','model_name':model_name,'base_url':None,'input_price':None,'output_price':None,'status':'untested','key_hint':'Imported from desktop environment','encrypted_key':store.encrypt(os.environ['ANTHROPIC_API_KEY']),'created_at':now()})
        response=RedirectResponse('/',status_code=303)
        create_session(response,owner)
        return response

    @app.get('/api/t/{tenant_id}/projects')
    def list_projects(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return store.list(tenant_id,'projects')

    @app.post('/api/t/{tenant_id}/projects')
    def create_project(tenant_id:str,payload:ProjectInput,request:Request):
        scoped(request,tenant_id)
        return files.create(tenant_id,payload.name,payload.root)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/files')
    def tree(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        return files.tree(tenant_id,project_id)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/file')
    def file(tenant_id:str,project_id:str,path:str,request:Request):
        scoped(request,tenant_id)
        return files.read(tenant_id,project_id,path)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/sessions')
    def sessions(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        return [s for s in store.list(tenant_id,'sessions') if s['project_id']==project_id]

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions')
    def new_session(tenant_id:str,project_id:str,payload:SessionInput,request:Request):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        return store.put(tenant_id,'sessions',{'id':uid(),'project_id':project_id,'name':payload.name,'messages':[],'run_ids':[],'created_at':now()})

    def get_session(tenant_id,project_id,session_id):
        store.get(tenant_id,'projects',project_id)
        session=store.get(tenant_id,'sessions',session_id)
        if session['project_id']!=project_id:
            raise TenantIsolationViolationException('Session is unavailable in this project.')
        return session

    @app.get('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}')
    def session_detail(tenant_id:str,project_id:str,session_id:str,request:Request):
        scoped(request,tenant_id)
        return get_session(tenant_id,project_id,session_id)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/instructions')
    async def instruct(tenant_id:str,project_id:str,session_id:str,payload:InstructionInput,request:Request):
        tenant=scoped(request,tenant_id)
        session=get_session(tenant_id,project_id,session_id)
        project=store.get(tenant_id,'projects',project_id)
        engine.ensure_available(tenant_id)
        roles=['discussion','planning','building','review']
        if set(payload.team)!=set(roles):
            raise ValueError('Choose a model for discussion, planning, building, and review in the AI team selector.')
        prompts={}
        for role,prompt_id in payload.prompt_ids.items():
            prompt=store.get(tenant_id,'prompts',prompt_id)
            if prompt['role']!=role:
                raise ValueError('The saved prompt role must match its team role.')
            prompts[role]=prompt['content']
        context=['User-provided context:\n'+payload.context,'Previous session instructions:']
        for message in session['messages']:
            context.append(message['content'])
        for rid in session['run_ids']:
            previous=store.get(tenant_id,'runs',rid)
            context.append(f'Previous run: {previous["status"]}.')
            for s in previous['stages']:
                if s['role']=='review' and s.get('artifact'):
                    context.append(json.dumps(s['artifact']))
        combined='\n\n'.join(context)
        if len(combined)>60000:
            raise ValueError('This session has reached its context capacity. Start a new session in the same project to continue with the current files.')
        w=WorkflowInput(name=payload.content.splitlines()[0][:100],objective=payload.content if len(payload.content)>=10 else 'User instruction: '+payload.content,
            context=combined,stages=[{'id':uid(),'role':r,'model_id':payload.team[r],'agent_id':payload.agent_ids.get(r),'prompt':prompts.get(r,''),'max_tokens':6000,'timeout':180} for r in roles],
            project_id=project_id,session_id=session_id,execution_mode=payload.execution_mode,
            max_workflow_iterations=min(payload.max_workflow_iterations,tenant['max_workflow_iterations']),attachment_ids=payload.attachment_ids)
        engine.validate_workflow(tenant_id,w.model_dump())
        workflow=store.put(tenant_id,'workflows',{'id':uid(),**w.model_dump(),'created_at':now()})
        run=engine.start(tenant_id,workflow['id'])
        session['messages'].append({'id':uid(),'role':'user','content':payload.content,'run_id':run['id'],'created_at':now()})
        session['run_ids'].append(run['id'])
        if len(session['messages'])==1:session['name']=payload.content.splitlines()[0][:80]
        store.put(tenant_id,'sessions',session)
        project.update(team=payload.team,agent_ids=payload.agent_ids,prompt_ids=payload.prompt_ids,execution_mode=payload.execution_mode,last_session_id=session_id)
        store.put(tenant_id,'projects',project)
        return {'session':session,'run':run}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/commands')
    async def command(tenant_id:str,project_id:str,session_id:str,payload:CommandInput,request:Request):
        scoped(request,tenant_id)
        session=get_session(tenant_id,project_id,session_id)
        engine.ensure_available(tenant_id)
        if not session['run_ids']:
            raise ValueError('Start a session instruction before running a project check.')
        previous=store.get(tenant_id,'runs',session['run_ids'][-1])
        # A separate run records manual checks; completed AI review history stays immutable.
        run={**previous,'id':uid(),'status':'running','phase':'COMMAND','stages':[],'transcript':[],'commands':[],'changes':[],'file_hashes':{},'cost':0,'cost_complete':True,'input_tokens':0,'output_tokens':0,'usage_complete':True,'created_at':now(),'started_at':now(),'finished_at':None,'error':None,'error_code':None}
        store.put(tenant_id,'runs',run)
        session['run_ids'].append(run['id']);store.put(tenant_id,'sessions',session)
        async def execute_check():
            try:
                result=await files.run_command(tenant_id,run,payload.command)
                engine.finish(tenant_id,run,'complete' if result['status']=='completed' else 'failed',None if result['status']=='completed' else 'The project check failed. See terminal output.')
            except asyncio.CancelledError:
                engine.finish(tenant_id,run,'cancelled','Project check cancelled.')
                raise
            except Exception:
                engine.finish(tenant_id,run,'failed','The project check could not finish. See terminal output.')
        key=(tenant_id,run['id'])
        task=asyncio.create_task(execute_check())
        engine.tasks[key]=task
        task.add_done_callback(lambda _:engine.tasks.pop(key,None))
        return store.get(tenant_id,'runs',run['id'])
