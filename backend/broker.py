"""Only this module launches an agent. No global credentials, no implicit environment routing.

A turn runs a locally installed agent command line tool, signed in with the user's own Claude,
ChatGPT or OpenCode subscription, so those credentials live outside this application and are
never seen by it. What the agent may do to the project is chosen per turn and translated here
into each tool's own spelling of the same three postures; nothing is silently escalated.

Several subscriptions can be connected to one provider. Each carries an account name whose
credential directory this module points the tool at, and a turn moves to the next connected
account when the selected one answers that its usage window is spent.
"""
from dataclasses import dataclass, field
import asyncio
import json
import os
import re
import logging
import shutil
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from .localprocess import child_env, child_flags, terminate
from .store import Store

# The package entry point keeps a shim-only npm install usable: create_subprocess_exec cannot
# execute a .cmd, and generated text must never be routed through cmd.exe.
CLI_TOOLS = {
    'claude_cli': ('claude', 'node_modules/@anthropic-ai/claude-code/cli.js', 'Claude Code'),
    'codex_cli': ('codex', 'node_modules/@openai/codex/bin/codex.js', 'Codex'),
    # OpenCode ships a compiled binary rather than a script, so this entry carries no extension.
    'opencode_cli': ('opencode', 'node_modules/opencode-ai/bin/opencode', 'OpenCode'),
    'gemini_cli': ('gemini', 'node_modules/@google/gemini-cli/dist/index.js', 'Gemini CLI'),
}
# Each agent tool reads the subscription it is signed in as out of one directory. Pointing that
# variable at a directory per named account is the whole mechanism behind connecting more than
# one subscription to the same provider: nothing is copied, and no credential is read here.
ACCOUNT_HOME_VARS = {'claude_cli': ('CLAUDE_CONFIG_DIR',), 'codex_cli': ('CODEX_HOME',), 'opencode_cli': ('XDG_DATA_HOME',), 'gemini_cli': ('GEMINI_CLI_HOME',)}
SIGNIN_COMMANDS = {'claude_cli': 'claude', 'codex_cli': 'codex login', 'opencode_cli': 'opencode auth login', 'gemini_cli': 'gemini'}
# A spent subscription is skipped for this long before it is tried again. ponytail: a fixed
# window, because the three CLIs do not report a reset time in any shared form. Parse the real
# reset if idling an hour ever costs more than the two seconds a premature retry costs.
COOLDOWN_SECONDS = 3600
# An agentic turn reads, edits and runs checks, so it is bounded in minutes rather than seconds.
TURN_TIMEOUT = 5400
# The only CLI output this module reads rather than discards. A subscription with nothing left
# is a routine condition, not a broken install, and telling the two apart is what lets a turn
# move to the next login instead of stopping. Matched text is classified and then dropped.
LIMIT_PHRASES = ('usage limit', 'rate limit', 'rate_limit', 'limit reached', 'quota', 'too many requests', 'out of credit', 'insufficient_quota')

def cooling(broker, tenant_id, model_id):
    """Whether a login ran out of usage recently and is being given a rest."""
    return broker.cooldowns.get((tenant_id, model_id), 0) > time.monotonic()

class ProviderError(RuntimeError):
    def __init__(self, message, retryable=False, exhausted=False):
        super().__init__(message)
        self.retryable = retryable
        self.exhausted = exhausted  # A subscription with nothing left, which another login can serve.

@dataclass
class AgentResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    served_by: str | None = None  # The model row that answered, when a spent subscription was switched away from.
    session_id: str | None = None  # The tool's own conversation id, for resuming it natively next turn.
    resumed: bool = False  # This turn continued the tool's own session rather than replaying the transcript.
    blocked: list = field(default_factory=list)  # Hosts the sandbox's network filter refused during the turn.

def is_exhausted(*texts):
    return any(phrase in (text or '').lower() for text in texts for phrase in LIMIT_PHRASES)

def strip_echo(text, prompt):
    """Output with the prompt, and any line of it, taken out."""
    if not text or not prompt:
        return text
    lines = {line.strip() for line in prompt.splitlines() if line.strip()}
    return '\n'.join(line for line in text.replace(prompt, '').splitlines() if line.strip() not in lines)

def cli_exit_error(provider, code, out, err):
    """Why a tool stopped, when it left nothing readable behind. Output can echo the prompt, so
    it is classified here and the message says only what the user can act on."""
    if is_exhausted(out, err):
        return exhausted_error(provider)
    return ProviderError(f'The {CLI_TOOLS[provider][2]} command line tool exited with code {code}. Run it once in a terminal to confirm the subscription is signed in.')

def exhausted_error(provider):
    return ProviderError(f'The {CLI_TOOLS[provider][2]} subscription has no usage left in its current window.', retryable=True, exhausted=True)

def account_home(store, tenant_id, provider, account):
    """The directory holding one named subscription login, created on first use.

    Scoped by workspace for the reason every other entity is: one workspace's connected
    subscription must never serve another's turn. Frontier writes nothing into it; the tool does.
    """
    home = Path(store.directory)/'subscriptions'/tenant_id/provider/account
    home.mkdir(parents=True, exist_ok=True)
    # Claude Code refuses to start against a directory with no configuration file at all, and
    # exits before it can report the sign-in that is actually missing. An empty object is enough.
    config = home/'.claude.json'
    if provider == 'claude_cli' and not config.exists():
        config.write_text('{}', encoding='utf-8')
    return home

def account_env(store, tenant_id, config):
    """Point a tool at one named login. Empty means the machine's own default sign-in."""
    account = config.get('account')
    if not account or config['provider'] not in ACCOUNT_HOME_VARS:
        return {}
    home = str(account_home(store, tenant_id, config['provider'], account))
    return {variable: home for variable in ACCOUNT_HOME_VARS[config['provider']]}

# Where each tool's own installer puts it when that folder may not be on the PATH Frontier started with.
KNOWN_DIRS = {
    'claude_cli': ['~/.local/bin', '~/.claude/local', '%LOCALAPPDATA%/Programs/claude', '%LOCALAPPDATA%/Microsoft/WinGet/Links'],
    'codex_cli': ['~/.local/bin', '%LOCALAPPDATA%/Programs/codex', '%LOCALAPPDATA%/Microsoft/WinGet/Links'],
    'opencode_cli': ['~/.local/bin', '~/.opencode/bin', '%LOCALAPPDATA%/Microsoft/WinGet/Links'],
    'gemini_cli': ['~/.local/bin', '%LOCALAPPDATA%/Microsoft/WinGet/Links'],
}


def refresh_path():
    """Re-read PATH from the registry, as a new terminal would.

    Frontier lives in the tray, so it can outlive the moment a tool was installed; the PATH it
    inherited at login then lacks the folder the installer just added. Merging the current user and
    machine values in lets a tool installed a minute ago be found without restarting Frontier.
    """
    if os.name != 'nt':
        return
    import winreg
    parts = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'), (winreg.HKEY_CURRENT_USER, 'Environment')):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, 'Path')
                parts += [os.path.expandvars(v) for v in str(value).split(';') if v.strip()]
        except OSError:
            continue
    current = os.environ.get('PATH', '').split(';')
    merged = list(dict.fromkeys([p for p in current + parts if p]))
    os.environ['PATH'] = ';'.join(merged)


def locate(provider):
    """The tool on PATH, else in the folders its installers use; None when it is nowhere."""
    name = CLI_TOOLS[provider][0]
    found = shutil.which(name)
    if found:
        return found
    refresh_path()
    found = shutil.which(name)
    if found:
        return found
    for folder in KNOWN_DIRS.get(provider, []):
        base = Path(os.path.expandvars(os.path.expanduser(folder)))
        for candidate in (base/(name+'.exe'), base/(name+'.cmd'), base/name):
            if candidate.is_file():
                return str(candidate)
    return None


def package_bin(root, entry, name):
    """The program an npm package names for `name` in its package.json, resolved inside the package.

    `entry` is the legacy path of the tool's script under `root`; its first three segments locate the
    package folder (node_modules/<scope>/<pkg>, or two for an unscoped package).
    """
    parts = Path(entry).parts
    depth = 3 if len(parts) > 2 and parts[1].startswith('@') else 2
    package = Path(root, *parts[:depth])
    manifest = package/'package.json'
    if not manifest.is_file():
        return None
    try:
        bins = json.loads(manifest.read_text(encoding='utf-8')).get('bin')
    except (OSError, ValueError):
        return None
    target = bins if isinstance(bins, str) else (bins or {}).get(name) if isinstance(bins, dict) else None
    if not target:
        return None
    program = (package/target).resolve()
    for candidate in (program, program.with_suffix('.exe')) if os.name == 'nt' and not program.suffix else (program,):
        if candidate.is_file():
            return [str(candidate)]
    return None


def _need_node(label):
    raise ProviderError(f'{label} is installed through npm, so Node.js is required to run it, and node was not found on the path.')


def resolve_cli(provider):
    name, entry, label = CLI_TOOLS[provider]
    found = locate(provider)
    if found:
        suffix = Path(found).suffix.lower()
        # Windows cannot start a script shim directly, and an extensionless file there is one.
        shim = suffix in ('.cmd', '.bat', '.ps1') or (os.name == 'nt' and not suffix)
        if not shim:
            return [found]
    node = shutil.which('node')
    roots = ([Path(found).parent] if found else []) + ([Path(os.environ['APPDATA'])/'npm'] if os.environ.get('APPDATA') else [])
    for root in dict.fromkeys(roots):
        # What the installed package itself declares as its program comes first: newer Claude Code
        # releases ship a native bin/claude.exe in the npm package instead of the old cli.js.
        declared = package_bin(root, entry, name)
        if declared:
            return declared if declared[-1].lower().endswith('.exe') or not declared[-1].lower().endswith(('.js', '.mjs', '.cjs')) else ([node, declared[-1]] if node else _need_node(label))
        for script in ([root/entry] if entry.endswith('.js') else [root/(entry+'.exe'), root/entry]):
            if not script.is_file():
                continue
            if not entry.endswith('.js'):
                return [str(script)]  # A packaged binary starts itself; Node is not involved.
            if node:
                return [node, str(script)]
            raise ProviderError(f'{label} is installed through npm, so Node.js is required to run it, and node was not found on the path.')
    hint = {'claude_cli': 'Install it from https://claude.com/claude-code (in PowerShell: irm https://claude.ai/install.ps1 | iex), run `claude` once in a terminal to sign in',
            'codex_cli': 'Install it with `npm install -g @openai/codex`, run `codex login` once',
            'opencode_cli': 'Install it with `npm install -g opencode-ai`, run `opencode auth login` once',
            'gemini_cli': 'Install it with `npm install -g @google/gemini-cli`, run `gemini` once to sign in'}.get(provider, 'Install it and sign in once in a terminal')
    raise ProviderError(f'The {label} command line tool was not found on this computer: `{name}` is not on the PATH and not in the folders its installer uses. {hint}, then press Test connection again.')


def toml_value(value):
    """A Python value as the TOML literal Codex's `-c key=value` override reads."""
    if isinstance(value, dict):
        return '{' + ', '.join(f'{k} = {toml_value(v)}' for k, v in value.items()) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return json.dumps(str(value))

def mcp_config_for_claude(servers, approval=None):
    entries = {}
    for server in servers:
        entries[server['name']] = ({'type': 'http', 'url': server['url']} if server.get('transport') == 'http'
                                   else {'command': server['command'], 'args': server.get('args') or [], 'env': server.get('env') or {}})
    if approval:
        entries['frontier'] = {'command': approval['command'][0], 'args': approval['command'][1:],
                               'env': {'FRONTIER_URL': approval['url'], 'FRONTIER_TOKEN': approval['token'], **approval.get('env', {})}}
    return {'mcpServers': entries}

def mcp_config_for_opencode(servers):
    entries = {}
    for server in servers:
        entries[server['name']] = ({'type': 'remote', 'url': server['url'], 'enabled': True} if server.get('transport') == 'http'
                                   else {'type': 'local', 'command': [server['command'], *(server.get('args') or [])], 'environment': server.get('env') or {}, 'enabled': True})
    return {'mcp': entries}

def codex_write_mode(extras):
    """Codex's own Windows sandbox cannot start at low integrity; under Frontier's file sandbox, which confines
    writes to the project just as workspace-write does, Codex runs without its own."""
    return 'danger-full-access' if (extras or {}).get('outer_sandbox') else 'workspace-write'

FRONTIER_TOOLS = ('ask_user', 'board_list', 'board_add', 'board_update')  # Served by permission_tool beside `approve`.

def agent_argv(provider, launch, model_name, mode, root, final_path, extras=None):
    """One posture, four spellings. This is the only place the tools differ on power.

    `extras` carries what a turn adds beyond the posture: the workspace's MCP servers, and for
    Claude under Edit files, the approval tool that turns a permission prompt into a card.
    """
    extras = extras or {}
    servers = extras.get('mcp_servers') or []
    if provider == 'claude_cli':
        # Not `plan` mode for Read only: that makes Claude act as a planner — it writes a plan
        # file into its own home and answers about that file — which is a side effect outside
        # the project and the wrong voice for a question. Read only is the ordinary agent with
        # its writing and shell tools removed, refusing anything else rather than asking.
        disallowed = ['Bash', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'] if mode == 'read' else []
        if extras.get('approval') and mode != 'edit':
            # Claude's own question tool needs the permission prompt, which only Edit files routes to
            # Frontier; elsewhere it would be refused, so Claude asks through Frontier's ask_user instead.
            disallowed.append('AskUserQuestion')
        if extras.get('protect_env'):
            # The project keeps its secrets: Claude may not open .env files under any posture.
            disallowed += ['Read(./.env)', 'Read(./.env.*)', 'Read(**/.env)', 'Read(**/.env.*)']
        argv = [*launch, '-p', '--output-format', 'json', '--model', model_name,
                '--permission-mode', {'read': 'dontAsk', 'edit': 'acceptEdits', 'auto': 'bypassPermissions'}[mode]]
        if disallowed:
            argv += ['--disallowedTools', *disallowed]
        if extras.get('resume'):
            # Claude's own session continues, with its full internal state, instead of a replayed transcript.
            argv += ['--resume', extras['resume']]
        # The browser only looks at pages; asking the user before every click would stall the turn on
        # a card per step, so its tools are allowed outright under every posture. So are Frontier's own
        # question and task-board tools, which touch nothing in the project.
        allowed = (['mcp__frontier-browser'] if any(s['name'] == 'frontier-browser' for s in servers) else []) + \
                  ([f'mcp__frontier__{name}' for name in FRONTIER_TOOLS] if extras.get('approval') else [])
        if allowed:
            argv += ['--allowedTools', *allowed]
        if extras.get('mcp_config'):
            argv += ['--mcp-config', str(extras['mcp_config'])]
            if mode == 'edit' and extras.get('approval'):
                # Edits are accepted by the posture; anything else Claude would ask about is
                # routed to Frontier's approval tool and shown to the user instead of refused.
                argv += ['--permission-prompt-tool', 'mcp__frontier__approve']
        return argv
    if provider == 'codex_cli':
        # `-a` is a global flag and must precede the subcommand. Approvals are never waited on:
        # exec has no one to ask, so an unanswerable prompt would hang the turn to its timeout.
        argv = [*launch, '-a', 'never']
        for server in servers:
            key = 'mcp_servers.' + re.sub(r'[^A-Za-z0-9_-]', '_', server['name'])
            if server.get('transport') == 'http':
                argv += ['-c', f'{key}.url={toml_value(server["url"])}']
            else:
                argv += ['-c', f'{key}.command={toml_value(server["command"])}', '-c', f'{key}.args={toml_value(server.get("args") or [])}']
                if server.get('env'):
                    argv += ['-c', f'{key}.env={toml_value(server["env"])}']
            if server.get('trusted'):
                # Exec has no one to approve a tool call, so a server Frontier vouches for runs its tools unasked.
                argv += ['-c', f'{key}.default_tools_approval_mode="approve"']
        if extras.get('resume'):
            # `exec resume` takes no -C or --sandbox: the working directory is the project, and the
            # sandbox is set through its config key.
            if mode != 'auto':
                argv += ['-c', 'sandbox_mode=' + toml_value('read-only' if mode == 'read' else codex_write_mode(extras))]
            argv += ['exec', 'resume', '--skip-git-repo-check', '--model', model_name, '--output-last-message', str(final_path)]
            argv += ['--dangerously-bypass-approvals-and-sandbox'] if mode == 'auto' else []
            return argv + [extras['resume'], '-']
        argv += ['exec', '--skip-git-repo-check', '--model', model_name, '-C', str(root), '--output-last-message', str(final_path)]
        argv += (['--dangerously-bypass-approvals-and-sandbox'] if mode == 'auto'
                 else ['--sandbox', 'read-only' if mode == 'read' else codex_write_mode(extras)])
        return argv + ['-']
    if provider == 'gemini_cli':
        # The conversation arrives on stdin; -p is appended to it. Plan mode is Gemini's read only.
        return [*launch, '-p', 'The conversation above is on standard input. Answer its final USER message.', '-m', model_name,
                '-o', 'json', '--approval-mode', {'read': 'plan', 'edit': 'auto_edit', 'auto': 'yolo'}[mode]]
    argv = [*launch, 'run', '--format', 'json', '--model', model_name,
            '--agent', 'plan' if mode == 'read' else 'build']
    return argv + (['--auto'] if mode == 'auto' else [])

async def run_cli(argv, stdin_text, work, env=None):
    """Run an agent tool to completion, killing its whole tree on timeout or cancellation.

    Stderr comes back only so a spent subscription can be told apart from a broken install. It
    can echo the prompt, so callers classify it and it is never surfaced or stored.
    """
    proc = await asyncio.create_subprocess_exec(*argv, cwd=work, env=child_env(**(env or {})),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    try:
        out, err = await proc.communicate(stdin_text.encode('utf-8') if stdin_text is not None else None)
    finally:
        await terminate(proc)
    return proc.returncode, out.decode('utf-8', errors='replace'), err.decode('utf-8', errors='replace')

async def probe_cli(provider, env=None):
    """Confirm the tool runs, without a model call. Sign-in is only proven by a real turn."""
    with TemporaryDirectory(prefix='frontier-cli-') as directory:
        code, out, _ = await run_cli([*resolve_cli(provider), '--version'], None, directory, env)
    if code != 0:
        raise ProviderError(f'The {CLI_TOOLS[provider][2]} command line tool did not run. Reinstall it, then test again.')
    return out.strip()[:80]

def read_claude(out, err, code, provider):
    """Claude puts a refusal in its JSON and still exits nonzero, so the payload is read before
    the exit code: it names the reason, which the exit code cannot. Telling a spent subscription
    from one that was never signed in depends on it."""
    try:
        payload = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        if code != 0:
            raise cli_exit_error(provider, code, out, err)
        raise ProviderError(f'{CLI_TOOLS[provider][2]} returned output that could not be read.')
    if payload.get('is_error') or code != 0:
        if is_exhausted(payload.get('result'), err):
            raise exhausted_error(provider)
        # Claude's own words for what went wrong: a model it cannot use, a prompt too long, a
        # policy refusal. Guessing "sign in" when the login is fine sends the user the wrong way.
        reason = ' '.join(str(payload.get('result') or '').split())[:400]
        if re.search(r'issue with the selected model|unrecognized_model|may not exist or you may not have access', reason + ' ' + (err or ''), re.I):
            raise ProviderError(f'{CLI_TOOLS[provider][2]} does not recognise the model identifier on this row. Use the name the tool itself accepts, such as sonnet, opus, haiku or fable, in Settings → Agents & Providers. Claude said: {reason}')
        if re.search(r'not logged in|/login|please log in|not authenticated|invalid api key', reason, re.I):
            raise ProviderError(f'{CLI_TOOLS[provider][2]} is not signed in ({reason}). Run it once in a terminal to sign in, then try again.')
        if reason:
            raise ProviderError(f'{CLI_TOOLS[provider][2]} stopped this turn: {reason}')
        raise ProviderError(f'{CLI_TOOLS[provider][2]} could not complete this turn (exit code {code}). Run it once in a terminal to see why; a sign-in problem shows there.')
    usage = payload.get('usage') or {}
    return AgentResult(payload.get('result') or '', usage.get('input_tokens'), usage.get('output_tokens'), session_id=payload.get('session_id'))

def read_gemini(out, err, code, provider):
    """Gemini prints one JSON object with the reply under `response`; anything else is a failure."""
    start, end = out.find('{'), out.rfind('}')
    payload = None
    if start >= 0 and end > start:
        try:
            payload = json.loads(out[start:end+1])
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict) or not (payload.get('response') or '').strip():
        if is_exhausted(out, err):
            raise exhausted_error(provider)
        if code != 0:
            raise cli_exit_error(provider, code, out, err)
        raise ProviderError(f'{CLI_TOOLS[provider][2]} returned no message. Run `gemini` once in a terminal to confirm it is signed in.')
    tokens = json.dumps(payload.get('stats') or {})
    def total(*names):
        found = [int(v) for name in names for v in re.findall(rf'"{name}"\s*:\s*(\d+)', tokens)]
        return sum(found) or None
    return AgentResult(payload['response'], total('input_tokens', 'prompt_tokens', 'prompt'), total('output_tokens', 'candidates_tokens', 'candidates'))

def read_opencode(out, err, provider):
    """Newline-delimited events: the reply is every text part, usage the step totals."""
    text, input_tokens, output_tokens, failure = '', 0, 0, ''
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get('type') == 'error':
            failure = json.dumps(event.get('error') or {})  # OpenCode reports a refusal as an event, not an exit code.
            continue
        part = event.get('part') or {}
        if part.get('type') == 'text':
            text += part.get('text') or ''
        tokens = part.get('tokens') or {}
        input_tokens += tokens.get('input') or 0
        output_tokens += tokens.get('output') or 0
    if not text.strip():
        if is_exhausted(failure, err):
            raise exhausted_error(provider)
        raise ProviderError(f'{CLI_TOOLS[provider][2]} returned no message. Confirm the subscription is signed in.')
    return AgentResult(text, input_tokens or None, output_tokens or None)

class ModelBroker:
    def __init__(self, store: Store):
        self.store = store
        # ponytail: in process, because a lost cooldown costs one two-second probe against a
        # subscription that turns out to still be spent. Move it to the store if that ever shows.
        self.cooldowns = {}
        self.confined = 0  # Turns running under the file sandbox; while any do, the API checks its callers' integrity.

    def accounts_for(self, tenant_id, config):
        """Every connected subscription that can take this turn, the selected one first.

        A second subscription is a second model row on the same provider and model name, so
        switching between them needs no separate concept. One inside its cooldown goes last
        rather than missing, so a turn never fails for want of a login that only might be spent.
        """
        siblings = [m for m in self.store.list(tenant_id, 'models')
                    if m['id'] != config['id'] and m['provider'] == config['provider'] and m['model_name'] == config['model_name']]
        ordered = [config] + sorted(siblings, key=lambda m: m.get('created_at') or '')
        return sorted(ordered, key=lambda m: cooling(self, tenant_id, m['id']))

    async def invoke_agent(self, tenant_id, config, prompt, mode, root, extras=None):
        """Take one turn, trying each connected subscription until one still has usage left."""
        candidates = self.accounts_for(tenant_id, config)
        for index, candidate in enumerate(candidates):
            try:
                result = await self.run_agent(tenant_id, candidate, prompt, mode, root, extras)
            except ProviderError as exc:
                if exc.exhausted:
                    # Rested even when it is the last login, so Adaptive and the fallback stop picking a spent agent first.
                    self.cooldowns[(tenant_id, candidate['id'])] = time.monotonic() + COOLDOWN_SECONDS
                if not exc.exhausted or index == len(candidates)-1:
                    raise
                continue
            self.cooldowns.pop((tenant_id, candidate['id']), None)
            result.served_by = candidate['id']
            return result

    async def run_agent(self, tenant_id, config, prompt, mode, root, extras=None):
        """One turn, inside Frontier's sandbox when the project asks for one: the tool at low integrity so it
        can write only the project (and its own settings), and its network through a filter."""
        from . import sandbox
        policy = (extras or {}).get('sandbox')
        if not policy or (not policy['files'] and policy['network'] == 'open'):
            return await self.run_tool(tenant_id, config, prompt, mode, root, extras)
        extras, added, net = dict(extras), {}, None
        # Codex under Read only keeps its own read-only sandbox, which is stricter than confining writes to the folder.
        if policy['files'] and not (config['provider'] == 'codex_cli' and mode == 'read'):
            login = next(iter(account_env(self.store, tenant_id, config).values()), None)
            added.update(await asyncio.to_thread(sandbox.prepare, self.store.directory, root, config['provider'], login))
            extras.update(argv_prefix=sandbox.launcher(), scratch_dir=added['TEMP'], outer_sandbox=True)
        if policy['network'] != 'open':
            net = sandbox.NetworkFilter(sandbox.PROVIDER_HOSTS.get(config['provider'], []) + sandbox.tool_hosts(extras.get('mcp_servers'))
                                        + (policy['allow'] if policy['network'] == 'allowlist' else []))
            added.update(sandbox.proxy_env(await net.start()))
        extras['sandbox_env'] = added
        self.confined += 1 if extras.get('outer_sandbox') else 0
        try:
            result = await self.run_tool(tenant_id, config, prompt, mode, root, extras)
        except ProviderError as exc:
            if net and net.blocked:
                raise ProviderError(f'{exc} The sandbox refused network access to {", ".join(net.blocked[:8])}.', exc.retryable, exc.exhausted) from None
            raise
        finally:
            self.confined -= 1 if extras.get('outer_sandbox') else 0
            if net:
                await net.stop()
        result.blocked = list(net.blocked) if net else []
        return result

    async def run_tool(self, tenant_id, config, prompt, mode, root, extras=None):
        """Launch one agent against the project folder and return what it said.

        The folder is the working directory, so the tool's own file and shell tools reach the
        project directly and nothing has to be applied afterwards. The scratch directory exists
        only for Codex, which writes its final message to a file rather than to its output.
        """
        provider = config['provider']
        launch = resolve_cli(provider)
        env = account_env(self.store, tenant_id, config)
        extras = dict(extras or {})
        env.update(extras.get('sandbox_env') or {})
        with TemporaryDirectory(prefix='frontier-turn-', dir=extras.get('scratch_dir')) as scratch:
            final = Path(scratch)/'final.txt'
            servers = extras.get('mcp_servers') or []
            if provider == 'claude_cli' and (servers or extras.get('approval')):
                config_path = Path(scratch)/'mcp.json'
                config_path.write_text(json.dumps(mcp_config_for_claude(servers, extras.get('approval'))), encoding='utf-8')
                extras['mcp_config'] = config_path
            if provider in ('codex_cli', 'opencode_cli') and extras.get('approval'):
                # The same server with only the task board: these tools cannot hold a turn open for a question.
                approval = extras['approval']
                servers = servers + [{'name': 'frontier', 'transport': 'stdio', 'trusted': True, 'command': approval['command'][0], 'args': approval['command'][1:],
                                      'env': {'FRONTIER_URL': approval['url'], 'FRONTIER_TOKEN': approval['token'], 'FRONTIER_TOOLS': 'board', **approval.get('env', {})}}]
                extras['mcp_servers'] = servers
            if provider == 'opencode_cli' and servers:
                env['OPENCODE_CONFIG_CONTENT'] = json.dumps(mcp_config_for_opencode(servers))
            argv = [*(extras.get('argv_prefix') or []), *agent_argv(provider, launch, config['model_name'], mode, root, final, extras)]
            try:
                async with asyncio.timeout(extras.get('timeout') or TURN_TIMEOUT):
                    code, out, err = await run_cli(argv, prompt, root, env)
                # A tool may echo the conversation; words like 'quota' in it must not read as the tool running out.
                err = strip_echo(err, prompt)
            except TimeoutError:
                limit = (extras.get('timeout') or TURN_TIMEOUT) // 60
                raise ProviderError(f'{CLI_TOOLS[provider][2]} was still working after {limit} minutes and was stopped. Anything it had already written to the folder is still there. A project whose checks run longer can raise the limit in Project settings.') from None
            try:
                if provider == 'claude_cli':
                    return read_claude(out, err, code, provider)
                if provider == 'gemini_cli':
                    return read_gemini(out, err, code, provider)
            except ProviderError:
                # The tool's stderr stays out of the conversation, but its tail goes to the local
                # backend log so a failed turn can be diagnosed on this machine.
                logging.getLogger('frontier.broker').warning('%s turn failed (exit %s). stderr tail: %s', CLI_TOOLS[provider][2], code, ' '.join((err or '')[-1500:].split()))
                raise
            if code != 0:
                raise cli_exit_error(provider, code, strip_echo(out, prompt), err)
            if provider == 'opencode_cli':
                return read_opencode(out, err, provider)
            text = final.read_text(encoding='utf-8') if final.is_file() else ''
            if not text.strip():
                if is_exhausted(strip_echo(out, prompt), err):
                    raise exhausted_error(provider)
                raise ProviderError(f'{CLI_TOOLS[provider][2]} returned no final message. Confirm the subscription is signed in.')
            # Codex reports only a combined token total, so usage stays unreported.
            return AgentResult(text, None, None)
