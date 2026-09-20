"""Project-bound file operations and captured local checks for the desktop harness.

File access rejects traversal, symlinks/junction escapes, sensitive files, and
overlapping cross-tenant project roots. Commands run only in explicit execute mode;
they are local processes, not an OS sandbox, and receive a scrubbed environment.
"""
import asyncio
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from pypdf import PdfReader
from .localprocess import child_env, child_flags, terminate
from .store import TenantIsolationViolationException, now, uid

IGNORED = {'.git','.venv','venv','node_modules','dist','build','target','__pycache__','.pytest_cache','.next','.idea','.vscode','.ssh','.aws','.azure','.codex','data'}
SENSITIVE = {'.env','.npmrc','.pypirc','credentials','credentials.json','secret.key','harness.db','id_rsa','id_ed25519'}
TEXT_EXTENSIONS = {'.py','.js','.jsx','.ts','.tsx','.json','.md','.txt','.html','.css','.scss','.yml','.yaml','.toml','.ini','.cfg','.xml','.sql','.rs','.go','.java','.c','.h','.cpp','.sh','.ps1','.csv','.lock','.gitignore','.pdf'}

# A rooted path at the start of an argument, or straight after an option prefix
# ('-sC:\Temp', '--rootdir=/etc'). Driveless roots are rooted on Windows too: the
# OS resolves '/Temp' against the current drive.
ROOTED_ARG = re.compile(r'^(?:-{1,2}[A-Za-z][\w-]*=?)?(?:[/\\]|[A-Za-z]:)')

def path_key(path):
    """Match Windows filesystem identity without changing displayed path spelling."""
    return path.casefold() if os.name=='nt' else path

def pdf_text(data):
    """Extract a PDF's text. Shared by project files and uploaded attachments."""
    reader=PdfReader(io.BytesIO(data))
    if len(reader.pages)>100:
        raise ValueError('PDFs must contain at most 100 pages.')
    return '\n'.join(page.extract_text() or '' for page in reader.pages)

def read_form(text):
    """The text a later read() returns: Python decodes files with universal newlines."""
    return text.replace('\r\n','\n').replace('\r','\n')

class ProjectFiles:
    def __init__(self,store):
        self.store=store

    def create(self,tenant_id,name,root=None):
        project_id=uid()
        if root:
            try:
                location=Path(root).expanduser().resolve(strict=True)
            except OSError:
                raise ValueError('That folder is not available. Check the path, or reconnect the drive.') from None
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
        try:
            root=Path(project['root']).resolve(strict=True)
        except OSError:
            raise ValueError('The project folder is unavailable. Reconnect the drive or restore the folder.') from None
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
        return self.walk(tenant_id,project_id)[0]

    def walk(self,tenant_id,project_id):
        """List every entry the project exposes, and what the walk itself refused.

        The refusals are returned rather than dropped. A folder pruned by name holds a
        user's documents as often as a build output, and a walk that stops at its limit
        looks exactly like a smaller project to everything downstream.
        """
        project=self.store.get(tenant_id,'projects',project_id)
        root=Path(project['root'])
        if not root.is_dir():
            raise ValueError('The project folder is unavailable. Reconnect the drive or restore the folder.')
        result=[];refused=[]
        for directory,dirs,files in os.walk(root,followlinks=False):
            keep=[]
            for name in sorted(dirs):
                path=Path(directory)/name
                relative=path.relative_to(root).as_posix()
                if name in IGNORED or name.startswith('.'):
                    refused.append(relative+'/ - excluded folder name.')
                elif path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
                    refused.append(relative+'/ - linked folders are excluded.')
                else:
                    keep.append(name)
            dirs[:]=keep
            for name in sorted(files):
                relative=(Path(directory)/name).relative_to(root).as_posix()
                try:
                    file=self.resolve(tenant_id,project_id,relative)
                    if file.is_file():
                        result.append({'path':relative,'size':file.stat().st_size,'text':file.suffix in TEXT_EXTENSIONS or name in ('Dockerfile','Makefile','LICENSE')})
                except (ValueError,OSError) as exc:
                    refused.append(f'{relative} - {exc}')
                    continue
                if len(result)>=2000:
                    refused.append('The folder holds more than 2,000 files. The rest were not listed at all.')
                    return result,refused
        return result,refused

    def read(self,tenant_id,project_id,relative):
        file=self.resolve(tenant_id,project_id,relative)
        if not file.is_file():
            raise ValueError('This file is no longer available.')
        try:
            size=file.stat().st_size
        except OSError:
            raise ValueError('This file could not be opened. Another program may be holding it.') from None
        if file.suffix.lower()=='.pdf':
            if size>5_000_000:
                raise ValueError('PDFs are limited to 5 MB.')
            try:
                content=pdf_text(file.read_bytes())
            except Exception as exc:
                raise ValueError(f'This PDF could not be read: {exc}') from None
            if not content.strip():
                raise ValueError('This PDF holds no extractable text. A scanned PDF needs OCR before it can be read.')
            return {'path':relative,'content':content,'hash':hashlib.sha256(content.encode()).hexdigest()}
        if size>300000:
            raise ValueError('Preview is limited to text files under 300 KB.')
        try:
            content=file.read_text(encoding='utf-8')
        except UnicodeError:
            raise ValueError('This file is not UTF-8 text.') from None
        except OSError:
            raise ValueError('This file could not be opened. Another program may be holding it.') from None
        if '\x00' in content:
            raise ValueError('Binary files cannot be previewed.')
        return {'path':relative,'content':content,'hash':hashlib.sha256(content.encode()).hexdigest()}

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

    async def run_command(self,tenant_id,project_id,session_id,command):
        """Run one supported project check in the project folder and stream it to the terminal.

        This is the user's own check, run at their request. The agent runs its own commands
        through its tool, under the posture chosen for that turn; the two do not share a path.
        """
        project=self.store.get(tenant_id,'projects',project_id)
        session=self.store.get(tenant_id,'sessions',session_id)
        record={'id':uid(),'command':command,'started_at':now(),'finished_at':None,'status':'running','output':'','exit_code':None}
        session.setdefault('commands',[]).append(record)
        session['commands']=session['commands'][-40:]
        self.store.put(tenant_id,'sessions',session)
        self.store.event(tenant_id,session_id,'command.started','Running: '+command)
        proc=None
        def save():
            current=self.store.get(tenant_id,'sessions',session_id)
            for index,item in enumerate(current.get('commands',[])):
                if item['id']==record['id']:
                    current['commands'][index]=record
                    break
            else:
                current.setdefault('commands',[]).append(record)
            self.store.put(tenant_id,'sessions',current)
        try:
            argv=self.command_argv(project['root'],command)
            env=child_env(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',CI='true',NO_COLOR='1')
            proc=await asyncio.create_subprocess_exec(*argv,cwd=project['root'],env=env,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,**child_flags())
            async with asyncio.timeout(120):
                while True:
                    data=await proc.stdout.read(4096)
                    if not data:break
                    chunk=data.decode('utf-8',errors='replace')
                    record['output']=(record['output']+chunk)[-150000:]
                    save()
                    self.store.event(tenant_id,session_id,'command.output',chunk[:4000],command_id=record['id'])
                record['exit_code']=await proc.wait()
                record['status']='completed' if record['exit_code']==0 else 'failed'
        except asyncio.CancelledError:
            record.update(status='cancelled',finished_at=now());save()
            raise
        except (ValueError,OSError,TimeoutError) as exc:
            record.update(status='failed',output=record['output']+'\n'+('Command exceeded its 120-second limit.' if isinstance(exc,TimeoutError) else str(exc)))
        finally:
            if proc:await terminate(proc)
            record['finished_at']=now();save()
        self.store.event(tenant_id,session_id,'command.completed',f'{command} · {"passed" if record["status"]=="completed" else "failed"}',exit_code=record['exit_code'])
        return record
