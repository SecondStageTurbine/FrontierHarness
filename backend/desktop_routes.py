"""Project/session API and a one-use native-launch authentication exchange."""
import asyncio
import hmac
import json
import os
from pathlib import Path
from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from .schemas import ProjectInput, SessionInput, InstructionInput, TenantInput, CommandInput
from .store import now, uid, TenantIsolationViolationException
from .projects import ProjectFiles

def install_desktop_routes(app,store,runner,user,scoped,create_session):
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
        return store.put(tenant_id,'sessions',{'id':uid(),'project_id':project_id,'name':payload.name,'messages':[],'commands':[],'created_at':now(),'updated_at':now()})

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
        """One message, one agentic turn. The reply arrives in the session, not in this response."""
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return runner.send(tenant_id,project_id,session_id,payload.content,payload.model_id,payload.mode)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/cancel')
    async def stop_turn(tenant_id:str,project_id:str,session_id:str,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        await runner.cancel(tenant_id,session_id)
        return get_session(tenant_id,project_id,session_id)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/events')
    async def session_events(tenant_id:str,project_id:str,session_id:str,request:Request,after:int=0):
        """Live activity for one conversation. Closed when nothing in it is still working."""
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        try:
            cursor=max(after,int(request.headers.get('last-event-id','0')))
        except ValueError:
            cursor=after
        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                try:
                    scoped(request,tenant_id)  # Session expiry or revocation also terminates open streams.
                    batch=store.events(tenant_id,session_id,cursor)
                    for event in batch:
                        cursor=event['seq']
                        yield f'id: {cursor}\ndata: {json.dumps(event)}\n\n'
                    if not runner.busy(tenant_id,session_id) and len(batch)<500:
                        yield 'event: done\ndata: {}\n\n'
                        return
                    yield ': heartbeat\n\n'
                    await asyncio.sleep(0.4)
                except (HTTPException,TenantIsolationViolationException):
                    return
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/commands')
    async def command(tenant_id:str,project_id:str,session_id:str,payload:CommandInput,request:Request):
        """The user's own project check, run at their request rather than by an agent."""
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return await files.run_command(tenant_id,project_id,session_id,payload.command)
