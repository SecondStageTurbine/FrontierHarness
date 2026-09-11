"""Same-origin HTTP/SSE adapter. Authentication precedes every tenant lookup."""
import asyncio
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from .schemas import LoginInput, TenantInput, ModelConfig, WorkflowInput, AgentInput, PromptInput, ContinueInput, SUBSCRIPTION_PROVIDERS
from .store import Store, TenantIsolationViolationException, uid, now, public_model
from .engine import Engine, TERMINAL
from .projects import pdf_text
from .broker import ProviderError, probe_cli

SESSION_LIFETIME = 86400*7  # Seconds of inactivity before a stored session expires.

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()

def create_app(directory=None, broker=None):
    store = Store(directory or os.environ.get('HARNESS_DATA_DIR', 'data'))
    engine = Engine(store, broker)
    attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        # Exclusive OS file lock prevents accidental multiple engine workers.
        lock = (store.directory / 'worker.lock').open('a+b')
        lock.seek(0)
        if lock.read(1) == b'':
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        engine.recover()
        try:
            yield
        finally:
            tasks = list(engine.tasks.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            lock.close()

    app = FastAPI(title='Frontier Harness', version='1.0.0', lifespan=lifespan)
    app.state.store, app.state.engine = store, engine

    @app.middleware('http')
    async def security(request, call_next):
        if request.url.path.startswith('/api'):
            origin = request.headers.get('origin')
            configured = os.environ.get('HARNESS_ORIGIN')
            if origin and origin not in {configured, f'{request.url.scheme}://{request.headers.get("host")}'}:
                return JSONResponse({'detail':'Cross-origin API access is blocked.'}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail':'Cross-site API access is blocked.'},status_code=403)
            length = request.headers.get('content-length')
            if length and int(length) > 6_000_000:
                return JSONResponse({'detail':'Upload is too large. Maximum 5 MB.'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(TenantIsolationViolationException)
    async def isolation(request, exc):
        return JSONResponse({'detail':str(exc),'code':'TenantIsolationViolationException'}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Never echo submitted passwords, credentials, or model inputs in errors.
        return JSONResponse({'detail':[{'loc':list(e['loc']),'msg':e['msg']} for e in exc.errors()]},status_code=422)

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({'detail':str(exc)},status_code=422)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse({'detail':str(exc)},status_code=502)

    def user(request):
        token = request.cookies.get('harness_session', '')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with store.db() as db:
            row = db.execute('SELECT users.id,users.username,sessions.expires FROM sessions JOIN users ON users.id=sessions.user_id WHERE token=? AND expires>?', (digest,time.time())).fetchone()
            if row and row['expires'] - time.time() < SESSION_LIFETIME/2:
                # Slide the window on use. The desktop session holds no password to sign back
                # in with, so an app in continuous use must never expire out from under it.
                db.execute('UPDATE sessions SET expires=? WHERE token=?', (time.time()+SESSION_LIFETIME,digest))
        if not row:
            raise HTTPException(401, 'Sign in to continue.')
        return {'id':row['id'],'username':row['username']}

    def scoped(request, tenant_id):
        return store.tenant(tenant_id, user(request)['id'])

    def session(response, user_id):
        token = secrets.token_urlsafe(48)
        with store.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user_id,time.time()+SESSION_LIFETIME))
        # The stored expiry is the real control and slides on use, so the cookie outlives it
        # deliberately: a browser dropping the cookie on its own would strand the desktop
        # session, which cannot sign in again.
        response.set_cookie('harness_session',token,httponly=True,samesite='strict',secure=os.environ.get('HARNESS_SECURE_COOKIE')=='1',max_age=86400*400)

    @app.get('/api/health')
    def health():
        return {'status':'ok'}

    @app.get('/api/auth/status')
    def auth_status(request:Request):
        with store.db() as db:
            setup = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        try:
            current = user(request)
        except HTTPException:
            current = None
        return {'setup_required':setup,'user':current}

    @app.post('/api/auth/{action}')
    def auth(action:str, payload:LoginInput, response:Response, request:Request):
        if action not in ('setup','login'):
            raise HTTPException(404)
        address = request.client.host if request.client else 'unknown'
        queue = attempts[address]
        while queue and queue[0] < time.time()-300:
            queue.popleft()
        if len(queue) >= 10:
            raise HTTPException(429,'Too many sign-in attempts. Try again in five minutes.')
        queue.append(time.time())
        with store.db() as db:
            if action == 'setup':
                if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                    raise HTTPException(409,'Initial setup is complete. Sign in.')
                current = {'id':uid(),'username':payload.username}
                db.execute('INSERT INTO users VALUES(?,?,?)',(current['id'],payload.username,password_hash(payload.password)))
            else:
                row = db.execute('SELECT * FROM users WHERE username=?',(payload.username,)).fetchone()
                if row and ':' not in row['password']:
                    # A desktop-managed account stores no password hash and signs in through
                    # the app's launch ticket, so name the one action that recovers it.
                    raise HTTPException(401,'This workspace signs in automatically from the Frontier desktop app. Quit Frontier, then start it again.')
                valid = password_hash(payload.password, row['password'].split(':')[0] if row else 'missing')
                if not row or not hmac.compare_digest(valid,row['password']):
                    raise HTTPException(401,'The username or password is incorrect.')
                current = {'id':row['id'],'username':row['username']}
        session(response,current['id'])
        attempts.pop(address,None)
        return current

    @app.post('/api/logout')
    def logout(request:Request,response:Response):
        token = request.cookies.get('harness_session','')
        with store.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie('harness_session')
        return {'ok':True}

    @app.get('/api/tenants')
    def tenants(request:Request):
        current = user(request)
        with store.db() as db:
            rows = db.execute('SELECT data FROM tenants WHERE owner=? ORDER BY rowid',(current['id'],)).fetchall()
        return [json.loads(r['data']) for r in rows]

    @app.post('/api/tenants')
    def create_tenant(payload:TenantInput,request:Request):
        current = user(request)
        if payload.model_routing_table:
            raise ValueError('Connect models before setting routing defaults.')
        tenant = dict(id=uid(),created_at=now(),**payload.model_dump())
        with store.db() as db:
            db.execute('INSERT INTO tenants VALUES(?,?,?)',(tenant['id'],current['id'],json.dumps(tenant)))
        return tenant

    @app.put('/api/tenants/{tenant_id}')
    def update_tenant(tenant_id:str,payload:TenantInput,request:Request):
        tenant = scoped(request,tenant_id)
        engine.ensure_available(tenant_id)
        for model in payload.model_routing_table.values():
            store.get(tenant_id,'models',model)
        tenant.update(payload.model_dump())
        with store.db() as db:
            db.execute('UPDATE tenants SET data=? WHERE id=? AND owner=?',(json.dumps(tenant),tenant_id,user(request)['id']))
        return tenant

    @app.delete('/api/tenants/{tenant_id}')
    def delete_tenant(tenant_id:str,request:Request,name:str):
        tenant = scoped(request,tenant_id)
        engine.ensure_available(tenant_id)
        if name != tenant['name']:
            raise ValueError('Type the exact workspace name to delete it.')
        with store.db() as db:
            db.execute('DELETE FROM events WHERE tenant_id=?',(tenant_id,))
            db.execute('DELETE FROM entities WHERE tenant_id=?',(tenant_id,))
            db.execute('DELETE FROM tenants WHERE id=? AND owner=?',(tenant_id,user(request)['id']))
        return {'ok':True}

    @app.get('/api/t/{tenant_id}/models')
    def models(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return [public_model(m) for m in store.list(tenant_id,'models')]

    @app.post('/api/t/{tenant_id}/models')
    @app.put('/api/t/{tenant_id}/models/{model_id}')
    def save_model(tenant_id:str,payload:ModelConfig,request:Request,model_id:str|None=None):
        scoped(request,tenant_id)
        engine.ensure_available(tenant_id)
        old = store.get(tenant_id,'models',model_id) if model_id else {}
        data = payload.model_dump(exclude={'api_key'})
        if old and (data['provider'] != old['provider'] or data['base_url'] != old['base_url']) and not payload.api_key:
            raise ValueError('Re-enter credentials when changing provider or endpoint.')
        encrypted = store.encrypt(payload.api_key) if payload.api_key else old.get('encrypted_key')
        if payload.provider in ('openai','anthropic') and not encrypted:
            raise ValueError('An API key is required for this provider.')
        if payload.provider in SUBSCRIPTION_PROVIDERS:
            encrypted = None  # Switching to a subscription login discards any stored key.
        hint = 'Subscription login' if payload.provider in SUBSCRIPTION_PROVIDERS else ('••••'+payload.api_key[-4:] if payload.api_key else old.get('key_hint','No key'))
        model = dict(**data,id=model_id or uid(),encrypted_key=encrypted,key_hint=hint,status='untested',created_at=old.get('created_at',now()))
        return public_model(store.put(tenant_id,'models',model))

    @app.post('/api/t/{tenant_id}/models/{model_id}/test')
    async def test_model(tenant_id:str,model_id:str,request:Request):
        scoped(request,tenant_id)
        model = store.get(tenant_id,'models',model_id)
        # Discovery authenticates without generating billable text or bypassing budgets.
        from openai import AsyncOpenAI
        from anthropic import AsyncAnthropic
        key = store.decrypt(model['encrypted_key']) if model.get('encrypted_key') else 'local-no-key'
        start = time.monotonic()
        try:
            async with asyncio.timeout(20):
                if model['provider'] in SUBSCRIPTION_PROVIDERS:
                    await probe_cli(model['provider'])
                elif model['provider'] == 'anthropic':
                    async with AsyncAnthropic(api_key=key,max_retries=0) as client:
                        await client.models.retrieve(model['model_name'])
                else:
                    async with AsyncOpenAI(api_key=key,base_url=model.get('base_url') or 'https://api.openai.com/v1',max_retries=0) as client:
                        found = await client.models.list()
                        if model['model_name'] not in [m.id for m in found.data]:
                            raise ValueError('The configured model was not returned by the endpoint.')
            model.update(status='connected',latency_ms=round((time.monotonic()-start)*1000),tested_at=now())
        except Exception as exc:
            model.update(status='error',tested_at=now())
            store.put(tenant_id,'models',model)
            if isinstance(exc,ProviderError):
                raise  # A subscription check already explains what to install or sign in to.
            raise ProviderError('Connection check failed. Verify the key, model identifier, and endpoint. The endpoint must support model discovery.') from None
        return public_model(store.put(tenant_id,'models',model))

    @app.post('/api/t/{tenant_id}/attachments')
    async def upload(tenant_id:str,request:Request,file:UploadFile=File(...)):
        scoped(request,tenant_id)
        raw = await file.read(5_000_001)
        if len(raw)>5_000_000:
            raise ValueError('Files must be smaller than 5 MB.')
        filename = Path(file.filename or 'attachment.txt').name
        try:
            if filename.lower().endswith('.pdf'):
                content = pdf_text(raw)
            else:
                content = raw.decode('utf-8')
        except Exception:
            raise ValueError('Use UTF-8 text/source files or a text-based PDF (up to 100 pages).') from None
        if not content.strip() or '\x00' in content or len(content)>100000:
            raise ValueError('File must contain extractable text, up to 100,000 characters.')
        result = store.put(tenant_id,'attachments',dict(id=uid(),name=filename,content=content,size=len(raw),created_at=now()))
        return {k:v for k,v in result.items() if k!='content'}

    @app.get('/api/t/{tenant_id}/workflows')
    def workflows(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        runs = store.list(tenant_id,'runs')
        return [{**w,'latest_run':next((summary(r) for r in runs if r['workflow_id']==w['id']),None)} for w in store.list(tenant_id,'workflows')]

    @app.get('/api/t/{tenant_id}/workflows/{workflow_id}')
    def workflow(tenant_id:str,workflow_id:str,request:Request):
        scoped(request,tenant_id)
        return store.get(tenant_id,'workflows',workflow_id)

    @app.post('/api/t/{tenant_id}/workflows')
    @app.put('/api/t/{tenant_id}/workflows/{workflow_id}')
    def save_workflow(tenant_id:str,payload:WorkflowInput,request:Request,workflow_id:str|None=None):
        scoped(request,tenant_id)
        old = store.get(tenant_id,'workflows',workflow_id) if workflow_id else {}
        engine.validate_workflow(tenant_id,payload.model_dump())
        return store.put(tenant_id,'workflows',dict(**payload.model_dump(),id=workflow_id or uid(),created_at=old.get('created_at',now())))

    @app.post('/api/t/{tenant_id}/workflows/{workflow_id}/runs')
    async def execute(tenant_id:str,workflow_id:str,request:Request):
        scoped(request,tenant_id)
        return engine.start(tenant_id,workflow_id)

    @app.post('/api/t/{tenant_id}/workflows/{workflow_id}/duplicate')
    def duplicate(tenant_id:str,workflow_id:str,request:Request):
        scoped(request,tenant_id)
        w = store.get(tenant_id,'workflows',workflow_id)
        w.update(id=uid(),name=w['name'][:113]+' (copy)',created_at=now(),archived=False)
        return store.put(tenant_id,'workflows',w)

    def summary(run):
        reviews = [s['artifact'] for s in run['stages'] if s['role']=='review' and s.get('artifact')]
        return {**{k:v for k,v in run.items() if k not in ('transcript','stages','workflow','models')},'quality_score':reviews[-1]['quality_score'] if reviews else None}

    @app.get('/api/t/{tenant_id}/runs')
    def runs(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return [summary(r) for r in store.list(tenant_id,'runs')]

    @app.get('/api/t/{tenant_id}/runs/{run_id}')
    def run(tenant_id:str,run_id:str,request:Request):
        scoped(request,tenant_id)
        r = store.get(tenant_id,'runs',run_id)
        return {**r,'events':store.events(tenant_id,run_id)}

    @app.post('/api/t/{tenant_id}/runs/{run_id}/cancel')
    async def cancel(tenant_id:str,run_id:str,request:Request):
        scoped(request,tenant_id)
        return await engine.cancel(tenant_id,run_id)

    @app.post('/api/t/{tenant_id}/runs/{run_id}/continue')
    async def continue_run(tenant_id:str,run_id:str,payload:ContinueInput,request:Request):
        scoped(request,tenant_id)
        return engine.continue_run(tenant_id,run_id,payload.max_workflow_iterations)

    @app.get('/api/t/{tenant_id}/runs/{run_id}/events')
    async def events(tenant_id:str,run_id:str,request:Request,after:int=0):
        scoped(request,tenant_id)
        store.get(tenant_id,'runs',run_id)
        try:
            cursor = max(after,int(request.headers.get('last-event-id','0')))
        except ValueError:
            cursor = after
        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                try:
                    scoped(request,tenant_id)  # Session expiry/revocation also terminates open streams.
                    batch = store.events(tenant_id,run_id,cursor)
                    for event in batch:
                        cursor = event['seq']
                        yield f'id: {cursor}\ndata: {json.dumps(event)}\n\n'
                    r = store.get(tenant_id,'runs',run_id)
                    if r['status'] in TERMINAL and len(batch)<500:
                        yield 'event: done\ndata: {}\n\n'
                        return
                    yield ': heartbeat\n\n'
                    await asyncio.sleep(0.4)
                except (HTTPException,TenantIsolationViolationException):
                    return
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

    @app.get('/api/t/{tenant_id}/usage')
    def usage(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        return [{'run_id':r['id'],'workflow_id':r['workflow_id'],'workflow_name':r['name'],'date':s['started_at'],'role':s['role'],'model_id':s['model_id'],'model_name':r['models'].get(s['model_id'],{}).get('name',s['model_id']),'provider':r['models'].get(s['model_id'],{}).get('provider','unknown'),'cost':s['cost'],'input_tokens':s['input_tokens'],'output_tokens':s['output_tokens']} for r in store.list(tenant_id,'runs') for s in r['stages']]

    from .desktop_routes import install_desktop_routes
    install_desktop_routes(app,store,engine,user,scoped,session)

    @app.get('/api/t/{tenant_id}/{kind}')
    def resources(tenant_id:str,kind:str,request:Request):
        scoped(request,tenant_id)
        if kind not in ('agents','prompts'):
            raise HTTPException(404)
        return store.list(tenant_id,kind)

    @app.post('/api/t/{tenant_id}/{kind}')
    @app.put('/api/t/{tenant_id}/{kind}/{resource_id}')
    async def save_resource(tenant_id:str,kind:str,request:Request,resource_id:str|None=None):
        scoped(request,tenant_id)
        if kind not in ('agents','prompts'):
            raise HTTPException(404)
        schema = AgentInput if kind=='agents' else PromptInput
        try:
            payload = schema.model_validate(await request.json())
        except ValidationError:
            raise ValueError('Check the name, role, model, and prompt fields.') from None
        old = store.get(tenant_id,kind,resource_id) if resource_id else {}
        if kind=='agents':
            engine.ensure_available(tenant_id)
            store.get(tenant_id,'models',payload.model_id)
        return store.put(tenant_id,kind,dict(**payload.model_dump(),id=resource_id or uid(),created_at=old.get('created_at',now())))

    @app.delete('/api/t/{tenant_id}/{kind}/{resource_id}')
    def delete_resource(tenant_id:str,kind:str,resource_id:str,request:Request):
        tenant = scoped(request,tenant_id)
        if kind not in ('agents','prompts','models','workflows','attachments'):
            raise HTTPException(404)
        engine.ensure_available(tenant_id)
        if kind in ('models','agents','attachments','prompts'):
            uses = []
            project_field={'models':'team','agents':'agent_ids','prompts':'prompt_ids'}.get(kind)
            if project_field:
                uses += [p['name'] for p in store.list(tenant_id,'projects') if resource_id in p.get(project_field,{}).values()]
            for w in store.list(tenant_id,'workflows'):
                if (kind=='attachments' and resource_id in w['attachment_ids']) or any(resource_id in [s['model_id'],s.get('fallback_model_id'),s.get('agent_id')] for s in w['stages']):
                    uses.append(w['name'])
            if kind=='models':
                uses += [a['name'] for a in store.list(tenant_id,'agents') if a['model_id']==resource_id]
                if resource_id in tenant['model_routing_table'].values():
                    uses.append('Workspace routing defaults')
            if uses:
                raise ValueError('Still used by: '+', '.join(uses)+'. Update these references before deleting.')
        store.delete(tenant_id,kind,resource_id)
        return {'ok':True}

    dist = Path(__file__).resolve().parent.parent / 'dist'
    if dist.exists():
        app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
        @app.get('/{path:path}')
        def frontend(path:str):
            if path.startswith('api/'):
                raise HTTPException(404)
            return FileResponse(dist/'index.html')
    return app

app = create_app()
