"""Project/session API and a one-use native-launch authentication exchange."""
import asyncio
import base64
import hmac
import json
import os
import re
from pathlib import Path
from fastapi import Request, Response, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from .schemas import ProjectInput, SessionInput, SessionPatch, InstructionInput, TenantInput, CommandInput, GitPaths, CommitInput, FileWrite
from .store import now, uid, TenantIsolationViolationException
from .projects import ProjectFiles
from . import gitops
from .adaptive import ADAPTIVE
from .broker import CLI_TOOLS

def attachment_note(store,tenant_id,root,attachment_ids):
    """Write each named attachment into the folder the agent works in so it can open the actual file.

    They land in .frontier/attachments: a dot directory the tree and change fingerprint ignore,
    so context files never read as project changes."""
    if not attachment_ids:
        return ''
    folder=Path(root)/'.frontier'/'attachments'
    lines=[]
    for aid in dict.fromkeys(attachment_ids):
        attachment=store.get(tenant_id,'attachments',aid)
        name=Path(attachment['name']).name or 'attachment.txt'
        content=attachment.get('content') or ''
        data=base64.b64decode(content.split(';base64,',1)[1]) if content.startswith('data:') and ';base64,' in content else content.encode('utf-8')
        target=folder/name
        n=2
        while target.exists() and target.read_bytes()!=data:
            target=folder/f'{Path(name).stem}-{n}{Path(name).suffix}'
            n+=1
        if not target.exists():
            folder.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
        lines.append(f'- {target.relative_to(root).as_posix()}')
    return ('\n\nAttached for context — harness-managed files in your working directory. '
            'Read them as needed; they are not project source and should not be committed:\n'+'\n'.join(lines))

def session_sidebar_state(session,active_sessions):
    """Small status model for the desktop rail: working, waiting, done, or quiet."""
    if session.get('id') in active_sessions:
        return 'working'
    messages=session.get('messages') or []
    if not messages:
        return None
    last=messages[-1]
    if last.get('role')=='assistant':
        status=last.get('status')
        if status=='running':
            return 'working'
        if status in ('failed','cancelled'):
            return 'waiting'
        return 'done'
    return 'waiting'

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

    @app.get('/api/t/{tenant_id}/activity')
    def activity(tenant_id:str,request:Request):
        """Conversation status for the sidebar, across projects."""
        scoped(request,tenant_id)
        active=[]
        active_sessions=set()
        for (tid,session_id),task in list(runner.turns.items()):
            if tid!=tenant_id or task.done():
                continue
            try:
                session=store.get(tenant_id,'sessions',session_id)
            except TenantIsolationViolationException:
                continue
            active_sessions.add(session_id)
            active.append({'project_id':session['project_id'],'session_id':session_id})
        sessions=[]
        project_states={}
        priority={'working':3,'waiting':2,'done':1}
        for session in store.list(tenant_id,'sessions'):
            state=session_sidebar_state(session,active_sessions)
            if not state:
                continue
            item={'project_id':session['project_id'],'session_id':session['id'],'state':state}
            sessions.append(item)
            current=project_states.get(session['project_id'])
            if not current or priority[state]>priority[current]:
                project_states[session['project_id']]=state
        projects=[{'project_id':project_id,'state':state} for project_id,state in project_states.items()]
        return {'active':active,'sessions':sessions,'projects':projects}

    @app.post('/api/t/{tenant_id}/projects')
    def create_project(tenant_id:str,payload:ProjectInput,request:Request):
        scoped(request,tenant_id)
        return files.create(tenant_id,payload.name,payload.root)

    @app.delete('/api/t/{tenant_id}/projects/{project_id}')
    def remove_project(tenant_id:str,project_id:str,request:Request):
        """Untrack a project from this workspace. The folder on disk is left exactly where it is."""
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        sessions=[s for s in store.list(tenant_id,'sessions') if s['project_id']==project_id]
        if any(runner.busy(tenant_id,s['id']) for s in sessions):
            raise ValueError('Stop the conversation that is still working before removing this project.')
        with store.db() as db:
            for s in sessions:
                db.execute('DELETE FROM events WHERE tenant_id=? AND run_id=?',(tenant_id,s['id']))
        for s in sessions:
            store.delete(tenant_id,'sessions',s['id'])
        store.delete(tenant_id,'projects',project_id)
        return {'ok':True}

    @app.get('/api/t/{tenant_id}/projects/{project_id}/files')
    def tree(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return files.tree(tenant_id,project_id,session_id)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/file')
    def file(tenant_id:str,project_id:str,path:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return files.read(tenant_id,project_id,path,session_id)

    @app.put('/api/t/{tenant_id}/projects/{project_id}/file')
    def write_file(tenant_id:str,project_id:str,path:str,payload:FileWrite,request:Request,session_id:str|None=None):
        """The user's own edit. If a turn is running it is attributed to that turn, as any write during it is."""
        scoped(request,tenant_id)
        return files.write(tenant_id,project_id,path,payload.content,session_id)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/search')
    def search_files(tenant_id:str,project_id:str,q:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return files.search(tenant_id,project_id,q,session_id)

    @app.get('/api/t/{tenant_id}/search/sessions')
    def search_sessions(tenant_id:str,q:str,request:Request):
        """Conversations in this workspace whose messages mention the query, newest first."""
        scoped(request,tenant_id)
        needle=q.strip().lower()
        if len(needle)<2:
            raise ValueError('Search for at least two characters.')
        hits=[]
        for session in store.list(tenant_id,'sessions'):
            for message in reversed(session.get('messages') or []):
                text=message.get('content') or ''
                at=text.lower().find(needle)
                if at>=0:
                    start=max(0,at-60)
                    hits.append({'session_id':session['id'],'project_id':session['project_id'],'name':session['name'],'archived':bool(session.get('archived')),
                                 'role':message.get('role'),'snippet':('…' if start else '')+text[start:at+len(needle)+80].replace('\n',' ')+('…' if at+len(needle)+80<len(text) else '')})
                    break
            if len(hits)>=50:
                break
        return hits

    def git_path(path):
        clean=path.replace('\\','/')
        if not clean or clean.startswith('/') or ':' in clean or any(p in ('..','') for p in clean.split('/')):
            raise ValueError('Use a relative path inside the project.')
        return clean

    @app.get('/api/t/{tenant_id}/projects/{project_id}/git')
    async def git_status(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        """The repository as the Changes panel shows it, for the project folder or the session's worktree."""
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        result=await gitops.status(root)
        result['worktree']=store.get(tenant_id,'sessions',session_id).get('worktree') if session_id else None
        return result

    @app.get('/api/t/{tenant_id}/projects/{project_id}/git/diff')
    async def git_diff(tenant_id:str,project_id:str,path:str,request:Request,staged:bool=False,session_id:str|None=None):
        scoped(request,tenant_id)
        return {'path':path,'staged':staged,'diff':await gitops.diff(files.root(tenant_id,project_id,session_id),git_path(path),staged)}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/git/stage')
    async def git_stage(tenant_id:str,project_id:str,payload:GitPaths,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        await gitops.stage(root,[git_path(p) for p in payload.paths],payload.staged)
        return await gitops.status(root)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/git/commit')
    async def git_commit(tenant_id:str,project_id:str,payload:CommitInput,request:Request,session_id:str|None=None):
        """The user's own commit of what they staged. An agent's commits still need Full auto."""
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        output=await gitops.commit(root,payload.message)
        if session_id:
            store.event(tenant_id,session_id,'git.committed',payload.message.splitlines()[0][:120])
        return {'output':output,**await gitops.status(root)}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/git/push')
    async def git_push(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        output=await gitops.push(root)
        if session_id:
            store.event(tenant_id,session_id,'git.pushed',output.splitlines()[-1][:120] if output else 'Pushed.')
        return {'output':output,**await gitops.status(root)}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/git/message')
    async def git_message(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        """A commit message for what is staged, written by an agent under Read only from the diff alone."""
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        diff=await gitops.staged_diff(root)
        if not diff.strip():
            raise ValueError('Stage some changes first.')
        agents=runner.agents(tenant_id)
        if not agents:
            raise ValueError('Connect a Claude, Codex or OpenCode agent to write commit messages.')
        preferred=[]
        if session_id:
            preferred+=[m.get('model_id') for m in reversed(store.get(tenant_id,'sessions',session_id)['messages']) if m.get('role')=='assistant']
        preferred.append(store.get(tenant_id,'projects',project_id).get('last_model_id'))
        config=next((a for pick in preferred if pick and pick!=ADAPTIVE for a in agents if a['id']==pick),agents[0])
        prompt=('Write a git commit message for the staged diff below. Reply with the message only: a summary line '
                'under 72 characters, then optionally a blank line and a short body in plain sentences. No code fences, '
                'no preamble, and do not run any commands.\n\n'+diff)
        result=await runner.broker.invoke_agent(tenant_id,config,prompt,'read',str(root))
        text=re.sub(r'^```[a-z]*\n|```$','',(result.text or '').strip()).strip()
        if not text:
            raise ValueError(f'{config["name"]} returned no message.')
        return {'message':text,'model_name':config['name']}

    @app.get('/api/t/{tenant_id}/projects/{project_id}/sessions')
    def sessions(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        return [s for s in store.list(tenant_id,'sessions') if s['project_id']==project_id]

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions')
    async def new_session(tenant_id:str,project_id:str,payload:SessionInput,request:Request):
        scoped(request,tenant_id)
        project=store.get(tenant_id,'projects',project_id)
        session={'id':uid(),'project_id':project_id,'name':payload.name,'messages':[],'commands':[],'created_at':now(),'updated_at':now()}
        if payload.worktree:
            branch=gitops.branch_name(payload.branch or payload.name,session['id'][:6])
            location=files.worktree_location(tenant_id,project_id,branch)
            await gitops.worktree_add(project['root'],location,branch)
            session['worktree']={'path':str(location),'branch':branch}
        return store.put(tenant_id,'sessions',session)

    @app.delete('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/worktree')
    async def drop_worktree(tenant_id:str,project_id:str,session_id:str,request:Request):
        """Remove the session's worktree folder. Its branch stays; the conversation continues in the project folder."""
        scoped(request,tenant_id)
        session=get_session(tenant_id,project_id,session_id)
        if runner.busy(tenant_id,session_id):
            raise ValueError('Stop the turn that is still working in this worktree first.')
        if session.get('worktree'):
            await gitops.worktree_remove(store.get(tenant_id,'projects',project_id)['root'],session['worktree']['path'])
            session['worktree']=None
        return store.put(tenant_id,'sessions',session)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/messages/{message_id}/revert')
    async def revert_turn(tenant_id:str,project_id:str,session_id:str,message_id:str,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return await runner.revert(tenant_id,project_id,session_id,message_id)

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
        content=payload.content+attachment_note(store,tenant_id,files.root(tenant_id,project_id,session_id),payload.attachment_ids or [])
        if payload.steer and runner.busy(tenant_id,session_id):
            # A turn is one opaque subprocess, so steering means stopping it and sending this instead.
            await runner.cancel(tenant_id,session_id)
        return runner.send(tenant_id,project_id,session_id,content,payload.model_id,payload.mode,queue=payload.queue)

    @app.patch('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}')
    def update_session(tenant_id:str,project_id:str,session_id:str,payload:SessionPatch,request:Request):
        scoped(request,tenant_id)
        session=get_session(tenant_id,project_id,session_id)
        session.update({k:v for k,v in payload.model_dump().items() if v is not None})
        return store.put(tenant_id,'sessions',session)

    @app.delete('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/queue/{item_id}')
    def unqueue(tenant_id:str,project_id:str,session_id:str,item_id:str,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return runner.unqueue(tenant_id,session_id,item_id)

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
