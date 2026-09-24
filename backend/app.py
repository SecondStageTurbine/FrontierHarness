"""Same-origin HTTP/SSE adapter. Authentication precedes every tenant lookup."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import httpx
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from .schemas import LoginInput, TenantInput, ModelConfig, SUBSCRIPTION_PROVIDERS, PasswordInput, RemoteInput, PushInput, EnvironmentInput
from . import automations, remote, devserver, push, environments
from starlette.background import BackgroundTask
from .store import Store, TenantIsolationViolationException, uid, now, public_model
from .agent import AgentRunner
from . import localhealth
from .projects import pdf_text
from .broker import ProviderError, probe_cli, account_env, account_home, ACCOUNT_HOME_VARS, SIGNIN_COMMANDS

SESSION_LIFETIME = 86400*7  # Seconds of inactivity before a stored session expires.

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()

def create_app(directory=None, broker=None):
    store = Store(directory or os.environ.get('HARNESS_DATA_DIR', 'data'))
    runner = AgentRunner(store, broker)
    attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        # Exclusive OS file lock prevents accidental multiple workers on one store.
        lock = (store.directory / 'worker.lock').open('a+b')
        try:
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
        except OSError:
            # Another backend holds this data folder. Say so plainly; the launcher shows this line.
            sys.stderr.write('Another Frontier is already using this data folder. Close it, or wait a moment for it to finish shutting down, then try again.\n')
            sys.stderr.flush()
            os._exit(3)
        runner.recover()
        stop = asyncio.Event()
        clock = asyncio.create_task(automations.scheduler(store, runner, stop))
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(clock, return_exceptions=True)
            await devserver.stop_all()
            tasks = list(runner.turns.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            lock.close()

    app = FastAPI(title='Frontier Harness', version='1.0.0', lifespan=lifespan)
    app.state.store, app.state.runner = store, runner
    token_file = store.directory/'mcp-token'
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32), encoding='utf-8')
    app.state.mcp_token = token_file.read_text(encoding='utf-8').strip()
    BINARY_ATTACHMENTS = {'.mp4','.mov','.webm','.mkv','.avi','.mp3','.wav','.m4a','.ogg','.zip','.tar','.gz','.7z','.sqlite','.db','.parquet'}
    # What this process actually bound at start; the flag may already say otherwise for the next start.
    app.state.bound_remote = remote.enabled(store.directory) and os.environ.get('HARNESS_DATA_DIR') is not None

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
            if length and int(length) > 52_000_000:
                return JSONResponse({'detail':'Upload is too large. Maximum 50 MB.'},status_code=413)
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
        # A program run by this Windows user (Frontier's MCP server) authenticates with the local
        # access token in the data folder, and only over loopback: a LAN client never can.
        local = request.headers.get('x-frontier-token')
        if local and request.client and request.client.host in ('127.0.0.1', '::1') and hmac.compare_digest(local, app.state.mcp_token):
            with store.db() as db:
                owner = db.execute('SELECT id,username FROM users ORDER BY rowid LIMIT 1').fetchone()
            if owner:
                return {'id': owner['id'], 'username': owner['username']}
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

    @app.post('/api/password')
    def set_password(payload:PasswordInput, request:Request):
        """Give the signed-in user a password, which is what a browser on another device signs in with."""
        current = user(request)
        with store.db() as db:
            db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(payload.password), current['id']))
        return {'ok':True}

    @app.get('/api/remote')
    def remote_status(request:Request):
        current = user(request)
        with store.db() as db:
            row = db.execute('SELECT password FROM users WHERE id=?', (current['id'],)).fetchone()
        wanted = remote.enabled(store.directory)
        return {'enabled': wanted, 'listening': remote.remote_host(str(store.directory)) == '0.0.0.0' and os.environ.get('HARNESS_DESKTOP_PORT') is not None and app.state.bound_remote,
                'addresses': remote.addresses(), 'port': int(os.environ.get('HARNESS_DESKTOP_PORT') or 0) or None,
                'has_password': bool(row and ':' in (row['password'] or '')), 'username': current['username']}

    @app.get('/api/tray')
    def tray_status(request:Request):
        user(request)
        return {'enabled': remote.tray_enabled(store.directory)}

    @app.put('/api/tray')
    def tray_set(payload:RemoteInput, request:Request):
        """Whether closing the window keeps Frontier running in the tray. Read by the desktop at each close."""
        user(request)
        remote.set_tray_enabled(store.directory, payload.enabled)
        return {'enabled': payload.enabled}

    # ── Other machines: pair once, then pass this window's calls through to them ──
    @app.get('/api/environments')
    async def environments_list(request:Request):
        user(request)
        return await asyncio.gather(*(environments.status(store, item) for item in environments.load(store.directory)))

    @app.post('/api/environments')
    async def environments_add(payload:EnvironmentInput, request:Request):
        user(request)
        return await environments.pair(store, payload.name, payload.link)

    @app.delete('/api/environments/{env_id}')
    def environments_remove(env_id:str, request:Request):
        user(request)
        environments.remove(store.directory, env_id)
        return {'ok': True}

    @app.api_route('/api/env/{env_id}/{rest:path}', methods=['GET','POST','PUT','PATCH','DELETE'])
    async def environment_proxy(env_id:str, rest:str, request:Request):
        """This window's API call, made on another machine with the session stored for it. Streams, so live turn events keep flowing."""
        user(request)
        item = environments.find(store.directory, env_id)
        if not item:
            raise HTTPException(404, 'That machine is no longer set up here. Switch back to this computer.')
        http = environments.client(timeout=None)
        headers = {k: v for k, v in request.headers.items() if k.lower() in ('content-type', 'accept', 'last-event-id')}
        try:
            upstream = await http.send(http.build_request(request.method, f'{item["url"]}/api/{rest}', params=request.query_params, content=await request.body(),
                                                          headers=headers, cookies={'harness_session': store.decrypt(item['token'])}), stream=True)
        except httpx.HTTPError:
            await http.aclose()
            return JSONResponse({'detail': f'{item["name"]} is not reachable at {item["url"]}. Check that Frontier is running there with remote access on.'}, status_code=502)
        if upstream.status_code == 401:
            await upstream.aclose(); await http.aclose()
            return JSONResponse({'detail': f'{item["name"]} no longer accepts this computer\'s session. Pair it again from Settings, Environments.', 'code': 'environment_signed_out'}, status_code=409)
        async def close():
            await upstream.aclose(); await http.aclose()
        passed = {k: v for k, v in upstream.headers.items() if k.lower() in ('content-type', 'cache-control', 'content-encoding', 'content-disposition')}
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=passed, background=BackgroundTask(close))

    # ── A phone: signing in by QR code, and push notifications ──
    pairings = {}  # sha256(token) -> (user id, expiry). In memory: a restart simply voids unused codes.

    @app.post('/api/remote/pair')
    def remote_pair(request:Request):
        """A one-time link that signs a phone in without typing the password. Shown as a QR code, valid ten minutes, used once."""
        current = user(request)
        for key, (_, expires) in list(pairings.items()):
            if expires < time.time():
                pairings.pop(key, None)
        token = secrets.token_urlsafe(32)
        pairings[hashlib.sha256(token.encode()).hexdigest()] = (current['id'], time.time() + 600)
        port = int(os.environ.get('HARNESS_DESKTOP_PORT') or 0) or None
        return {'token': token, 'expires_in': 600, 'urls': [f'http://{a}:{port}/pair?code={token}' for a in remote.addresses()] if port else []}

    @app.get('/pair')
    def pair(code:str=''):
        found = pairings.pop(hashlib.sha256(code.encode()).hexdigest(), None)
        if not found or found[1] < time.time():
            return HTMLResponse('<!doctype html><meta name="viewport" content="width=device-width"><body style="font:16px system-ui;padding:24px;background:#09090b;color:#fafafa">'
                                '<h2>This pairing code has expired or was already used.</h2><p>Show a new QR code in Frontier: Settings, Remote access.</p>', status_code=410)
        response = RedirectResponse('/', status_code=303)
        session(response, found[0])
        return response

    @app.get('/api/push')
    def push_status(request:Request):
        user(request)
        settings = push.config(store.directory)
        return {**settings, 'subscribe_url': f'{settings["server"]}/{settings["topic"]}' if settings['topic'] else None}

    @app.put('/api/push')
    def push_set(payload:PushInput, request:Request):
        user(request)
        settings = push.save(store.directory, **payload.model_dump(exclude_unset=True))
        return {**settings, 'subscribe_url': f'{settings["server"]}/{settings["topic"]}'}

    @app.post('/api/push/test')
    async def push_test(request:Request):
        user(request)
        settings = push.config(store.directory)
        if not settings['enabled']:
            raise HTTPException(400, 'Turn push notifications on first.')
        try:
            await asyncio.to_thread(push.send, store.directory, 'done', 'Frontier test notification', 'Notifications from Frontier reach this device.', None, True, True)
        except OSError as exc:
            raise HTTPException(502, f'{settings["server"]} could not be reached: {exc}') from None
        return {'sent': True}

    @app.put('/api/remote')
    def remote_set(payload:RemoteInput, request:Request):
        user(request)
        remote.set_enabled(store.directory, payload.enabled)
        return {'enabled': payload.enabled, 'restart_required': payload.enabled != app.state.bound_remote}

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
        tenant = dict(id=uid(),created_at=now(),**payload.model_dump())
        with store.db() as db:
            db.execute('INSERT INTO tenants VALUES(?,?,?)',(tenant['id'],current['id'],json.dumps(tenant)))
        return tenant

    @app.put('/api/tenants/{tenant_id}')
    def update_tenant(tenant_id:str,payload:TenantInput,request:Request):
        tenant = scoped(request,tenant_id)
        tenant.update(payload.model_dump())
        with store.db() as db:
            db.execute('UPDATE tenants SET data=? WHERE id=? AND owner=?',(json.dumps(tenant),tenant_id,user(request)['id']))
        return tenant

    @app.delete('/api/tenants/{tenant_id}')
    def delete_tenant(tenant_id:str,request:Request,name:str):
        tenant = scoped(request,tenant_id)
        if name != tenant['name']:
            raise ValueError('Type the exact workspace name to delete it.')
        with store.db() as db:
            db.execute('DELETE FROM events WHERE tenant_id=?',(tenant_id,))
            db.execute('DELETE FROM entities WHERE tenant_id=?',(tenant_id,))
            db.execute('DELETE FROM tenants WHERE id=? AND owner=?',(tenant_id,user(request)['id']))
        return {'ok':True}

    @app.get('/api/t/{tenant_id}/models')
    async def models(tenant_id:str,request:Request):
        scoped(request,tenant_id)
        rows = store.list(tenant_id,'models')
        # Whether each local agent's server is up right now, so the picker can say so before a
        # turn is spent finding out. Cloud agents carry no answer.
        online = await localhealth.availability(rows)
        return [{**public_model(m),'online':online.get(m['id'])} for m in rows]

    @app.post('/api/t/{tenant_id}/models')
    @app.put('/api/t/{tenant_id}/models/{model_id}')
    def save_model(tenant_id:str,payload:ModelConfig,request:Request,model_id:str|None=None):
        scoped(request,tenant_id)
        old = store.get(tenant_id,'models',model_id) if model_id else {}
        data = payload.model_dump(exclude={'api_key'})
        if old and (data['provider'] != old['provider'] or data['base_url'] != old['base_url']) and not payload.api_key:
            raise ValueError('Re-enter credentials when changing provider or endpoint.')
        encrypted = store.encrypt(payload.api_key) if payload.api_key else old.get('encrypted_key')
        if payload.provider in ('openai','anthropic','typesafe') and not encrypted:
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
                    await probe_cli(model['provider'], account_env(store, tenant_id, model))
                elif model['provider'] == 'typesafe':
                    # TypeSafe has no chat-completion surface to discover a model against; its
                    # own /v1/models lists what the key can send in a request's `model` field.
                    async with httpx.AsyncClient(timeout=15) as client:
                        found = await client.get('https://api.typesafe.ai/v1/models', headers={'Authorization': f'Bearer {key}'})
                        if found.status_code == 401:
                            raise ProviderError('TypeSafe rejected this API key.')
                        found.raise_for_status()
                elif model['provider'] == 'anthropic':
                    async with AsyncAnthropic(api_key=key,max_retries=0) as client:
                        await client.models.retrieve(model['model_name'])
                else:
                    async with AsyncOpenAI(api_key=key,base_url=model.get('base_url') or 'https://api.openai.com/v1',max_retries=0) as client:
                        found = await client.models.list()
                        served = [m.id for m in found.data]
                        if model['model_name'] not in served:
                            # A local server often serves a model under an alias rather than the
                            # file name; naming what it actually lists turns a dead end into a fix.
                            shown = ', '.join(served[:8]) + (f' and {len(served)-8} more' if len(served) > 8 else '')
                            raise ProviderError(f'The endpoint does not list "{model["model_name"]}". It lists: {shown or "nothing"}. Use one of those as the model identifier.')
            model.update(status='connected',latency_ms=round((time.monotonic()-start)*1000),tested_at=now())
        except Exception as exc:
            model.update(status='error',tested_at=now())
            store.put(tenant_id,'models',model)
            if isinstance(exc,ProviderError):
                raise  # A subscription check already explains what to install or sign in to.
            # The address is the thing most often wrong, so the message names the one it tried.
            tried = model.get('base_url') or {'anthropic':'api.anthropic.com','typesafe':'api.typesafe.ai'}.get(model['provider'],'api.openai.com')
            raise ProviderError(f'Could not reach {tried}. Check that the server is running and the address is right — a local server usually ends in /v1 — then test again.') from None
        return public_model(store.put(tenant_id,'models',model))

    @app.post('/api/t/{tenant_id}/models/{model_id}/signin')
    def signin(tenant_id:str,model_id:str,request:Request):
        """Prepare one named subscription's own credential directory and name the command that
        signs into it. Frontier never sees the credential: the command line tool writes it there
        and reads it back, which is what lets a second subscription exist alongside the first."""
        scoped(request,tenant_id)
        model = store.get(tenant_id,'models',model_id)
        if model['provider'] not in SUBSCRIPTION_PROVIDERS or not model.get('account'):
            raise ValueError('Only a subscription login with a named account signs in separately.')
        home = account_home(store,tenant_id,model['provider'],model['account'])
        variable = ACCOUNT_HOME_VARS[model['provider']][0]
        assign = f'set {variable}={home}' if os.name=='nt' else f'export {variable}="{home}"'
        return {'directory':str(home),'variable':variable,'command':assign+chr(10)+SIGNIN_COMMANDS[model['provider']]}

    @app.post('/api/t/{tenant_id}/transcribe')
    async def transcribe(tenant_id:str,request:Request,model_id:str=Form(...),file:UploadFile=File(...)):
        """Dictation, through a model this workspace already configured rather than a credential
        of its own. The recording is held for the length of one request and never stored: speech
        is how a prompt was typed, not an artifact of the workspace."""
        scoped(request,tenant_id)
        model = store.get(tenant_id,'models',model_id)
        if model['provider'] not in ('openai','custom_openai'):
            raise ValueError('Choose an OpenAI or OpenAI-compatible model for dictation.')
        raw = await file.read(25_000_001)
        if len(raw)>25_000_000:
            raise ValueError('Recordings must be smaller than 25 MB.')
        from openai import AsyncOpenAI
        key = store.decrypt(model['encrypted_key']) if model.get('encrypted_key') else 'local-no-key'
        try:
            async with asyncio.timeout(120):
                async with AsyncOpenAI(api_key=key,base_url=model.get('base_url') or 'https://api.openai.com/v1',max_retries=0) as client:
                    result = await client.audio.transcriptions.create(model=model['model_name'],file=(Path(file.filename or 'speech.webm').name,raw))
        except TimeoutError:
            raise ProviderError('Transcription timed out. Record a shorter passage.') from None
        except Exception:
            # Provider exception strings can carry the request payload, so none of it is passed on.
            raise ProviderError('Transcription failed. Confirm the model accepts audio and the key is valid.') from None
        return {'text':result.text}

    IMAGE_MIME = {'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif'}

    @app.post('/api/t/{tenant_id}/attachments')
    async def upload(tenant_id:str,request:Request,file:UploadFile=File(...)):
        scoped(request,tenant_id)
        raw = await file.read(50_000_001)
        if len(raw)>50_000_000:
            raise ValueError('Files must be smaller than 50 MB.')
        filename = Path(file.filename or 'attachment.txt').name
        # Screenshots and pasted images are stored as data URLs; the instruct route writes them
        # back to disk in the project so an agent can open the actual file.
        mime = IMAGE_MIME.get(Path(filename).suffix.lower())
        if mime:
            if len(raw)>10_000_000:
                raise ValueError('Images must be smaller than 10 MB.')
            result = store.put(tenant_id,'attachments',dict(id=uid(),name=filename,kind='image',content=f'data:{mime};base64,'+base64.b64encode(raw).decode('ascii'),size=len(raw),created_at=now()))
            return {k:v for k,v in result.items() if k!='content'}
        if Path(filename).suffix.lower() in BINARY_ATTACHMENTS:
            # Video, audio and archives are kept as files beside the database, never in it, and
            # the instruct route copies them into the project for the agent's own tools.
            folder = store.directory/'attachments'
            folder.mkdir(exist_ok=True)
            attachment_id = uid()
            (folder/(attachment_id+Path(filename).suffix.lower())).write_bytes(raw)
            result = store.put(tenant_id,'attachments',dict(id=attachment_id,name=filename,kind='file',path=str(folder/(attachment_id+Path(filename).suffix.lower())),size=len(raw),created_at=now()))
            return {k:v for k,v in result.items() if k!='path'}
        # Decided by format, not by whether the bytes happen to survive a UTF-8 decode:
        # a small office file that decodes would otherwise reach the model as gibberish.
        if filename.lower().endswith(('.doc','.docx','.xls','.xlsx','.ppt','.pptx','.odt','.ods','.odp','.rtf','.pages','.numbers','.key')):
            raise ValueError('Word, Excel and PowerPoint documents are not read. Export to PDF or plain text first.')
        try:
            if filename.lower().endswith('.pdf'):
                content = pdf_text(raw)
            else:
                content = raw.decode('utf-8')
        except Exception:
            raise ValueError('Use a UTF-8 text or source file, or a text-based PDF of up to 100 pages.') from None
        if not content.strip():
            # A scanned PDF is the common case here, and 'no extractable text' reads as a
            # broken file unless the message says what is actually missing.
            raise ValueError('This PDF holds no extractable text. A scanned PDF needs OCR before it can be read.' if filename.lower().endswith('.pdf') else 'This file contains no text.')
        if '\x00' in content or len(content)>100000:
            raise ValueError('File must contain extractable text, up to 100,000 characters.')
        result = store.put(tenant_id,'attachments',dict(id=uid(),name=filename,kind='text',content=content,size=len(raw),created_at=now()))
        return {k:v for k,v in result.items() if k!='content'}

    from .desktop_routes import install_desktop_routes
    install_desktop_routes(app,store,runner,user,scoped,session)

    @app.delete('/api/t/{tenant_id}/{kind}/{resource_id}')
    def delete_resource(tenant_id:str,kind:str,resource_id:str,request:Request):
        scoped(request,tenant_id)
        if kind not in ('models','attachments'):
            raise HTTPException(404)
        if kind=='models':
            # A conversation records the agent that answered each message, so disconnecting one
            # still in use would leave those messages naming a model that no longer exists.
            uses=[p['name'] for p in store.list(tenant_id,'projects') if p.get('last_model_id')==resource_id]
            if uses:
                raise ValueError('Still selected in: '+', '.join(sorted(set(uses)))+'. Choose another agent there first.')
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
