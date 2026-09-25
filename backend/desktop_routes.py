"""Project/session API and a one-use native-launch authentication exchange."""
import asyncio
import base64
import hmac
import json
import os
import re
from pathlib import Path
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse, FileResponse
from .schemas import (ProjectInput, SessionInput, SessionPatch, InstructionInput, TenantInput, CommandInput, GitPaths, CommitInput, FileWrite,
                      McpServerInput, ApprovalDecision, ApprovalRequest, FanoutInput, AutomationInput, ProjectSettings, PrCreateInput,
                      DevServerInput, RewindInput, ImportInput, CloneInput, CatchupInput, ResumeCliInput, BoardTaskInput, BoardTaskPatch, HistoryImportInput, PrReviewInput, PrEditInput, PrMergeInput)
from .store import now, uid, TenantIsolationViolationException
from .projects import ProjectFiles
from . import gitops, automations, pullrequests, devserver, maintenance, board, skill_catalog
from . import vscode_themes as vscode_themes_module
import secrets
from datetime import datetime, timedelta, timezone
from .adaptive import ADAPTIVE
from .agent import context_usage

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
        if attachment.get('kind')=='file' and attachment.get('path'):
            try:
                data=Path(attachment['path']).read_bytes()
            except OSError:
                continue
        else:
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

def context_note(files,tenant_id,project_id,session_id,chips):
    """Typed references from the panels, written out beside the message so the agent sees exactly what the user pointed at."""
    if not chips:
        return ''
    parts=[]
    for chip in chips:
        if chip.kind=='file' and chip.path:
            try:
                text=files.read(tenant_id,project_id,chip.path,session_id)['content']
            except (ValueError,TenantIsolationViolationException):
                parts.append(f'File {chip.path} (could not be read here; open it yourself).')
                continue
            lines=text.splitlines()
            if chip.start:
                start,end=max(1,chip.start),min(len(lines),chip.end or chip.start)
                excerpt='\n'.join(lines[start-1:end])
                parts.append(f'File {chip.path}, lines {start}-{end}:\n```\n{excerpt[:12000]}\n```')
            else:
                parts.append(f'File {chip.path}' + (f' (first 200 of {len(lines)} lines):' if len(lines)>200 else ':') + '\n```\n' + '\n'.join(lines[:200])[:12000] + '\n```')
        elif chip.text:
            label={'terminal':'Terminal output','diff':'Diff','selection':'Selected text'}.get(chip.kind,'Context')
            if chip.path:
                label+=f' from {chip.path}'
            parts.append(f'{label}:\n```\n{chip.text[:12000]}\n```')
    return ('\n\nREFERENCED CONTEXT (the user pointed at these while writing the message):\n'+'\n\n'.join(parts)) if parts else ''

def shown(project):
    """A project as the interface sees it: a kept secret appears only as whether it is set."""
    token=project.get('agent_browser_token')
    return {**{k:v for k,v in project.items() if k!='agent_browser_token'},'agent_browser_token_set':bool(token)}

STATE_RANK={'working':3,'waiting':2,'done':1}  # Which state a project shows when its sessions differ.

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
        return [shown(p) for p in store.list(tenant_id,'projects')]

    def writer_for(tenant_id,project_id,session_id):
        """The agent that writes a commit message, a pull request or a summary for this conversation."""
        return runner.writer(tenant_id,store.get(tenant_id,'sessions',session_id) if session_id else {'messages':[]},store.get(tenant_id,'projects',project_id))

    @app.get('/api/t/{tenant_id}/dashboard')
    async def dashboard(tenant_id:str,request:Request):
        """Every project at a glance: what its agents are doing, its dev server, its branch, its board, and when it last moved."""
        scoped(request,tenant_id)
        projects,sessions,tasks=await asyncio.to_thread(lambda:(store.list(tenant_id,'projects'),store.session_summaries(tenant_id),store.list(tenant_id,'board')))
        active=runner.active_sessions(tenant_id)
        by_project,task_counts={},{}
        for s in sessions:
            if not s.get('team_parent') and not s.get('archived'):
                by_project.setdefault(s['project_id'],[]).append(s)
        for t in tasks:
            counts=task_counts.setdefault(t.get('project_id'),dict.fromkeys(board.STATUSES,0))
            if t.get('status') in counts:
                counts[t['status']]+=1
        async def git(root):
            try:
                found=await asyncio.wait_for(gitops.status(root),5)
            except (gitops.GitError,OSError,TimeoutError):
                return None
            return {'branch':found['branch'],'changes':len(found['entries']),'ahead':found['ahead'],'behind':found['behind']} if found.get('repo') else None
        gits=await asyncio.gather(*(git(p['root']) if Path(p['root']).is_dir() else asyncio.sleep(0) for p in projects))
        rows=[]
        for project,repo in zip(projects,gits):
            mine=by_project.get(project['id'],[])
            states=[st for st in (session_sidebar_state(s,active) for s in mine) if st]
            latest=max(mine,key=lambda s:s.get('updated_at') or '',default=None)
            reply=next((m for m in reversed((latest or {}).get('messages') or []) if m.get('content')),None)
            server=devserver.get(project['root'])
            dev={k:v for k,v in server.status().items() if k!='output'} if server else {'running':False}
            counts=task_counts.get(project['id'])or dict.fromkeys(board.STATUSES,0)
            rows.append({'id':project['id'],'name':project['name'],'root':project['root'],'missing':not Path(project['root']).is_dir(),
                         'state':max(states,key=STATE_RANK.get) if states else None,'working':sum(1 for s in states if s=='working'),
                         'sessions':len(mine),'last_activity':(latest or {}).get('updated_at') or project.get('updated_at'),
                         'last_session':{'id':latest['id'],'name':latest['name'],'snippet':' '.join((reply or {}).get('content','').split())[:160]} if latest else None,
                         'dev':{**dev,'command':project.get('dev_command')},'git':repo,'board':counts})
        return sorted(rows,key=lambda r:r['last_activity'] or '',reverse=True)

    @app.get('/api/t/{tenant_id}/activity')
    def activity(tenant_id:str,request:Request):
        """Conversation status for the sidebar, across projects."""
        scoped(request,tenant_id)
        summaries=store.session_summaries(tenant_id)
        active_sessions=runner.active_sessions(tenant_id)
        active=[{'project_id':s['project_id'],'session_id':s['id']} for s in summaries if s['id'] in active_sessions]
        sessions=[]
        project_states={}
        for session in summaries:
            state=session_sidebar_state(session,active_sessions)
            if not state:
                continue
            item={'project_id':session['project_id'],'session_id':session['id'],'state':state}
            sessions.append(item)
            current=project_states.get(session['project_id'])
            if not current or STATE_RANK[state]>STATE_RANK[current]:
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
        # Its automations, board and scheduled work go with it; left behind they would fail every time they came due.
        for kind in ('automations','board'):
            for item in store.list(tenant_id,kind):
                if item.get('project_id')==project_id:
                    store.delete(tenant_id,kind,item['id'])
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
        config=writer_for(tenant_id,project_id,session_id)
        prompt=('Write a git commit message for the staged diff below. Reply with the message only: a summary line '
                'under 72 characters, then optionally a blank line and a short body in plain sentences. No code fences, '
                'no preamble, and do not run any commands.\n\n'+diff)
        result=await runner.broker.invoke_agent(tenant_id,config,prompt,'read',str(root),runner.protections(tenant_id,project_id))
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
            copied=await gitops.worktree_add(project['root'],location,branch,copy=project.get('worktree_copy') or [])
            session['worktree']={'path':str(location),'branch':branch,'copied':copied}
            if project.get('worktree_setup'):
                session['setup']=await files.run_setup(location,project['worktree_setup'])
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
        session=get_session(tenant_id,project_id,session_id)
        return {**session,'context':context_usage(session),'approvals':runner.pending(tenant_id,session_id)}

    # ── Approvals: a running Claude turn asks through its permission tool, the user answers here ──
    @app.post('/internal/approvals')
    def approval_request(payload:ApprovalRequest,request:Request):
        """Called by the turn's own permission tool over loopback, authenticated by the turn token."""
        approval_id=runner.request_approval(request.headers.get('x-frontier-turn',''),payload.tool_name,payload.input,payload.tool_use_id,payload.kind)
        return {'id':approval_id}

    @app.get('/internal/approvals/{approval_id}')
    async def approval_wait(approval_id:str,request:Request,wait:float=0):
        token=request.headers.get('x-frontier-turn','')
        approval=runner.approvals.get(approval_id)
        if not approval or runner.turn_tokens.get(token,(None,None,None))[2]!=approval['session_id']:
            raise HTTPException(404,'No such request.')
        return await runner.approval_state(approval_id,wait)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/approvals/{approval_id}')
    def approval_decide(tenant_id:str,project_id:str,session_id:str,approval_id:str,payload:ApprovalDecision,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        runner.decide(tenant_id,session_id,approval_id,payload.allow,payload.message)
        return {'ok':True}

    # ── MCP servers the workspace hands to every agent that can take them ──
    @app.get('/api/t/{tenant_id}/mcp')
    def mcp_list(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return store.list(tenant_id,'mcp_servers')

    @app.post('/api/t/{tenant_id}/mcp')
    @app.put('/api/t/{tenant_id}/mcp/{server_id}')
    def mcp_save(tenant_id:str,payload:McpServerInput,request:Request,server_id:str|None=None):
        scoped(request,tenant_id)
        existing=store.get(tenant_id,'mcp_servers',server_id) if server_id else {'id':uid(),'created_at':now()}
        if any(s['name']==payload.name and s['id']!=existing['id'] for s in store.list(tenant_id,'mcp_servers')):
            raise ValueError('Another server already has that name.')
        return store.put(tenant_id,'mcp_servers',{**existing,**payload.model_dump()})

    @app.delete('/api/t/{tenant_id}/mcp/{server_id}')
    def mcp_delete(tenant_id:str,server_id:str,request:Request):
        scoped(request,tenant_id)
        store.delete(tenant_id,'mcp_servers',server_id)
        return {'ok':True}

    # ── Skills and commands the agents discover on their own; listed so the composer can offer them ──
    def read_skills(root):
        found=[]
        def describe(file):
            try:
                text=file.read_text(encoding='utf-8',errors='replace')
            except OSError:
                return ''
            m=re.search(r'^description:\s*(.+)$',text,re.M)
            if m:
                return m.group(1).strip().strip('"\'')[:200]
            body=re.sub(r'^---.*?---\s*','',text,flags=re.S)
            return next((line.strip() for line in body.splitlines() if line.strip() and not line.startswith('#')),'')[:200]
        for scope,base in [('project',Path(root)),('user',Path.home())]:
            for provider,folder in [('claude','.claude'),('codex','.codex'),('codex','.agents'),('gemini','.gemini')]:
                home=base/folder
                for skill in sorted((home/'skills').glob('*/SKILL.md')) if (home/'skills').is_dir() else []:
                    found.append({'name':skill.parent.name,'kind':'skill','scope':scope,'provider':provider,'description':describe(skill),'path':str(skill)})
                for command in sorted((home/'commands').glob('*.md')) if (home/'commands').is_dir() else []:
                    found.append({'name':command.stem,'kind':'command','scope':scope,'provider':provider,'description':describe(command),'path':str(command)})
        return found

    # ── Skills Frontier can install into a project ──
    @app.get('/api/t/{tenant_id}/projects/{project_id}/skill-catalog')
    def skill_catalog_list(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        return skill_catalog.listing(files.root(tenant_id,project_id,None))

    @app.post('/api/t/{tenant_id}/projects/{project_id}/skill-catalog/{name}')
    def skill_catalog_install(tenant_id:str,project_id:str,name:str,request:Request):
        scoped(request,tenant_id)
        try:
            return skill_catalog.install(files.root(tenant_id,project_id,None),name)
        except KeyError:
            raise HTTPException(404,'No such skill in the catalog.') from None

    @app.delete('/api/t/{tenant_id}/projects/{project_id}/skill-catalog/{name}')
    def skill_catalog_remove(tenant_id:str,project_id:str,name:str,request:Request):
        scoped(request,tenant_id)
        try:
            return skill_catalog.remove(files.root(tenant_id,project_id,None),name)
        except KeyError:
            raise HTTPException(404,'No such skill in the catalog.') from None

    @app.get('/api/t/{tenant_id}/projects/{project_id}/skills')
    def skills(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return read_skills(files.root(tenant_id,project_id,session_id))

    # ── Usage: what every turn cost, from the sessions themselves ──
    @app.get('/api/t/{tenant_id}/usage')
    def usage(tenant_id:str,request:Request,days:int=30):
        scoped(request,tenant_id)
        since=datetime.now(timezone.utc)-timedelta(days=max(1,min(days,365)))
        projects={p['id']:p['name'] for p in store.list(tenant_id,'projects')}
        by_model,by_day,by_project={},{},{}
        totals={'turns':0,'input_tokens':0,'output_tokens':0,'cost':0.0,'seconds':0}
        def add(row,message,seconds):
            row['turns']+=1;row['input_tokens']+=message.get('input_tokens') or 0;row['output_tokens']+=message.get('output_tokens') or 0
            row['cost']+=message.get('cost') or 0.0;row['seconds']+=seconds
        def bump(bucket,key,label,message,seconds):
            add(bucket.setdefault(key,{'key':key,'label':label,'turns':0,'input_tokens':0,'output_tokens':0,'cost':0.0,'seconds':0}),message,seconds)
        for session in store.list(tenant_id,'sessions'):
            for message in session.get('messages') or []:
                if message.get('role')!='assistant' or not message.get('finished_at'):
                    continue
                try:
                    finished=datetime.fromisoformat(message['finished_at'])
                    started=datetime.fromisoformat(message['created_at'])
                except ValueError:
                    continue
                if finished.tzinfo is None:
                    finished=finished.replace(tzinfo=timezone.utc);started=started.replace(tzinfo=timezone.utc)
                if finished<since:
                    continue
                seconds=max(0,int((finished-started).total_seconds()))
                bump(by_model,message.get('model_id') or 'unknown',message.get('model_name') or 'Unknown',message,seconds)
                bump(by_day,finished.date().isoformat(),finished.date().isoformat(),message,seconds)
                bump(by_project,session['project_id'],projects.get(session['project_id'],'Removed project'),message,seconds)
                add(totals,message,seconds)
        return {'days':days,'totals':totals,'models':sorted(by_model.values(),key=lambda r:-r['turns']),
                'projects':sorted(by_project.values(),key=lambda r:-r['turns']),'series':sorted(by_day.values(),key=lambda r:r['key'])}

    # ── Preview: which local dev servers are listening ──
    @app.get('/api/t/{tenant_id}/projects/{project_id}/ports')
    async def ports(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        candidates=[3000,3001,3333,4000,4200,4321,5000,5173,5174,5500,5555,6006,7860,8000,8080,8081,8501,8787,8888,9000]
        async def probe(port):
            try:
                _,writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',port),0.25)
                writer.close()
                return port
            except (OSError,TimeoutError):
                return None
        found=await asyncio.gather(*(probe(p) for p in candidates))
        return {'ports':[p for p in found if p]}

    # ── Fan-out: one message to several agents, each in its own worktree ──
    @app.post('/api/t/{tenant_id}/projects/{project_id}/fanout')
    async def fanout(tenant_id:str,project_id:str,payload:FanoutInput,request:Request):
        scoped(request,tenant_id)
        return await runner.fanout(tenant_id,project_id,payload.content,payload.model_ids,payload.mode)

    # ── Automations: scheduled or webhook-triggered turns ──
    @app.get('/api/t/{tenant_id}/automations')
    def automation_list(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return store.list(tenant_id,'automations')

    @app.post('/api/t/{tenant_id}/automations')
    @app.put('/api/t/{tenant_id}/automations/{automation_id}')
    def automation_save(tenant_id:str,payload:AutomationInput,request:Request,automation_id:str|None=None):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',payload.project_id)
        if payload.model_id!=ADAPTIVE:
            runner.select(tenant_id,payload.model_id)
        existing=store.get(tenant_id,'automations',automation_id) if automation_id else {'id':uid(),'created_at':now(),'secret':secrets.token_urlsafe(24),'runs':0}
        automation={**existing,**payload.model_dump()}
        automation['next_run_at']=automations.stamp(automations.next_run(automation)) if automation['enabled'] else None
        return store.put(tenant_id,'automations',automation)

    @app.delete('/api/t/{tenant_id}/automations/{automation_id}')
    def automation_delete(tenant_id:str,automation_id:str,request:Request):
        scoped(request,tenant_id)
        store.delete(tenant_id,'automations',automation_id)
        return {'ok':True}

    @app.post('/api/t/{tenant_id}/automations/{automation_id}/run')
    async def automation_run(tenant_id:str,automation_id:str,request:Request):
        scoped(request,tenant_id)
        return await automations.run(store,runner,tenant_id,store.get(tenant_id,'automations',automation_id),trigger='manual')

    @app.post('/api/hooks/{automation_id}/{secret}')
    async def automation_hook(automation_id:str,secret:str):
        """A webhook: no cookie, the secret in the URL is the whole credential. Same response whether or not it exists."""
        with store.db() as db:
            tenants=[row['id'] for row in db.execute('SELECT id FROM tenants').fetchall()]
        for tenant_id in tenants:
            try:
                automation=store.get(tenant_id,'automations',automation_id)
            except TenantIsolationViolationException:
                continue
            if hmac.compare_digest(automation.get('secret',''),secret) and automation.get('enabled'):
                session=await automations.run(store,runner,tenant_id,automation,trigger='webhook')
                return {'ok':True,'session_id':session['id']}
        raise HTTPException(404,'No such hook.')

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/compact')
    async def compact(tenant_id:str,project_id:str,session_id:str,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return await runner.compact(tenant_id,project_id,session_id)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/team/continue')
    async def continue_team(tenant_id:str,project_id:str,session_id:str,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return runner.continue_team(tenant_id,project_id,session_id)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/instructions')
    async def instruct(tenant_id:str,project_id:str,session_id:str,payload:InstructionInput,request:Request):
        """One message, one agentic turn. The reply arrives in the session, not in this response."""
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        content=payload.content+context_note(files,tenant_id,project_id,session_id,payload.context)+attachment_note(store,tenant_id,files.root(tenant_id,project_id,session_id),payload.attachment_ids or [])
        if payload.steer and runner.busy(tenant_id,session_id):
            # A turn is one opaque subprocess, so steering means stopping it and sending this instead.
            await runner.cancel(tenant_id,session_id)
        chips=[{'kind':c.kind,'path':c.path,'start':c.start,'end':c.end,'label':c.label or c.path or c.kind} for c in payload.context]
        return runner.send(tenant_id,project_id,session_id,content,payload.model_id,payload.mode,queue=payload.queue,team=payload.team,context=chips)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/messages/{message_id}/rewind')
    async def rewind(tenant_id:str,project_id:str,session_id:str,message_id:str,payload:RewindInput,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return await runner.rewind(tenant_id,project_id,session_id,message_id,payload.restore_files)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}/remember')
    async def remember(tenant_id:str,project_id:str,session_id:str,request:Request):
        scoped(request,tenant_id)
        get_session(tenant_id,project_id,session_id)
        return await runner.remember(tenant_id,project_id,session_id)

    # ── Project settings, credentials, import ──
    @app.put('/api/t/{tenant_id}/projects/{project_id}/settings')
    def project_settings(tenant_id:str,project_id:str,payload:ProjectSettings,request:Request):
        scoped(request,tenant_id)
        project=store.get(tenant_id,'projects',project_id)
        if payload.default_model_id and payload.default_model_id!=ADAPTIVE:
            runner.select(tenant_id,payload.default_model_id)
        fields=payload.model_dump(exclude_unset=True)  # Only what was sent; a toggle elsewhere must not wipe the rest.
        if 'agent_browser_token' in fields:
            # The extension shows it as PLAYWRIGHT_MCP_EXTENSION_TOKEN=<token>; either form may be pasted.
            token=(fields.pop('agent_browser_token') or '').strip().removeprefix('PLAYWRIGHT_MCP_EXTENSION_TOKEN=').strip().strip('"\'')
            project['agent_browser_token']=store.encrypt(token) if token else None
        project.update(fields)
        return shown(store.put(tenant_id,'projects',project))

    # ── Screenshots the agent's browser took, newest first ──
    SHOT_TYPES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'}
    @app.get('/api/t/{tenant_id}/projects/{project_id}/browser-shots')
    def browser_shots(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        folder=files.root(tenant_id,project_id,session_id)/'.frontier'/'browser'
        if not folder.is_dir():
            return []
        shots=[f for f in folder.iterdir() if f.is_file() and not f.is_symlink() and f.suffix.lower() in SHOT_TYPES]
        shots.sort(key=lambda f:f.stat().st_mtime,reverse=True)
        return [{'name':f.name,'modified':datetime.fromtimestamp(f.stat().st_mtime,timezone.utc).isoformat(),'size':f.stat().st_size} for f in shots[:40]]

    @app.get('/api/t/{tenant_id}/projects/{project_id}/browser-shot')
    def browser_shot(tenant_id:str,project_id:str,name:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        if not re.match(r'^[\w .()-]+$',name) or Path(name).suffix.lower() not in SHOT_TYPES:
            raise ValueError('Not a screenshot name.')
        target=files.root(tenant_id,project_id,session_id)/'.frontier'/'browser'/name
        if not target.is_file() or target.is_symlink():
            raise HTTPException(404,'No such screenshot.')
        return FileResponse(target,media_type=SHOT_TYPES[target.suffix.lower()])

    # ── Since you were last here ──
    @app.post('/api/t/{tenant_id}/projects/{project_id}/catchup')
    async def catchup(tenant_id:str,project_id:str,payload:CatchupInput,request:Request):
        """What happened in a project since a moment: turns, files, commits, automation runs; summarised on request."""
        scoped(request,tenant_id)
        project=store.get(tenant_id,'projects',project_id)
        since=payload.since
        turns=[]
        for session in store.list(tenant_id,'sessions'):
            if session['project_id']!=project_id:
                continue
            for index,message in enumerate(session.get('messages') or []):
                if message.get('role')!='assistant' or (message.get('created_at') or '')<since:
                    continue
                asked=next((m['content'] for m in reversed(session['messages'][:index]) if m['role']=='user'),'')
                turns.append({'session':session['name'],'agent':message.get('model_name'),'status':message.get('status'),'at':message.get('created_at'),
                              'asked':asked.split('\n\nREFERENCED CONTEXT')[0][:300],'files':[c['path'] for c in message.get('changes') or []][:20],
                              'reply':(message.get('content') or '')[:500]})
        turns.sort(key=lambda t:t['at'] or '')
        commits=[]
        if await gitops.toplevel(project['root']):
            try:
                log=await gitops.run(project['root'],'log','--pretty=format:%h%x09%an%x09%cI%x09%s','-n','200')
                # Filtered here rather than with --since, which git silently ignores for dates it cannot parse.
                moment=datetime.fromisoformat(since)
                for line in log.splitlines():
                    parts=line.split('\t',3)
                    if len(parts)==4 and datetime.fromisoformat(parts[2])>=moment:
                        commits.append({'hash':parts[0],'author':parts[1],'date':parts[2][:10],'subject':parts[3]})
                commits=commits[:60]
            except gitops.GitError:
                commits=[]
        runs=[{'name':a['name'],'at':a.get('last_run_at'),'outcome':a.get('last_outcome')} for a in store.list(tenant_id,'automations') if a.get('project_id')==project_id and (a.get('last_run_at') or '')>=since]
        files_touched=sorted({f for t in turns for f in t['files']})
        result={'since':since,'counts':{'turns':len(turns),'commits':len(commits),'files':len(files_touched),'automation_runs':len(runs)},
                'turns':turns[-40:],'commits':commits,'files':files_touched[:80],'automation_runs':runs,'summary':None,'model_name':None}
        if payload.summarize and (turns or commits or runs):
            config=runner.writer(tenant_id,{'messages':[]},project)
            facts=json.dumps({k:result[k] for k in ('turns','commits','files','automation_runs')},indent=1)[:40000]
            prompt=('The user is coming back to this project. Using only the facts below, write a short catch-up in markdown: '
                    'what was done and by which agent, what changed in the code, what was left unfinished or failed, and the most '
                    'sensible next step. Under 250 words. Do not run commands or change files.\n\nFACTS SINCE '+since+':\n'+facts)
            reply=await runner.broker.invoke_agent(tenant_id,config,prompt,'read',project['root'],runner.protections(tenant_id,project['id']))
            result.update(summary=(reply.text or '').strip(),model_name=config['name'])
        return result

    # ── How other programs start Frontier's MCP server ──
    @app.get('/api/t/{tenant_id}/mcp-self')
    def mcp_self(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        import sys
        if getattr(sys,'frozen',False):
            command,args=sys.executable,['--mcp-server']
        else:
            command,args=sys.executable,['-m','backend.desktop','--mcp-server']
        quoted=' '.join(['"'+command+'"',*args])
        return {'command':command,'args':args,'cwd':None if getattr(sys,'frozen',False) else str(Path(__file__).resolve().parents[1]),
                'claude':f'claude mcp add frontier --scope user -- {quoted}','codex':f'codex mcp add frontier -- {quoted}',
                'json':{'mcpServers':{'frontier':{'command':command,'args':args}}}}

    # ── First-run setup: which agent tools this computer has ──
    def projects_by_folder(tenant_id):
        return {str(Path(p['root']).resolve()).lower():p for p in store.list(tenant_id,'projects')}

    @app.get('/api/t/{tenant_id}/setup/history')
    async def setup_history(tenant_id:str,request:Request):
        """Folders with past Claude and Codex conversations, and whether each is a project here yet."""
        scoped(request,tenant_id)
        folders=await asyncio.to_thread(maintenance.history_folders)
        known={key:p['id'] for key,p in projects_by_folder(tenant_id).items()}
        return [{**f,'project_id':known.get(f['root'].lower())} for f in folders]

    @app.post('/api/t/{tenant_id}/setup/history')
    async def setup_history_import(tenant_id:str,payload:HistoryImportInput,request:Request):
        """Each chosen folder becomes a project, if it is not one already, and its conversations come in."""
        scoped(request,tenant_id)
        known=projects_by_folder(tenant_id)
        done=[]
        for root in payload.roots:
            project=known.get(str(Path(root).resolve()).lower()) or files.create(tenant_id,Path(root).name or root,root)
            imported=await asyncio.to_thread(maintenance.import_history,store,tenant_id,project)
            done.append({'project_id':project['id'],'name':project['name'],'imported':len(imported)})
        return done

    @app.get('/api/t/{tenant_id}/themes/vscode')
    async def vscode_themes(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return await asyncio.to_thread(vscode_themes_module.installed)

    @app.get('/api/t/{tenant_id}/themes/vscode/theme')
    async def vscode_theme(tenant_id:str,path:str,request:Request):
        scoped(request,tenant_id)
        try:
            return await asyncio.to_thread(vscode_themes_module.load,path)
        except KeyError:
            raise HTTPException(404,'That theme is not one of the installed themes.') from None
        except (OSError,ValueError) as exc:
            raise HTTPException(422,f'The theme file could not be read: {exc}') from None

    @app.get('/api/t/{tenant_id}/setup/detect')
    async def setup_detect(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return await maintenance.detect()

    @app.get('/api/t/{tenant_id}/projects/{project_id}/environment')
    def environment(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return files.python_env(files.root(tenant_id,project_id,session_id)) or {'kind':None}

    @app.post('/api/t/{tenant_id}/projects/clone')
    async def clone_project(tenant_id:str,payload:CloneInput,request:Request):
        """A repository URL becomes a managed project folder with the clone in it."""
        scoped(request,tenant_id)
        return await files.clone(tenant_id,payload.url.strip(),payload.name)

    @app.get('/api/t/{tenant_id}/projects/{project_id}/credentials')
    def credentials(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return files.credentials(tenant_id,project_id,session_id)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/import')
    def import_history(tenant_id:str,project_id:str,payload:ImportInput,request:Request):
        scoped(request,tenant_id)
        return maintenance.import_history(store,tenant_id,store.get(tenant_id,'projects',project_id),payload.sources)

    # ── The project's task board ──
    def turn_of(request):
        turn=runner.turn_tokens.get(request.headers.get('x-frontier-turn',''))
        if not turn:
            raise HTTPException(403,'This turn is not running.')
        return turn

    def agent_name(tenant_id,session_id,message_id):
        session=store.get(tenant_id,'sessions',session_id)
        message=next((m for m in session['messages'] if m['id']==message_id),{})
        return message.get('model_name') or 'agent',session

    @app.get('/internal/board')
    def board_for_turn(request:Request):
        tenant_id,project_id,_,_=turn_of(request)
        return board.tasks(store,tenant_id,project_id)

    @app.post('/internal/board')
    def board_add_for_turn(payload:BoardTaskInput,request:Request):
        tenant_id,project_id,session_id,message_id=turn_of(request)
        name,session=agent_name(tenant_id,session_id,message_id)
        return board.add(store,tenant_id,project_id,payload.title,payload.notes,payload.status,f'{name} · {session["name"]}',session_id)

    @app.post('/internal/board/{task_id}')
    def board_update_for_turn(task_id:str,payload:BoardTaskPatch,request:Request):
        tenant_id,project_id,session_id,message_id=turn_of(request)
        name,_=agent_name(tenant_id,session_id,message_id)
        try:
            return board.update(store,tenant_id,project_id,task_id,by=name,**payload.model_dump(exclude_unset=True))
        except (KeyError,TenantIsolationViolationException):
            raise HTTPException(404,'No task with that id on this project’s board.') from None

    @app.get('/api/t/{tenant_id}/projects/{project_id}/board')
    def board_list(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        return board.tasks(store,tenant_id,project_id)

    @app.post('/api/t/{tenant_id}/projects/{project_id}/board')
    def board_add(tenant_id:str,project_id:str,payload:BoardTaskInput,request:Request):
        scoped(request,tenant_id)
        store.get(tenant_id,'projects',project_id)
        return board.add(store,tenant_id,project_id,payload.title,payload.notes,payload.status)

    @app.put('/api/t/{tenant_id}/projects/{project_id}/board/{task_id}')
    def board_update(tenant_id:str,project_id:str,task_id:str,payload:BoardTaskPatch,request:Request):
        scoped(request,tenant_id)
        try:
            return board.update(store,tenant_id,project_id,task_id,by='you',**payload.model_dump(exclude_unset=True))
        except KeyError:
            raise HTTPException(404,'No such task.') from None

    @app.delete('/api/t/{tenant_id}/projects/{project_id}/board/{task_id}')
    def board_delete(tenant_id:str,project_id:str,task_id:str,request:Request):
        scoped(request,tenant_id)
        if store.get(tenant_id,'board',task_id).get('project_id')!=project_id:
            raise HTTPException(404,'No such task.')
        store.delete(tenant_id,'board',task_id)
        return {'deleted':task_id}

    @app.get('/api/t/{tenant_id}/projects/{project_id}/cli-sessions')
    async def cli_sessions(tenant_id:str,project_id:str,request:Request):
        scoped(request,tenant_id)
        return await asyncio.to_thread(maintenance.cli_conversations,store,tenant_id,store.get(tenant_id,'projects',project_id))

    @app.post('/api/t/{tenant_id}/projects/{project_id}/cli-sessions/resume')
    async def cli_resume(tenant_id:str,project_id:str,payload:ResumeCliInput,request:Request):
        scoped(request,tenant_id)
        try:
            return await asyncio.to_thread(maintenance.resume_cli,store,tenant_id,store.get(tenant_id,'projects',project_id),payload.source,payload.key)
        except LookupError as exc:
            raise HTTPException(404,str(exc)) from None

    @app.get('/api/t/{tenant_id}/providers/limits')
    async def provider_limits(tenant_id:str,request:Request,refresh:bool=False):
        scoped(request,tenant_id)
        # The machine's own sign-in for each tool, plus every named login this workspace connected.
        from .broker import account_home
        logins=list(maintenance.DEFAULT_LOGINS)
        for model in store.list(tenant_id,'models'):
            account=model.get('account')
            if account and model.get('provider') in maintenance.READERS_LIMITS and not any(l['provider']==model['provider'] and l['name']==account for l in logins):
                logins.append({'provider':model['provider'],'name':account,'home':str(account_home(store,tenant_id,model['provider'],account))})
        return await asyncio.to_thread(maintenance.subscription_limits,refresh,logins)

    @app.get('/api/t/{tenant_id}/providers/versions')
    async def provider_versions(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return await maintenance.versions()

    @app.post('/api/t/{tenant_id}/providers/{provider}/update')
    async def provider_update(tenant_id:str,provider:str,request:Request):
        scoped(request,tenant_id)
        return {'version':await maintenance.update(provider)}

    # ── Pull requests through gh ──
    @app.get('/api/t/{tenant_id}/projects/{project_id}/pr')
    async def pr_status(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        if not pullrequests.available():
            return {'available':False,'pr':None}
        try:
            pr=await pullrequests.current(root)
        except gitops.GitError as exc:
            return {'available':True,'pr':None,'error':str(exc)}
        if session_id and pr:
            session=store.get(tenant_id,'sessions',session_id)
            changed=(session.get('pull_request') or {}).get('number')!=pr['number'] or session['pull_request'].get('state')!=pr['state']
            if changed:
                session['pull_request']=pullrequests.summary(pr)
            if pr['state']=='merged' and not session.get('archived') and not session.get('pinned') and not runner.busy(tenant_id,session_id):
                # Its branch is in; the conversation settles on its own, as a merged thread would in T3.
                session['archived']=True
                session['archived_reason']=f'pull request #{pr["number"]} merged'
                changed=True
            if changed:
                store.put(tenant_id,'sessions',session)
        return {'available':True,'pr':pr}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/pr')
    async def pr_create(tenant_id:str,project_id:str,payload:PrCreateInput,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        pr=await pullrequests.create(root,payload.title,payload.body,payload.base,payload.draft,payload.reviewers,payload.labels)
        if session_id and pr:
            session=store.get(tenant_id,'sessions',session_id)
            session['pull_request']=pullrequests.summary(pr)
            store.put(tenant_id,'sessions',session)
            store.event(tenant_id,session_id,'pr.opened',f'Opened pull request #{pr["number"]}.')
        return {'available':True,'pr':pr}

    async def open_pr(tenant_id,project_id,session_id):
        root=files.root(tenant_id,project_id,session_id)
        pr=await pullrequests.current(root)
        if not pr:
            raise ValueError('This branch has no pull request.')
        return root,pr

    @app.get('/api/t/{tenant_id}/projects/{project_id}/pr/options')
    async def pr_options(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        return await pullrequests.options(files.root(tenant_id,project_id,session_id))

    @app.post('/api/t/{tenant_id}/projects/{project_id}/pr/review')
    async def pr_review(tenant_id:str,project_id:str,payload:PrReviewInput,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root,pr=await open_pr(tenant_id,project_id,session_id)
        await pullrequests.review(root,pr['number'],payload.event,payload.body)
        return {'pr':await pullrequests.current(root)}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/pr/review/draft')
    async def pr_review_draft(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        """A review of the pull request's diff, written by an agent under Read only, for the user to edit and submit."""
        scoped(request,tenant_id)
        root,pr=await open_pr(tenant_id,project_id,session_id)
        diff=await pullrequests.diff(root,pr['number'])
        config=writer_for(tenant_id,project_id,session_id)
        prompt=(f'Review pull request #{pr["number"]} "{pr["title"]}" from its diff below; you may read files in the project for context but change nothing. '
                'On the first line write exactly one of APPROVE, COMMENT or REQUEST_CHANGES: request changes only for a real defect. Then a blank line, '
                'then the review in markdown: a one-paragraph summary, then each finding with file and line, what goes wrong and the fix, most serious first. '
                'Do not invent problems; if it is good, say so briefly.\n\nDIFF:\n'+diff)
        result=await runner.broker.invoke_agent(tenant_id,config,prompt,'read',str(root),runner.protections(tenant_id,project_id))
        text=(result.text or '').strip()
        first,_,rest=text.partition('\n')
        event={'APPROVE':'approve','REQUEST_CHANGES':'request_changes','COMMENT':'comment'}.get(first.strip().strip('*').upper().replace(' ','_'))
        return {'event':event or 'comment','body':(rest if event else text).strip()[:60000],'model_name':config['name']}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/pr/edit')
    async def pr_edit(tenant_id:str,project_id:str,payload:PrEditInput,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root,pr=await open_pr(tenant_id,project_id,session_id)
        await pullrequests.edit(root,pr['number'],payload.add_reviewers,payload.remove_reviewers,payload.add_labels,payload.remove_labels,payload.base)
        return {'pr':await pullrequests.current(root)}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/pr/merge')
    async def pr_merge(tenant_id:str,project_id:str,payload:PrMergeInput,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root,pr=await open_pr(tenant_id,project_id,session_id)
        await pullrequests.merge(root,pr['number'],payload.method,payload.auto,payload.delete_branch,payload.disable_auto)
        if session_id:
            text=('Turned off auto-merge' if payload.disable_auto else f'Turned on auto-merge ({payload.method})' if payload.auto else f'Merged ({payload.method})')+f' for pull request #{pr["number"]}.'
            store.event(tenant_id,session_id,'pr.merge',text)
        return {'pr':await pullrequests.current(root)}

    @app.get('/api/t/{tenant_id}/projects/{project_id}/pr/comments')
    async def pr_comments(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root,pr=await open_pr(tenant_id,project_id,session_id)
        feedback=await pullrequests.review_comments(root,pr['number'])
        return {**feedback,'pr':pr,'prompt':pullrequests.address_prompt(pr,feedback)}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/pr/description')
    async def pr_description(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        """A title and body for the pull request, drafted by the conversation's agent from the branch's commits and diff."""
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        current=await gitops.status(root)
        base=None
        for candidate in ('origin/main','origin/master','main','master'):
            try:
                await gitops.run(root,'rev-parse','--verify','-q',candidate)
                base=candidate
                break
            except gitops.GitError:
                continue
        if not base:
            raise ValueError('No main or master branch to compare against.')
        log=await gitops.run(root,'log','--oneline',f'{base}..HEAD')
        stat=await gitops.run(root,'diff','--stat',f'{base}...HEAD')
        body=await gitops.run(root,'diff',f'{base}...HEAD')
        if not (log.strip() or stat.strip()):
            raise ValueError('This branch has no commits beyond the base yet. Commit first.')
        config=writer_for(tenant_id,project_id,session_id)
        prompt=('Write a pull request title and description for the branch changes below. Reply with the title on the first line, then a blank line, '
                'then a description in markdown: what changed and why, how it was tested, anything reviewers should look at. No code fences, no preamble, do not run commands.'
                f'\n\nCOMMITS:\n{log[:4000]}\n\nFILES:\n{stat[:4000]}\n\nDIFF:\n{body[:24000]}')
        result=await runner.broker.invoke_agent(tenant_id,config,prompt,'read',str(root),runner.protections(tenant_id,project_id))
        text=(result.text or '').strip()
        title,_,rest=text.partition('\n')
        return {'title':title.strip().lstrip('#').strip()[:200] or current.get('branch'),'body':rest.strip()[:20000],'model_name':config['name']}

    # ── The project's dev server ──
    @app.get('/api/t/{tenant_id}/projects/{project_id}/devserver')
    def devserver_status(tenant_id:str,project_id:str,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        server=devserver.get(root)
        return server.status() if server else {'root':str(root),'running':False,'command':store.get(tenant_id,'projects',project_id).get('dev_command')}

    @app.post('/api/t/{tenant_id}/projects/{project_id}/devserver')
    async def devserver_control(tenant_id:str,project_id:str,payload:DevServerInput,request:Request,session_id:str|None=None):
        scoped(request,tenant_id)
        root=files.root(tenant_id,project_id,session_id)
        project=store.get(tenant_id,'projects',project_id)
        if payload.action=='kill_port':
            if not payload.port:
                raise ValueError('Say which port to free.')
            return {'killed':await devserver.kill_port(payload.port)}
        if payload.action=='stop':
            server=await devserver.stop(root)
            return server.status() if server else {'root':str(root),'running':False}
        command=payload.command or project.get('dev_command')
        if not command:
            raise ValueError('Set the dev server command in the project settings first, such as npm run dev.')
        if payload.command and payload.command!=project.get('dev_command'):
            project['dev_command']=payload.command
            store.put(tenant_id,'projects',project)
        if payload.action=='restart':
            await devserver.stop(root)
        server=await devserver.start(root,command)
        return server.status()

    @app.patch('/api/t/{tenant_id}/projects/{project_id}/sessions/{session_id}')
    def update_session(tenant_id:str,project_id:str,session_id:str,payload:SessionPatch,request:Request):
        scoped(request,tenant_id)
        session=get_session(tenant_id,project_id,session_id)
        session.update({k:v for k,v in payload.model_dump().items() if v is not None and k!='snoozed_until'})
        if payload.snoozed_until is not None:
            if payload.snoozed_until=='':
                session.pop('snoozed_until',None)
            else:
                datetime.fromisoformat(payload.snoozed_until)  # Must parse, or it never wakes.
                session['snoozed_until']=payload.snoozed_until
        if payload.name is not None:
            session['auto_named']=False  # A name the user chose is never replaced by the agent's.
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
