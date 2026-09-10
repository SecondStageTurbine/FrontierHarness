"""Project-bound file operations and captured local checks for the desktop harness.

File access rejects traversal, symlinks/junction escapes, sensitive files, and
overlapping cross-tenant project roots. Commands run only in explicit execute mode;
they are local processes, not an OS sandbox, and receive a scrubbed environment.
"""
import asyncio
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from .store import TenantIsolationViolationException, now, uid

IGNORED = {'.git','.venv','venv','node_modules','dist','build','target','__pycache__','.pytest_cache','.next','.idea','.vscode','.ssh','.aws','.azure','.codex','data'}
SENSITIVE = {'.env','.npmrc','.pypirc','credentials','credentials.json','secret.key','harness.db','id_rsa','id_ed25519'}
TEXT_EXTENSIONS = {'.py','.js','.jsx','.ts','.tsx','.json','.md','.txt','.html','.css','.scss','.yml','.yaml','.toml','.ini','.cfg','.xml','.sql','.rs','.go','.java','.c','.h','.cpp','.sh','.ps1','.csv','.lock','.gitignore'}

# A rooted path at the start of an argument, or straight after an option prefix
# ('-sC:\Temp', '--rootdir=/etc'). Driveless roots are rooted on Windows too: the
# OS resolves '/Temp' against the current drive.
ROOTED_ARG = re.compile(r'^(?:-{1,2}[A-Za-z][\w-]*=?)?(?:[/\\]|[A-Za-z]:)')

def path_key(path):
    """Match Windows filesystem identity without changing displayed path spelling."""
    return path.casefold() if os.name=='nt' else path

def read_form(text):
    """The text a later read() returns: Python decodes files with universal newlines."""
    return text.replace('\r\n','\n').replace('\r','\n')

class ProjectFiles:
    def __init__(self,store):
        self.store=store

    def create(self,tenant_id,name,root=None):
        project_id=uid()
        if root:
            location=Path(root).expanduser().resolve(strict=True)
            if not location.is_dir() or location==Path(location.anchor) or location==Path.home():
                raise ValueError('Choose a project folder, not your home folder or drive root.')
        else:
            # Managed projects are siblings of private app state, never inside its credentials folder.
            location=self.store.directory.parent/'Frontier Projects'/tenant_id/(re.sub(r'[^\w -]','',name)[:60]+'-'+project_id[:6])
            location.mkdir(parents=True,exist_ok=False)
            location=location.resolve()
        secret_dir=self.store.directory.resolve()
        if location==secret_dir or location in secret_dir.parents or secret_dir in location.parents:
            raise ValueError('Choose a project outside Frontier’s private application data.')
        # Root registration is a security index; contents always remain tenant scoped.
        with self.store.db() as db:
            registered=db.execute("SELECT tenant_id,data FROM entities WHERE kind='projects'").fetchall()
        for row in registered:
            other=Path(json.loads(row['data'])['root']).resolve()
            if location==other or location in other.parents or other in location.parents:
                if row['tenant_id']!=tenant_id:
                    raise TenantIsolationViolationException('This folder overlaps a project owned by another workspace.')
                raise ValueError('This folder is already open as a project, or overlaps an existing project.')
        return self.store.put(tenant_id,'projects',dict(id=project_id,name=name,root=str(location),created_at=now()))

    def resolve(self,tenant_id,project_id,relative):
        project=self.store.get(tenant_id,'projects',project_id)
        root=Path(project['root']).resolve(strict=True)
        relative=relative.replace('\\','/')
        pieces=relative.split('/')
        if not relative or relative.startswith('/') or any(p in ('','..','.') for p in pieces) or ':' in relative:
            raise ValueError('Use a relative path inside the project.')
        if any(p.lower() in IGNORED or p.lower() in SENSITIVE or p.lower().startswith('.env') or p.lower().endswith(('.pem','.key','.p12','.pfx')) for p in pieces):
            raise ValueError('This path is excluded from agent file access.')
        target=root/relative
        current=root
        for piece in pieces:
            current=current/piece
            if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):
                raise ValueError('Linked files and folders are excluded from project access.')
        resolved=target.resolve()
        if root not in resolved.parents:
            raise TenantIsolationViolationException('File access cannot leave the project root.')
        return target

    def tree(self,tenant_id,project_id):
        project=self.store.get(tenant_id,'projects',project_id)
        root=Path(project['root'])
        if not root.is_dir():
            raise ValueError('The project folder is unavailable. Reconnect the drive or restore the folder.')
        result=[]
        for directory,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in IGNORED and not d.startswith('.') and not (Path(directory)/d).is_symlink() and not (hasattr(Path(directory)/d,'is_junction') and (Path(directory)/d).is_junction()))
            for name in sorted(files):
                relative=(Path(directory)/name).relative_to(root).as_posix()
                try:
                    file=self.resolve(tenant_id,project_id,relative)
                    if file.is_file():
                        result.append({'path':relative,'size':file.stat().st_size,'text':file.suffix in TEXT_EXTENSIONS or name in ('Dockerfile','Makefile','LICENSE')})
                except (ValueError,OSError):
                    continue
                if len(result)>=2000:return result
        return result

    def read(self,tenant_id,project_id,relative):
        file=self.resolve(tenant_id,project_id,relative)
        if not file.is_file():
            raise ValueError('This file is no longer available.')
        if file.stat().st_size>300000:
            raise ValueError('Preview is limited to text files under 300 KB.')
        try:
            content=file.read_text(encoding='utf-8')
        except UnicodeError:
            raise ValueError('This file is not UTF-8 text.') from None
        if '\x00' in content:
            raise ValueError('Binary files cannot be previewed.')
        return {'path':relative,'content':content,'hash':hashlib.sha256(content.encode()).hexdigest()}

    def snapshot(self,tenant_id,project_id):
        """Hash every readable file; include content while it fits the context budget.

        A file with no baseline hash cannot be modified at all, because apply() cannot tell
        an external edit from a file it was never shown, so the baseline covers everything
        read() accepts rather than only what fits in context. Files read() rejects (binary,
        undecodable, over its size limit) stay out: the model cannot edit what it cannot see.
        """
        context=[];hashes={};size=0
        for f in self.tree(tenant_id,project_id):
            try:
                item=self.read(tenant_id,project_id,f['path'])
            except ValueError:
                continue
            hashes[f['path']]=item['hash']
            if len(item['content'])>50000 or size+len(item['content'])>90000:continue
            size+=len(item['content'])
            context.append(f'FILE {f["path"]}\n{item["content"]}')
        return context,hashes

    def apply(self,tenant_id,run,stage,files):
        project_id=run['workflow']['project_id']
        expected_hashes={path_key(p):value for p,value in run['file_hashes'].items()}
        prepared=[]
        seen=set()
        for artifact in files:
            relative=artifact['name'].replace('\\','/')
            identity=path_key(relative)
            if identity in seen:raise ValueError('A build cannot write the same file twice.')
            seen.add(identity)
            target=self.resolve(tenant_id,project_id,relative)
            before=self.read(tenant_id,project_id,relative)['content'] if target.exists() else None
            expected=expected_hashes.get(identity)
            if before is not None and (expected is None or hashlib.sha256(before.encode()).hexdigest()!=expected):
                raise ValueError(f'{relative} changed outside this run, or was not read into context. The file was not overwritten.')
            after=artifact['content']
            if before==read_form(after):continue  # Identical text, even if the file on disk uses CRLF.
            change={'id':uid(),'stage_id':stage['id'],'iteration':run['iteration'],'path':relative,'before':before,'after':after,'status':'proposed','kind':'added' if before is None else 'modified','diff':'\n'.join(difflib.unified_diff((before or '').splitlines(),after.splitlines(),fromfile='a/'+relative,tofile='b/'+relative,lineterm=''))}
            prepared.append((target,change))
        for target,change in prepared:
            run['changes'].append(change)
            self.store.put(tenant_id,'runs',run)  # Durable intention precedes the write.
            if run['workflow']['execution_mode']!='propose':
                target.parent.mkdir(parents=True,exist_ok=True)
                safe=self.resolve(tenant_id,project_id,change['path'])
                temp=safe.with_name(safe.name+'.frontier-'+uid()+'.tmp')
                try:
                    temp.write_text(change['after'],encoding='utf-8',newline='')
                    os.replace(temp,safe)
                finally:
                    if temp.exists():temp.unlink()
                change['status']='applied'
                for old in list(run['file_hashes']):
                    if path_key(old)==path_key(change['path']):del run['file_hashes'][old]
                # The bytes written keep the model's own line endings; the baseline has to match
                # what the next read returns, or a CRLF file fails as changed outside the run.
                run['file_hashes'][change['path']]=hashlib.sha256(read_form(change['after']).encode()).hexdigest()
            self.store.put(tenant_id,'runs',run)
            self.store.event(tenant_id,run['id'],'file.changed',f'{"Wrote" if change["status"]=="applied" else "Proposed"} {change["path"]}',iteration=run['iteration'],path=change['path'],kind=change['kind'])

    def command_argv(self,root,command):
        if re.search(r'[;&|><`\r\n]',command) or '$(' in command:
            raise ValueError('Use one supported project check without shell operators.')
        # POSIX splitting reads a backslash as an escape and would silently delete the
        # separators in a Windows path, so protect them before splitting.
        args=shlex.split(command.replace('\\','\\\\') if os.name=='nt' else command,posix=True)
        if not args:raise ValueError('Enter a command.')
        # Validate the caller's own arguments before building argv, so the runner's
        # trusted absolute paths (the project venv, npm's entrypoint) need no exception.
        for arg in args[1:]:
            if '..' in Path(arg).parts or ROOTED_ARG.match(arg):
                raise ValueError('Command arguments cannot reference paths outside the project.')
        python=Path(root)/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
        runner=[str(python),'-m'] if python.exists() else [sys.executable,'--project-check' if getattr(sys,'frozen',False) else '-m']
        if args[0] in ('python','python3','py') and len(args)>=3 and args[1]=='-m' and args[2] in ('pytest','unittest','compileall'):
            args=[*runner,*args[2:]]
        elif args[0]=='pytest':
            args=[*runner,'pytest',*args[1:]]
        elif args[:2] in (['npm','test'],['npm','run']) and (args[1]=='test' or (len(args)>2 and args[2] in ('build','test','lint','typecheck'))):
            # Invoke npm's JS entrypoint directly; never pass generated text through cmd.exe.
            npm=shutil.which('npm')
            node=shutil.which('node')
            candidates=[Path(npm).parent/'node_modules/npm/bin/npm-cli.js'] if npm else []
            if not node or not candidates or not candidates[0].exists():
                raise ValueError('Node.js/npm is unavailable for project checks.')
            args=[node,str(candidates[0]),*args[1:]]
        else:
            raise ValueError('Supported checks: python -m pytest, python -m unittest, python -m compileall, npm test, npm run build/test/lint/typecheck.')
        return args

    async def run_command(self,tenant_id,run,command):
        project=self.store.get(tenant_id,'projects',run['workflow']['project_id'])
        record={'id':uid(),'command':command,'started_at':now(),'finished_at':None,'status':'running','output':'','exit_code':None,'iteration':run['iteration']}
        run['commands'].append(record);self.store.put(tenant_id,'runs',run)
        self.store.event(tenant_id,run['id'],'command.started','Running: '+command,iteration=run['iteration'])
        proc=None
        try:
            # Every caller routes through here, so the execute-mode gate lives here and
            # not in each route; rejection is reported through the command record.
            if run['workflow'].get('execution_mode')!='execute':
                raise ValueError('Project checks run only in Build & test mode. Change this project’s execution mode to run commands.')
            argv=self.command_argv(project['root'],command)
            env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE','HOME','APPDATA','LOCALAPPDATA','LANG'}}
            env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',CI='true',NO_COLOR='1')
            kwargs={'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True}
            proc=await asyncio.create_subprocess_exec(*argv,cwd=project['root'],env=env,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,**kwargs)
            async with asyncio.timeout(120):
                while True:
                    data=await proc.stdout.read(4096)
                    if not data:break
                    chunk=data.decode('utf-8',errors='replace')
                    record['output']=(record['output']+chunk)[-150000:]
                    self.store.put(tenant_id,'runs',run)
                    self.store.event(tenant_id,run['id'],'command.output',chunk[:4000],iteration=run['iteration'],command_id=record['id'])
                record['exit_code']=await proc.wait()
                record['status']='completed' if record['exit_code']==0 else 'failed'
        except asyncio.CancelledError:
            record.update(status='cancelled',finished_at=now())
            self.store.put(tenant_id,'runs',run)
            raise
        except (ValueError,OSError,TimeoutError) as exc:
            record.update(status='failed',output=record['output']+'\n'+('Command exceeded its 120-second limit.' if isinstance(exc,TimeoutError) else str(exc)))
        finally:
            if proc and proc.returncode is None:
                if os.name=='nt':
                    killer=await asyncio.create_subprocess_exec('taskkill','/PID',str(proc.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,creationflags=0x08000000)
                    await killer.wait()
                else:
                    import signal
                    os.killpg(proc.pid,signal.SIGKILL)
                await proc.wait()
            record['finished_at']=now();self.store.put(tenant_id,'runs',run)
        self.store.event(tenant_id,run['id'],'command.completed',f'{command} · {"passed" if record["status"]=="completed" else "failed"}',iteration=run['iteration'],exit_code=record['exit_code'])
        return record
