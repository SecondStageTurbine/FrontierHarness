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


# ── Subscription limits: how much of each plan's window is used, as the tools' own /usage shows ──

LIMITS_CACHE = {}  # provider -> (valid until, result). The endpoints rate-limit hard, so they are asked rarely.
LIMITS_TTL = 300


def fetch_json(url, headers):
    """GET a JSON document; a refusal carries the server's Retry-After when it sends one."""
    import urllib.error, urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Frontier', **headers}), timeout=15) as response:
            return json.loads(response.read().decode('utf-8')), None, None
    except urllib.error.HTTPError as exc:
        wait = exc.headers.get('retry-after') if exc.headers else None
        wait = int(wait) if wait and wait.strip().isdigit() else None
        if exc.code == 429:
            return None, 'Usage figures are rate limited by the provider for a moment. Your allowance is not affected.', wait
        if exc.code in (401, 403):
            return None, 'The sign-in has expired. Run the tool once in a terminal to refresh it.', None
        return None, f'The provider answered HTTP {exc.code}.', wait
    except (OSError, ValueError) as exc:
        return None, f'Could not reach the provider: {exc}', None


def read_token(path, *keys):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        for key in keys:
            value = value[key]
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claude_limits():
    import os
    home = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home()/'.claude')
    token = read_token(home/'.credentials.json', 'claudeAiOauth', 'accessToken')
    if not token:
        return None  # Not signed in to Claude Code with a subscription on this computer.
    payload, error, wait = fetch_json('https://api.anthropic.com/api/oauth/usage', {'Authorization': f'Bearer {token}', 'anthropic-beta': 'oauth-2025-04-20'})
    if payload is None:
        return {'provider': 'claude_cli', 'label': 'Claude', 'plan': None, 'error': error, 'retry_after': wait, 'limits': []}
    names = {'session': 'Current session', 'weekly_all': 'Week, all models'}
    limits = []
    for entry in payload.get('limits') or []:
        if not isinstance(entry.get('percent'), (int, float)):
            continue
        label = names.get(entry.get('kind')) or ((entry.get('scope') or {}).get('model') or {}).get('display_name') or str(entry.get('kind') or 'Usage').replace('_', ' ').capitalize()
        limits.append({'label': label, 'percent': float(entry['percent']), 'severity': entry.get('severity') or 'normal', 'resets_at': entry.get('resets_at')})
    return {'provider': 'claude_cli', 'label': 'Claude', 'plan': None, 'error': None, 'retry_after': None, 'limits': limits}


def codex_limits():
    import os
    from datetime import datetime, timezone
    home = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    token = read_token(home/'auth.json', 'tokens', 'access_token')
    if not token:
        return None
    payload, error, wait = fetch_json('https://chatgpt.com/backend-api/wham/usage', {'Authorization': f'Bearer {token}'})
    if payload is None:
        return {'provider': 'codex_cli', 'label': 'Codex', 'plan': None, 'error': error, 'retry_after': wait, 'limits': []}
    limits = []
    for key in ('primary_window', 'secondary_window'):
        window = (payload.get('rate_limit') or {}).get(key)
        if not window or not isinstance(window.get('used_percent'), (int, float)):
            continue
        seconds = window.get('limit_window_seconds') or 0
        label = {604800: 'Week', 18000: '5 hours'}.get(seconds) or (f'{seconds // 86400} days' if seconds and seconds % 86400 == 0 else f'{seconds // 3600} hours')
        reset = window.get('reset_at')
        limits.append({'label': label, 'percent': float(window['used_percent']), 'severity': 'normal',
                       'resets_at': datetime.fromtimestamp(reset, timezone.utc).isoformat() if isinstance(reset, (int, float)) else None})
    return {'provider': 'codex_cli', 'label': 'Codex', 'plan': payload.get('plan_type'), 'error': None, 'retry_after': None, 'limits': limits}


def subscription_limits(force=False):
    """Each signed-in subscription's usage windows. A good answer is kept five minutes; a refusal until
    the provider's Retry-After, or a minute, so a refresh never hammers an endpoint that rate-limits."""
    import time
    found = []
    for provider, read in (('claude_cli', claude_limits), ('codex_cli', codex_limits)):
        until, result = LIMITS_CACHE.get(provider, (0, None))
        if force and result is not None and not result['error']:
            until = 0  # A refresh re-asks a good answer, never one the provider asked us to wait on.
        if time.time() >= until:
            result = read()
            ttl = LIMITS_TTL if result is None or not result['error'] else (result['retry_after'] or 60)
            LIMITS_CACHE[provider] = (time.time() + ttl, result)
        if result is not None:
            found.append(result)
    return found
