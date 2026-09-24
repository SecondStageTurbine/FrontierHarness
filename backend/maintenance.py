"""Keeping the agent tools current, and bringing conversations from their own histories into Frontier."""
import asyncio
import json
import re
import shutil
from pathlib import Path

from .broker import CLI_TOOLS, ProviderError, probe_cli, resolve_cli
from .localprocess import child_env, child_flags, terminate
from .store import now, uid

PACKAGES = {'claude_cli': '@anthropic-ai/claude-code', 'codex_cli': '@openai/codex', 'opencode_cli': 'opencode-ai', 'gemini_cli': '@google/gemini-cli'}


def npm_argv():
    npm, node = shutil.which('npm'), shutil.which('node')
    cli = Path(npm).parent/'node_modules/npm/bin/npm-cli.js' if npm else None
    if not node or not cli or not cli.exists():
        raise ProviderError('Node.js and npm are needed to check or update the agent tools.')
    return [node, str(cli)]


async def npm(*args, timeout=60):
    proc = await asyncio.create_subprocess_exec(*npm_argv(), *args, env=child_env(NO_COLOR='1', NO_UPDATE_NOTIFIER='1'), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except TimeoutError:
        await terminate(proc)
        raise ProviderError('npm did not answer in time.') from None
    return proc.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


async def versions():
    """Installed and latest version of each agent tool, and whether an update is possible from here."""
    async def one(provider):
        label = CLI_TOOLS[provider][2]
        row = {'provider': provider, 'label': label, 'installed': None, 'latest': None, 'updatable': False, 'error': None}
        try:
            row['installed'] = re.search(r'\d+\.\d+(\.\d+)?', await probe_cli(provider)).group(0)
        except (ProviderError, AttributeError) as exc:
            row['error'] = str(exc) if isinstance(exc, ProviderError) else 'Version not recognised.'
            return row
        try:
            launch = resolve_cli(provider)
            row['updatable'] = any('node_modules' in part or 'npm' in part.lower() for part in launch)
            code, out, _ = await npm('view', PACKAGES[provider], 'version', timeout=30)
            if code == 0 and out.strip():
                row['latest'] = out.strip().splitlines()[-1]
        except ProviderError as exc:
            row['error'] = str(exc)
        return row
    return await asyncio.gather(*(one(p) for p in CLI_TOOLS))


async def update(provider):
    if provider not in PACKAGES:
        raise ProviderError('Unknown tool.')
    code, out, err = await npm('install', '-g', f'{PACKAGES[provider]}@latest', timeout=600)
    if code != 0:
        raise ProviderError(f'npm could not update {CLI_TOOLS[provider][2]}: {(err or out).strip()[:600]}')
    return await probe_cli(provider)


# ── Importing past conversations ──

def claude_slug(root):
    return re.sub(r'[^A-Za-z0-9]', '-', str(root))


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') in ('text', 'input_text', 'output_text'))
    return ''


def read_claude(root):
    """Claude Code keeps one JSONL per conversation under ~/.claude/projects/<slug>/."""
    folder = Path.home()/'.claude'/'projects'/claude_slug(root)
    if not folder.is_dir():
        return []
    found = []
    for file in sorted(folder.glob('*.jsonl'), key=lambda f: f.stat().st_mtime):
        messages, title, first_at = [], None, None
        for line in file.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = record.get('type')
            if kind == 'user' and not messages and record.get('entrypoint') == 'sdk-cli':
                break  # Started by `claude -p`, as Frontier's own turns are: not a conversation someone had.
            if kind == 'custom-title':
                title = record.get('customTitle') or record.get('title') or title
            if record.get('isSidechain') or record.get('isMeta') or kind not in ('user', 'assistant'):
                continue
            text = text_of((record.get('message') or {}).get('content')).strip()
            if not text or text.startswith(('<command-name>', '<local-command', '<system-reminder>')):
                continue
            stamp = record.get('timestamp') or now()
            first_at = first_at or stamp
            if kind == 'user' and (messages and messages[-1]['role'] == 'user'):
                continue  # Tool results echo as user records; keep the human's own message.
            messages.append({'id': uid(), 'role': kind, 'content': text[:40000], 'created_at': stamp,
                             **({'status': 'complete', 'finished_at': stamp, 'model_name': 'Claude Code (imported)', 'provider': 'claude_cli', 'changes': []} if kind == 'assistant' else {})})
        if sum(1 for m in messages if m['role'] == 'user') >= 1 and any(m['role'] == 'assistant' for m in messages):
            found.append({'source': 'claude', 'key': file.stem, 'name': (title or next(m['content'] for m in messages if m['role'] == 'user').splitlines()[0])[:80],
                          'messages': messages, 'created_at': first_at or now()})
    return found


def read_codex(root):
    """Codex writes rollouts under ~/.codex/sessions/YYYY/MM/DD/, each naming its cwd in the first record."""
    base = Path.home()/'.codex'/'sessions'
    if not base.is_dir():
        return []
    wanted = str(Path(root).resolve()).lower()
    found = []
    for file in sorted(base.rglob('*.jsonl'), key=lambda f: f.stat().st_mtime):
        try:
            with file.open(encoding='utf-8', errors='replace') as handle:
                head = json.loads(handle.readline() or '{}')
        except (OSError, json.JSONDecodeError):
            continue
        meta = head.get('payload') or {}
        if head.get('type') != 'session_meta' or meta.get('originator') == 'codex_exec' or str(Path(meta.get('cwd') or '').resolve()).lower() != wanted:
            continue
        messages = []
        for line in file.read_text(encoding='utf-8', errors='replace').splitlines()[1:]:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get('payload') or {}
            if record.get('type') != 'response_item' or payload.get('type') != 'message' or payload.get('role') not in ('user', 'assistant'):
                continue
            text = text_of(payload.get('content')).strip()
            if not text or text.startswith('<'):
                continue  # Environment and plugin notices arrive as user messages wrapped in tags.
            stamp = record.get('timestamp') or now()
            if payload['role'] == 'user' and messages and messages[-1]['role'] == 'user':
                messages[-1]['content'] += '\n\n' + text[:40000]
                continue
            messages.append({'id': uid(), 'role': payload['role'], 'content': text[:40000], 'created_at': stamp,
                             **({'status': 'complete', 'finished_at': stamp, 'model_name': 'Codex (imported)', 'provider': 'codex_cli', 'changes': []} if payload['role'] == 'assistant' else {})})
        if any(m['role'] == 'user' for m in messages) and any(m['role'] == 'assistant' for m in messages):
            found.append({'source': 'codex', 'key': meta.get('id') or file.stem, 'name': next(m['content'] for m in messages if m['role'] == 'user').splitlines()[0][:80],
                          'messages': messages, 'created_at': meta.get('timestamp') or now()})
    return found


def import_history(store, tenant_id, project, sources=('claude', 'codex')):
    """Conversations the tools kept about this folder become sessions here, once each."""
    existing = {s.get('imported_key') for s in store.list(tenant_id, 'sessions') if s.get('imported_key')}
    imported = []
    for source in sources:
        for item in READERS[source](project['root']):
            if f'{source}:{item["key"]}' in existing:
                continue
            session = import_item(store, tenant_id, project, source, item)
            imported.append({'id': session['id'], 'name': session['name'], 'source': source, 'messages': len(item['messages'])})
    return imported


READERS = {'claude': read_claude, 'codex': read_codex}


def native_of(source, key, count):
    """The tool's own session behind an imported conversation. The next turn by the same tool resumes
    it natively, sending only the messages after `upto`; any other agent replays the transcript."""
    return {'provider': 'claude_cli' if source == 'claude' else 'codex_cli', 'id': key, 'upto': count}


def import_item(store, tenant_id, project, source, item):
    session = {'id': uid(), 'project_id': project['id'], 'name': item['name'] or 'Imported conversation', 'messages': item['messages'], 'commands': [],
               'created_at': item['created_at'], 'updated_at': item['messages'][-1]['created_at'], 'auto_named': False,
               'imported_key': f'{source}:{item["key"]}', 'imported_from': source, 'native': native_of(source, item['key'], len(item['messages']))}
    store.put(tenant_id, 'sessions', session)
    return session


def cli_conversations(store, tenant_id, project):
    """Recent Claude and Codex conversations about this folder, newest first, for the resume picker."""
    imported = {s['imported_key']: s['id'] for s in store.list(tenant_id, 'sessions') if s.get('imported_key') and s.get('project_id') == project['id']}
    rows = []
    for source, reader in READERS.items():
        for item in reader(project['root']):
            first = next(m['content'] for m in item['messages'] if m['role'] == 'user')
            rows.append({'source': source, 'key': item['key'], 'name': item['name'], 'first': first[:300], 'created_at': item['created_at'],
                         'updated_at': item['messages'][-1]['created_at'], 'messages': len(item['messages']),
                         'session_id': imported.get(f'{source}:{item["key"]}')})
    return sorted(rows, key=lambda r: r['updated_at'], reverse=True)[:40]


def resume_cli(store, tenant_id, project, source, key):
    """One CLI conversation as a session here: imported now, or the one imported before."""
    item = next((i for i in READERS[source](project['root']) if i['key'] == key), None)
    if item is None:
        raise LookupError(f'That {source.title()} conversation is no longer on this computer.')
    existing = next((s for s in store.list(tenant_id, 'sessions') if s.get('imported_key') == f'{source}:{key}' and s.get('project_id') == project['id']), None)
    if existing is None:
        return import_item(store, tenant_id, project, source, item)
    if existing.get('archived'):
        existing['archived'] = False
        store.put(tenant_id, 'sessions', existing)
    return existing



INSTALL = {
    'claude_cli': {'install': 'irm https://claude.ai/install.ps1 | iex', 'signin': 'claude', 'models': ['sonnet', 'opus', 'haiku', 'fable'], 'default': 'sonnet'},
    'codex_cli': {'install': 'npm install -g @openai/codex', 'signin': 'codex login', 'models': ['gpt-6-sol', 'gpt-6-astra', 'gpt-6-luna'], 'default': 'gpt-6-sol'},
    'opencode_cli': {'install': 'npm install -g opencode-ai', 'signin': 'opencode auth login', 'models': ['opencode/free'], 'default': 'opencode/free'},
    'gemini_cli': {'install': 'npm install -g @google/gemini-cli', 'signin': 'gemini', 'models': ['gemini-2.5-pro', 'gemini-2.5-flash'], 'default': 'gemini-2.5-pro'},
}


async def codex_models():
    """The identifiers this machine's Codex offers, and its configured default."""
    try:
        launch = resolve_cli('codex_cli')
    except ProviderError:
        return [], None
    proc = await asyncio.create_subprocess_exec(*launch, 'debug', 'models', env=child_env(), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **child_flags())
    try:
        async with asyncio.timeout(25):
            out, _ = await proc.communicate()
        slugs = [m['slug'] for m in json.loads(out.decode('utf-8', 'replace')).get('models', []) if m.get('visibility', 'list') == 'list']
    except (TimeoutError, ValueError, KeyError):
        await terminate(proc)
        slugs = []
    default = None
    config = Path.home()/'.codex'/'config.toml'
    if config.is_file():
        found = re.search(r'^model\s*=\s*"([^"]+)"', config.read_text(encoding='utf-8', errors='replace'), re.M)
        default = found.group(1) if found else None
    return slugs, default


def opencode_default():
    for path in (Path.home()/'.config'/'opencode'/'opencode.json', Path.home()/'.config'/'opencode'/'opencode.jsonc'):
        if path.is_file():
            try:
                text = re.sub(r'^\s*//.*$', '', path.read_text(encoding='utf-8', errors='replace'), flags=re.M)
                return json.loads(text).get('model')
            except ValueError:
                return None
    return None


async def detect():
    """Each agent tool: whether it is installed, where, its version, and the identifiers to offer."""
    async def one(provider):
        info = dict(INSTALL[provider])
        row = {'provider': provider, 'label': CLI_TOOLS[provider][2], 'installed': False, 'path': None, 'version': None,
               'install': info['install'], 'signin': info['signin'], 'models': list(info['models']), 'default': info['default']}
        try:
            row['path'] = ' '.join(resolve_cli(provider))
            row['version'] = re.search(r'\d+\.\d+(\.\d+)?', await probe_cli(provider)).group(0)
            row['installed'] = True
        except (ProviderError, AttributeError, OSError):
            return row
        if provider == 'codex_cli':
            slugs, default = await codex_models()
            if slugs:
                row['models'] = slugs
            row['default'] = default if default in row['models'] else (row['models'][0] if row['models'] else row['default'])
        if provider == 'opencode_cli':
            configured = opencode_default()
            if configured:
                row['models'] = [configured] + [m for m in row['models'] if m != configured]
                row['default'] = configured
        return row
    return await asyncio.gather(*(one(p) for p in CLI_TOOLS))
