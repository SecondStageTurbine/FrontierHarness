"""Only this module launches an agent. No global credentials, no implicit environment routing.

A turn runs a locally installed agent command line tool, signed in with the user's own Claude,
ChatGPT or OpenCode subscription, so those credentials live outside this application and are
never seen by it. What the agent may do to the project is chosen per turn and translated here
into each tool's own spelling of the same three postures; nothing is silently escalated.

Several subscriptions can be connected to one provider. Each carries an account name whose
credential directory this module points the tool at, and a turn moves to the next connected
account when the selected one answers that its usage window is spent.
"""
from dataclasses import dataclass
import asyncio
import json
import os
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
}
# Each agent tool reads the subscription it is signed in as out of one directory. Pointing that
# variable at a directory per named account is the whole mechanism behind connecting more than
# one subscription to the same provider: nothing is copied, and no credential is read here.
ACCOUNT_HOME_VARS = {'claude_cli': ('CLAUDE_CONFIG_DIR',), 'codex_cli': ('CODEX_HOME',), 'opencode_cli': ('XDG_DATA_HOME',)}
SIGNIN_COMMANDS = {'claude_cli': 'claude', 'codex_cli': 'codex login', 'opencode_cli': 'opencode auth login'}
# A spent subscription is skipped for this long before it is tried again. ponytail: a fixed
# window, because the three CLIs do not report a reset time in any shared form. Parse the real
# reset if idling an hour ever costs more than the two seconds a premature retry costs.
COOLDOWN_SECONDS = 3600
# An agentic turn reads, edits and runs checks, so it is bounded in minutes rather than seconds.
TURN_TIMEOUT = 1800
# The only CLI output this module reads rather than discards. A subscription with nothing left
# is a routine condition, not a broken install, and telling the two apart is what lets a turn
# move to the next login instead of stopping. Matched text is classified and then dropped.
LIMIT_PHRASES = ('usage limit', 'rate limit', 'rate_limit', 'limit reached', 'quota', 'too many requests', 'out of credit', 'insufficient_quota')

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

def is_exhausted(*texts):
    return any(phrase in (text or '').lower() for text in texts for phrase in LIMIT_PHRASES)

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

def resolve_cli(provider):
    name, entry, label = CLI_TOOLS[provider]
    found = shutil.which(name)
    if found:
        suffix = Path(found).suffix.lower()
        # Windows cannot start a script shim directly, and an extensionless file there is one.
        shim = suffix in ('.cmd', '.bat', '.ps1') or (os.name == 'nt' and not suffix)
        if not shim:
            return [found]
    node = shutil.which('node')
    roots = ([Path(found).parent] if found else []) + ([Path(os.environ['APPDATA'])/'npm'] if os.environ.get('APPDATA') else [])
    for root in roots:
        for script in ([root/entry] if entry.endswith('.js') else [root/(entry+'.exe'), root/entry]):
            if not script.is_file():
                continue
            if not entry.endswith('.js'):
                return [str(script)]  # A packaged binary starts itself; Node is not involved.
            if node:
                return [node, str(script)]
            raise ProviderError(f'{label} is installed through npm, so Node.js is required to run it, and node was not found on the path.')
    raise ProviderError(f'The {label} command line tool was not found. Install it, sign in to your subscription, then select it again.')

def agent_argv(provider, launch, model_name, mode, root, final_path):
    """One posture, three spellings. This is the only place the three tools differ on power."""
    if provider == 'claude_cli':
        if mode == 'read':
            # Not `plan` mode: that makes Claude act as a planner — it writes a plan file into
            # its own home and answers about that file — which is a side effect outside the
            # project and the wrong voice for a question. Read only is the ordinary agent with
            # its writing and shell tools removed, refusing anything else rather than asking.
            return [*launch, '-p', '--output-format', 'json', '--model', model_name, '--permission-mode', 'dontAsk',
                    '--disallowedTools', 'Bash', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit']
        return [*launch, '-p', '--output-format', 'json', '--model', model_name,
                '--permission-mode', {'edit': 'acceptEdits', 'auto': 'bypassPermissions'}[mode]]
    if provider == 'codex_cli':
        # `-a` is a global flag and must precede the subcommand. Approvals are never waited on:
        # exec has no one to ask, so an unanswerable prompt would hang the turn to its timeout.
        argv = [*launch, '-a', 'never', 'exec', '--skip-git-repo-check', '--model', model_name,
                '-C', str(root), '--output-last-message', str(final_path)]
        argv += (['--dangerously-bypass-approvals-and-sandbox'] if mode == 'auto'
                 else ['--sandbox', 'read-only' if mode == 'read' else 'workspace-write'])
        return argv + ['-']
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
        raise ProviderError(f'{CLI_TOOLS[provider][2]} could not complete this turn. Run it once in a terminal to confirm the subscription is signed in.')
    usage = payload.get('usage') or {}
    return AgentResult(payload.get('result') or '', usage.get('input_tokens'), usage.get('output_tokens'))

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

    def accounts_for(self, tenant_id, config):
        """Every connected subscription that can take this turn, the selected one first.

        A second subscription is a second model row on the same provider and model name, so
        switching between them needs no separate concept. One inside its cooldown goes last
        rather than missing, so a turn never fails for want of a login that only might be spent.
        """
        siblings = [m for m in self.store.list(tenant_id, 'models')
                    if m['id'] != config['id'] and m['provider'] == config['provider'] and m['model_name'] == config['model_name']]
        ordered = [config] + sorted(siblings, key=lambda m: m.get('created_at') or '')
        return sorted(ordered, key=lambda m: self.cooldowns.get((tenant_id, m['id']), 0) > time.monotonic())

    async def invoke_agent(self, tenant_id, config, prompt, mode, root):
        """Take one turn, trying each connected subscription until one still has usage left."""
        candidates = self.accounts_for(tenant_id, config)
        for index, candidate in enumerate(candidates):
            try:
                result = await self.run_agent(tenant_id, candidate, prompt, mode, root)
            except ProviderError as exc:
                if not exc.exhausted or index == len(candidates)-1:
                    raise
                self.cooldowns[(tenant_id, candidate['id'])] = time.monotonic() + COOLDOWN_SECONDS
                continue
            self.cooldowns.pop((tenant_id, candidate['id']), None)
            result.served_by = candidate['id']
            return result

    async def run_agent(self, tenant_id, config, prompt, mode, root):
        """Launch one agent against the project folder and return what it said.

        The folder is the working directory, so the tool's own file and shell tools reach the
        project directly and nothing has to be applied afterwards. The scratch directory exists
        only for Codex, which writes its final message to a file rather than to its output.
        """
        provider = config['provider']
        launch = resolve_cli(provider)
        env = account_env(self.store, tenant_id, config)
        with TemporaryDirectory(prefix='frontier-turn-') as scratch:
            final = Path(scratch)/'final.txt'
            argv = agent_argv(provider, launch, config['model_name'], mode, root, final)
            try:
                async with asyncio.timeout(TURN_TIMEOUT):
                    code, out, err = await run_cli(argv, prompt, root, env)
            except TimeoutError:
                raise ProviderError(f'{CLI_TOOLS[provider][2]} was still working after {TURN_TIMEOUT // 60} minutes and was stopped. Anything it had already written to the folder is still there.') from None
            if provider == 'claude_cli':
                return read_claude(out, err, code, provider)
            if code != 0:
                raise cli_exit_error(provider, code, out, err)
            if provider == 'opencode_cli':
                return read_opencode(out, err, provider)
            text = final.read_text(encoding='utf-8') if final.is_file() else ''
            if not text.strip():
                if is_exhausted(out, err):
                    raise exhausted_error(provider)
                raise ProviderError(f'{CLI_TOOLS[provider][2]} returned no final message. Confirm the subscription is signed in.')
            # Codex reports only a combined token total, so usage stays unreported.
            return AgentResult(text, None, None)
