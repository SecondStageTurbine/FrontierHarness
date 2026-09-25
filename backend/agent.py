"""One message, one agentic turn, inside the project folder.

The agent running a turn can change between any two turns, so nothing that matters is allowed
to live inside a command line tool's own session store: the three tools do not share one, and
a Claude session cannot be handed to Codex. The messages recorded here are the conversation,
and every turn replays them into whichever agent is selected. That is what makes a switch
lossless: the agent taking over reads the same conversation, in the same project folder, that
the previous one left behind. The folder is the other half of the handover — an agent is told
to read it rather than to trust a summary of what another agent did to it.

With Adaptive selected, the agent is chosen at the start of the turn by `adaptive`, and if the
attempt fails or the agent says it cannot, a stronger one continues the same turn with a
handoff. A manual choice is never second-guessed.
"""
import asyncio
import logging
import os
import re
import secrets
import sys
from pathlib import Path

from . import adaptive, benchmark, board, gitops, localhealth, push, sandbox, team as teamwork
from .adaptive import ADAPTIVE, MAX_ESCALATIONS
from .broker import CLI_TOOLS, ProviderError, cooling
from .projects import ProjectFiles
from .store import TenantIsolationViolationException, now, uid

# What the agent may do to the project this turn. The names are ours; each tool spells the
# same three postures differently, and broker.agent_argv is the only place that translates.
MODES = ('read', 'edit', 'auto')
# Oldest turns are dropped rather than refusing to continue: a conversation that stops being
# answerable is worse than one that has forgotten its beginning, and the model is told.
TRANSCRIPT_LIMIT = 120_000
# The shortest time an agent gets for one turn, in any project. Engine builds and test gates run long.
MIN_TURN_MINUTES = 90
HANDOVER = (
    'You are continuing a conversation that a different agent was handling. The conversation so '
    'far follows. Your working directory is the project it concerns and is the shared state '
    'between you: read what is actually there rather than assuming what the previous agent left. '
    'Answer the final USER message.'
)
CONTINUING = 'The conversation so far follows. Answer the final USER message.'
# The first reply names the conversation. One line the harness strips, instead of a second call.
TITLE_ASK = ('On the very first line of your reply write "Title: " followed by a title for this conversation '
             'of at most six words, then continue your actual reply on the next line.')
TITLE_LINE = re.compile(r'^\s*\**\s*Title:\s*\**\s*(.+?)\s*\**\s*$', re.IGNORECASE)
COMPACT_ASK = ('Write a handoff summary of the conversation below for an agent that will continue it without seeing '
               'the original messages: the objective, what was decided, what was done and which files changed, what '
               'is still open, and anything the user asked to keep in mind. Plain prose or short bullets, under 600 '
               'words. Reply with the summary only; do not run commands or change files.')
# Two agents, asked to reinstall Frontier, stopped its processes to free the executable and
# ended their own turn with it, before the installer ran. The host is named so the agent knows.
HOST = ('You are running inside Frontier, a desktop application; this turn is one of its subprocesses. '
        'Stopping, killing, reinstalling or updating Frontier ends this turn before anything after it runs. '
        'If asked to install or update Frontier, build it and tell the user to run the installer themselves. '
        'Your turn ends the moment you give your final reply, and you cannot send anything afterwards: there is no '
        '"I will report back". Anything you start in the background, a build, a test run, an editor in batch mode, is '
        'stopped when your turn ends. Run long commands to completion inside this turn and report their actual result; '
        'if something truly cannot finish in this turn, say plainly that it did not finish and what the user should check.')
# Verified against Codex 0.155: workspace-write keeps every .git directory read-only whatever
# writable_roots says, and its restricted token cannot reach the credential store a push needs.
# Claude's acceptEdits has no one to approve a shell command. So the agent is told, rather than
# discovering it and telling the user to run git by hand.
POSTURE = {
    'read': 'This turn is Read only: you may not change files or run commands.',
    'edit': ('This turn is Edit files: you may change files in the project, but git commit and push '
             'are not possible — the .git directory and the network are off limits under this posture. '
             'If the task needs a commit or push, do the file work, then say the user should resend '
             'that part under Full auto. Do not ask the user to run git by hand.'),
    'auto': 'This turn is Full auto: you may edit, run commands, commit, and push as the task needs.',
}


def agentic_providers(models):
    """Only a local agent command line tool can take a turn.

    An API key reaches a model, not an agent: there is no tool loop, no file access and no
    shell behind it. Those models stay configured and useful for dictation and for Adaptive's
    classifier, and are simply not offered as something that can run a turn.
    """
    return [m for m in models if m['provider'] in CLI_TOOLS and m.get('enabled', True) is not False]


def transcript(messages):
    """The conversation in the portable form every agent can read.

    Tool calls and file reads from a previous turn are deliberately absent: they belong to the
    tool that made them, and the project folder already holds their result.
    """
    lines = []
    for message in messages:
        text = (message.get('content') or '').strip()
        if not text:
            continue
        if message['role'] == 'user':
            lines.append('USER:\n' + text)
        else:
            lines.append(f'ASSISTANT ({message.get("model_name") or "earlier agent"}):\n{text}')
    return lines


def browser_server(project, token=None):
    """A real browser the agent can open, click and read, through Playwright's MCP server.

    Screenshots land in .frontier/browser in the project, where the Preview panel shows them. By default a
    fresh browser, headed when the user asked to watch and otherwise headless. With "my own browser", it is
    the Chrome or Edge the user already has open, reached through the Playwright extension, with their tabs
    and sign-ins; the extension's token, when given, connects without the user approving each time.
    On Windows npx is a .cmd, so it goes through cmd. Its tools run unasked in every tool, Codex included.
    """
    output = str(Path(project['root'])/'.frontier'/'browser')
    if project.get('agent_browser_mode') == 'mine':
        args = ['-y', '@playwright/mcp@latest', '--extension', '--browser', project.get('agent_browser_channel') or 'chrome', '--output-dir', output]
        command, args = (('cmd', ['/c', 'npx', *args]) if os.name == 'nt' else ('npx', args))
        return {'name': 'frontier-browser', 'transport': 'stdio', 'command': command, 'args': args, 'trusted': True, 'enabled': True,
                'env': {'PLAYWRIGHT_MCP_EXTENSION_TOKEN': token} if token else {}}
    args = ['-y', '@playwright/mcp@latest', '--isolated', '--output-dir', output] + ([] if project.get('agent_browser_visible') else ['--headless'])
    if os.name == 'nt':
        # Playwright wants Chrome by default; Edge ships with every Windows 10 and 11, so it is named outright.
        edge = next((str(p) for p in (Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe',
                                      Path(os.environ.get('PROGRAMFILES', r'C:\Program Files'))/'Microsoft/Edge/Application/msedge.exe',
                                      Path(os.environ.get('LOCALAPPDATA', ''))/'Microsoft/Edge/Application/msedge.exe') if p.is_file()), None)
        args += ['--browser', 'msedge'] + (['--executable-path', edge] if edge else [])
    command, args = (('cmd', ['/c', 'npx', *args]) if os.name == 'nt' else ('npx', args))
    return {'name': 'frontier-browser', 'transport': 'stdio', 'command': command, 'args': args, 'env': {}, 'trusted': True, 'enabled': True}


def sandbox_note(policy):
    """What the agent is told about Frontier's sandbox, so a refusal is reported rather than worked around."""
    parts = []
    if policy['files']:
        parts.append('You can change files only inside the project folder; anything outside it is read only, and writing there fails with access denied.')
    if policy['network'] == 'agent':
        parts.append('Network access is limited to your own AI service; package installs and other downloads are refused by Frontier\'s sandbox (HTTP 403).')
    elif policy['network'] == 'allowlist':
        parts.append('Network access is limited to your own AI service and these hosts: ' + (', '.join(policy['allow']) or 'none') + '. Anything else is refused by Frontier\'s sandbox (HTTP 403).')
    if not parts:
        return None
    return 'This turn runs in Frontier\'s sandbox, set by the user. ' + ' '.join(parts) + ' If the sandbox stops something the task needs, say so plainly instead of working around it.'


def browser_note(project, root):
    if not project.get('agent_browser'):
        return None
    from . import devserver
    server = devserver.get(root)
    where = f' The project\'s dev server is running at http://localhost:{server.port}/.' if server and server.status()['running'] and server.port else ''
    if project.get('agent_browser_mode') == 'mine':
        return ("The user's own browser, with their open tabs and the sites they are signed in to, is available through the "
                'frontier-browser tools (list and select tabs, navigate, click, type, read the page, console messages, screenshots).'
                + where + ' Use these tools for any web page, not another browser or computer-use tool. It is their real browser: '
                'work in the tab you need, open new tabs rather than navigating away from theirs, do not close their tabs or '
                'change their settings, and do only what the task asks on sites where they are signed in.')
    return ('A real browser is available through the frontier-browser tools (navigate, click, type, read the page, console '
            'messages, screenshots).' + where + ' Use these tools for any web page, not another browser or computer-use tool. '
            'After changing anything a user sees, open it, check the console for errors, '
            'take a screenshot, and report what you saw, not what you expect.')


def resumable(session, config, root, project, message):
    """The tool session to resume natively this turn, if the same tool that owns it is answering
    in the project folder itself (a worktree is another folder, which the tool's session does not know)."""
    native = session.get('native')
    if not native or message.get('team') or native['provider'] != config.get('provider'):
        return None
    try:
        same = Path(root).resolve() == Path(project['root']).resolve()
    except OSError:
        same = False
    return native if same else None


def conversation(session):
    """The messages an agent is sent, and the summary standing in for the ones compacted away."""
    messages = session.get('messages') or []
    summary = session.get('summary')
    if summary:
        index = next((i for i, m in enumerate(messages) if m['id'] == summary['through']), -1)
        return messages[index+1:], summary['text']
    return messages, None


def context_usage(session):
    """How full the transcript is against the limit, as the composer shows it."""
    messages, summary = conversation(session)
    lines = transcript(messages)
    if summary:
        lines.insert(0, summary)
    chars = len('\n\n'.join(lines))
    dropped = 0
    while len('\n\n'.join(lines)) > TRANSCRIPT_LIMIT and len(lines) > 1:
        lines.pop(0)
        dropped += 1
    compacted = (session.get('summary') or {}).get('count', 0)
    return {'chars': chars, 'limit': TRANSCRIPT_LIMIT, 'dropped': dropped, 'compacted': compacted}


def build_prompt(messages, switched, handoff=None, mode=None, summary=None, first=False, rules=None, memory=None, environment=None, board=None):
    """Instructions, then as much of the conversation as fits, ending at the new message.

    A handoff, when present, is what a previous agent left behind on this same turn; it goes
    ahead of the conversation so the next agent continues rather than starts over. A summary
    stands in for messages that were compacted away.
    """
    lines = transcript(messages)
    dropped = 0
    while len('\n\n'.join(lines)) > TRANSCRIPT_LIMIT and len(lines) > 1:
        lines.pop(0)
        dropped += 1
    head = (HANDOVER if switched else CONTINUING) + ' ' + HOST
    if mode in POSTURE:
        head += ' ' + POSTURE[mode]
    if first:
        head += ' ' + TITLE_ASK
    if dropped:
        head += f' The first {dropped} messages of this conversation were dropped to fit; say so if one is needed.'
    if handoff:
        head += '\n\n' + handoff
    if rules and rules.strip():
        head += '\n\nWORKSPACE RULES (set by the user; follow them in every turn):\n' + rules.strip()[:8000]
    if memory and memory.strip():
        head += '\n\nPROJECT NOTES (kept by the user and earlier conversations about this project):\n' + memory.strip()[:20000]
    if environment:
        head += '\n\nPROJECT ENVIRONMENT: ' + environment
    if board:
        head += ('\n\nPROJECT TASK BOARD (open tasks shared by every session in this project). When this turn finishes, '
                 'advances or blocks one of them, update it with the frontier board_update tool if you have it; otherwise '
                 'say in your reply which task changed. Add a task with board_add only for real follow-up work the user '
                 'should see, not for steps of this turn.\n' + board)
    if summary:
        head += '\n\nEarlier messages of this conversation were compacted into this summary:\n' + summary
    return head + '\n\n' + '\n\n'.join(lines)


def take_title(text):
    """The title line an agent was asked for, and the reply without it."""
    if not text:
        return None, text
    first, _, rest = text.lstrip().partition('\n')
    found = TITLE_LINE.match(first)
    if not found:
        return None, text
    return found.group(1).strip().strip('"\'')[:80] or None, rest.lstrip('\n')


def fingerprint(files, tenant_id, project_id, session_id=None):
    """Every readable file's hash and text, so a turn's edits can be shown afterwards.

    An agent writes to the folder itself, so this before-and-after is the only record of what
    it changed. Git is not consulted, because a project folder need not be a repository.

    ponytail: reads the whole project twice per turn, bounded by the walker's file cap and the
    reader's size limit. Switch to a git diff where the folder is a repository if it ever shows.
    """
    entries, _ = files.walk(tenant_id, project_id, session_id)
    root = files.resolved_root(tenant_id, project_id, session_id)  # Once, not once per file.
    captured = {}
    for entry in entries:
        try:
            item = files.read(tenant_id, project_id, entry['path'], session_id, root=root)
        except ValueError:
            continue  # Unreadable before is unreadable after; it cannot produce a diff either way.
        captured[entry['path']] = (item['hash'], item['content'])
    return captured


def changes_between(before, after):
    changes = []
    for path, (digest, text) in sorted(after.items()):
        previous = before.get(path)
        if previous is None:
            changes.append({'id': uid(), 'path': path, 'status': 'added', 'before': None, 'after': text})
        elif previous[0] != digest:
            changes.append({'id': uid(), 'path': path, 'status': 'modified', 'before': previous[1], 'after': text})
    for path, (_, text) in sorted(before.items()):
        if path not in after:
            changes.append({'id': uid(), 'path': path, 'status': 'removed', 'before': text, 'after': None})
    return changes


class AgentRunner:
    def __init__(self, store, broker=None):
        from .broker import ModelBroker
        self.store = store
        self.broker = broker or ModelBroker(store)
        self.files = ProjectFiles(store)
        self.turns: dict[tuple[str, str], asyncio.Task] = {}
        # Permission requests from a running Claude turn, and the per-turn tokens that let the
        # approval tool speak for exactly one turn. Both are ephemeral: a restart ends the turn.
        self.turn_tokens: dict[str, tuple] = {}
        self.approvals: dict[str, dict] = {}

    def protections(self, tenant_id, project_id):
        """The project's own limits, for agent calls outside a conversation turn (commit messages, pull requests,
        summaries, the team lead): its sandbox and keeping .env files from Claude, whatever the call is for."""
        project = self.store.get(tenant_id, 'projects', project_id)
        found = {'protect_env': bool(project.get('protect_env', True))}
        confined = sandbox.policy(project)
        if confined['files'] or confined['network'] != 'open':
            found['sandbox'] = confined
        return found

    def undo_changes(self, tenant_id, project_id, session_id, changes):
        """Write back each change's recorded before-text, deleting files the turn added. The fallback when there is no checkpoint."""
        restored = []
        for change in changes:
            target = self.files.resolve(tenant_id, project_id, change['path'], session_id)
            if change['status'] == 'added':
                if target.is_file():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(change['before'] or '', encoding='utf-8')
            restored.append(change['path'])
        return restored

    def ensure_idle(self, tenant_id, session_id):
        if self.busy(tenant_id, session_id):
            raise ValueError('This conversation is still working. Wait for it to finish or stop it.')

    def active_sessions(self, tenant_id):
        """The sessions of this workspace with a turn running right now."""
        return {sid for (tid, sid), task in list(self.turns.items()) if tid == tenant_id and not task.done()}

    def busy(self, tenant_id, session_id):
        task = self.turns.get((tenant_id, session_id))
        return bool(task and not task.done())

    def fallback(self, tenant_id, tried):
        """An agent to continue a turn whose agent ran out of usage: connected, enabled, online as far as we know, not yet tried, and not cooling down."""
        for agent in self.agents(tenant_id):
            if agent['id'] in tried or agent.get('enabled') is False or agent.get('online') is False:
                continue
            if cooling(self.broker, tenant_id, agent['id']):
                continue
            return agent
        return None

    def busy_anywhere(self, tenant_id, project_id):
        """Whether any conversation of this project has a turn running."""
        for (tid, session_id), task in list(self.turns.items()):
            if tid == tenant_id and not task.done():
                try:
                    if self.store.get(tenant_id, 'sessions', session_id)['project_id'] == project_id:
                        return True
                except TenantIsolationViolationException:
                    continue
        return False

    def extras(self, tenant_id, token, mode, project=None):
        """What this turn carries beyond its posture: the workspace's MCP servers, whether the
        project's .env files are off limits, and, when the backend is reachable over loopback, the
        approval tool that turns a prompt into a card."""
        servers = [s for s in self.store.list(tenant_id, 'mcp_servers') if s.get('enabled', True)]
        extras = {'mcp_servers': servers, 'protect_env': bool((project or {}).get('protect_env', True))}
        confined = sandbox.policy(project)
        if confined['files'] or confined['network'] != 'open':
            extras['sandbox'] = confined
        if (project or {}).get('agent_browser') and not any(s['name'] == 'frontier-browser' for s in servers):
            sealed = (project or {}).get('agent_browser_token')  # Not `token`: that name is this turn's own token.
            # The project's own browser replaces a plain Playwright server added to the workspace: two browsers let an
            # agent pick the wrong one (a fresh one instead of the user's own, or the reverse).
            plain = [s for s in servers if '@playwright/mcp' not in ' '.join([s.get('command') or '', *(s.get('args') or [])])]
            extras['mcp_servers'] = plain + [browser_server(project, self.store.decrypt(sealed) if sealed else None)]
        port = os.environ.get('HARNESS_DESKTOP_PORT')
        if port:
            # Frontier's own tools: under Edit files the approval card; in every posture the question
            # card (Claude) and the project's task board.
            if getattr(sys, 'frozen', False):
                command, env = [sys.executable, '--permission-tool'], {}
            else:
                command, env = [sys.executable, '-m', 'backend.desktop', '--permission-tool'], {'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
            extras['approval'] = {'url': f'http://127.0.0.1:{port}', 'token': token, 'command': command, 'env': env}
        return extras

    def notify(self, tenant_id, project_id, session, message, event, text):
        """A push to the user's phone, when they set one up: a turn finished, stopped, or is waiting on them.
        Team workers stay quiet; their lead's turn speaks for them."""
        if session.get('team_parent'):
            return
        try:
            directory = self.store.directory
            project = self.store.get(tenant_id, 'projects', project_id)
            agent = message.get('model_name') or 'The agent'
            title = {'done': f'{agent} finished in {project["name"]}', 'failed': f'{agent} stopped in {project["name"]}',
                     'waiting': f'{agent} needs you in {project["name"]}'}[event]
            body = ' '.join(text.split())[:300] if push.config(directory)['details'] and text else session.get('name') or ''
            push.send(directory, event, title, body, lambda: push.link(directory, tenant_id, project_id, session['id']))
        except Exception:
            pass  # A notification is a courtesy; it never disturbs the turn.

    def request_approval(self, token, tool_name, tool_input, tool_use_id=None, kind='permission'):
        """A running turn's tool asks; the conversation shows a card until someone answers."""
        turn = self.turn_tokens.get(token)
        if not turn:
            raise ValueError('This turn is not running.')
        tenant_id, project_id, session_id, message_id = turn
        approval = {'id': uid(), 'tenant_id': tenant_id, 'session_id': session_id, 'message_id': message_id, 'tool_name': tool_name,
                    'input': tool_input, 'tool_use_id': tool_use_id, 'created_at': now(), 'decision': None, 'message': None,
                    'event': asyncio.Event(), 'kind': kind}
        self.approvals[approval['id']] = approval
        text = f'The agent asks: {str(tool_input.get("question") or "")[:200]}' if kind == 'question' else f'{tool_name} needs your permission.'
        self.store.event(tenant_id, session_id, 'approval.requested', text, message_id=message_id, approval_id=approval['id'])
        session = self.store.get(tenant_id, 'sessions', session_id)
        message = next((m for m in session['messages'] if m['id'] == message_id), {})
        self.notify(tenant_id, project_id, session, message, 'waiting', str(tool_input.get('question') or '') if kind == 'question' else f'{tool_name} needs your permission.')
        return approval['id']

    async def approval_state(self, approval_id, wait=0):
        approval = self.approvals.get(approval_id)
        if not approval:
            return {'decision': 'deny', 'message': 'This request is no longer open.'}
        if wait and approval['decision'] is None:
            try:
                await asyncio.wait_for(approval['event'].wait(), min(float(wait), 30))
            except TimeoutError:
                pass
        return {'decision': approval['decision'], 'message': approval['message']}

    def decide(self, tenant_id, session_id, approval_id, allow, message=None):
        approval = self.approvals.get(approval_id)
        if not approval or approval['tenant_id'] != tenant_id or approval['session_id'] != session_id:
            raise TenantIsolationViolationException('This request is unavailable in the current workspace.')
        if approval['decision'] is not None:
            raise ValueError('This request was already answered.')
        approval.update(decision='allow' if allow else 'deny', message=message, decided_at=now())
        approval['event'].set()
        self.store.event(tenant_id, session_id, 'approval.decided', f'{approval["tool_name"]} {"allowed" if allow else "denied"}.',
                         message_id=approval['message_id'], approval_id=approval_id)

    def pending(self, tenant_id, session_id):
        return [{k: v for k, v in a.items() if k != 'event'} for a in self.approvals.values()
                if a['tenant_id'] == tenant_id and a['session_id'] == session_id and a['decision'] is None]

    def abandon_approvals(self, session_id):
        """When a turn ends, whatever it was still asking is answered no and forgotten."""
        for approval_id, approval in list(self.approvals.items()):
            if approval['session_id'] == session_id:
                if approval['decision'] is None:
                    approval.update(decision='deny', message='The turn ended before this was answered.')
                    approval['event'].set()
                self.approvals.pop(approval_id, None)

    def select(self, tenant_id, model_id):
        """The agent asked for, or a readable reason it cannot take a turn."""
        config = self.store.get(tenant_id, 'models', model_id)
        if config['provider'] not in CLI_TOOLS:
            raise ValueError('That model is reached through an API key, which has no tools, no file access and no shell. Choose a Claude, Codex or OpenCode agent to run a turn.')
        return config

    def agents(self, tenant_id):
        """Every agent that can take a turn, each with its track record so routing weighs what it has done."""
        records = {r['id']: r for r in self.store.list(tenant_id, adaptive.TRACK_KIND)}
        board = benchmark.table(self.store.directory)
        return [{**a, 'track': adaptive.track_for(a, records), 'benchmark': benchmark.match(a, board)} for a in agentic_providers(self.store.list(tenant_id, 'models'))]

    def continue_team(self, tenant_id, project_id, session_id):
        """Pick a stopped team back up: the same lead, posture and plan, with only unfinished tasks run again."""
        session = self.store.get(tenant_id, 'sessions', session_id)
        last = session['messages'][-1] if session['messages'] else {}
        if not last.get('team', {}).get('tasks') or last.get('status') not in ('cancelled', 'failed'):
            raise ValueError('Only a team that stopped before finishing can be continued.')
        # A team from before 0.20.8 did not record its request; it is the message that started it.
        asked = next((m['content'] for m in reversed(session['messages']) if m['role'] == 'user'), '')
        return self.send(tenant_id, project_id, session_id, 'Continue the team\u2019s work where it stopped.', last['model_id'], last['mode'],
                         team={'objective': asked, **last['team']})

    def send(self, tenant_id, project_id, session_id, content, model_id, mode, queue=False, team=False, context=None):
        """Record the message, start the turn, and answer immediately.

        The reply is written into a message that already exists and is marked running, so the
        conversation shows the turn in progress instead of appearing empty until it finishes.
        With Adaptive selected the agent is not yet known; it is chosen at the start of the turn
        and the message says so until then. While a turn is running, `queue` keeps the message
        for the moment it finishes instead of refusing it.
        """
        if mode not in MODES:
            raise ValueError('Choose read only, edit files, or full auto.')
        session = self.store.get(tenant_id, 'sessions', session_id)
        if self.busy(tenant_id, session_id):
            if not queue:
                raise ValueError('This conversation is still working. Wait for it to finish or stop it.')
            session.setdefault('queue', []).append({'id': uid(), 'content': content, 'model_id': model_id, 'mode': mode, 'created_at': now(), 'team': team, 'context': context or []})
            return self.store.put(tenant_id, 'sessions', session)
        project = self.store.get(tenant_id, 'projects', project_id)
        if not self.files.root(tenant_id, project_id, session_id).is_dir():
            raise FileNotFoundError('This project’s folder is not available. Reconnect the drive or open the project again.')
        adaptive_turn = model_id == ADAPTIVE
        if adaptive_turn:
            if team:
                raise ValueError('Team mode needs a specific agent as the lead. Pick one in the selector.')
            if not self.agents(tenant_id):
                raise ValueError('Adaptive needs at least one Claude, Codex or OpenCode agent connected.')
            config = None
        else:
            config = self.select(tenant_id, model_id)
        if team and mode == 'read':
            raise ValueError('A team needs Edit files or Full auto to do its work.')
        # The switch is decided against the last agent that actually answered, not against the
        # selector, so reselecting the same agent never costs a handover preamble. An Adaptive
        # turn decides this once it has chosen.
        previous = self.previous_agent(session, None)
        switched_from = previous['model_name'] if previous and not adaptive_turn and previous['model_id'] != model_id else None
        session['messages'].append({'id': uid(), 'role': 'user', 'content': content, 'created_at': now(), 'context': [c for c in (context or []) if c]})
        reply = {'id': uid(), 'role': 'assistant', 'content': '', 'status': 'running', 'error': None,
                 'model_id': None if adaptive_turn else model_id,
                 'model_name': 'Adaptive' if adaptive_turn else config['name'],
                 'provider': None if adaptive_turn else config['provider'],
                 'mode': mode, 'switched_from': switched_from,
                 'routing': {'mode': 'adaptive', 'status': 'choosing'} if adaptive_turn else {'mode': 'manual'},
                 'changes': [], 'input_tokens': None, 'output_tokens': None,
                 'created_at': now(), 'finished_at': None}
        if team:
            # `team` is True for a new team, or the plan of a stopped one to continue.
            reply['team'] = {**team, 'status': 'working'} if isinstance(team, dict) else {'status': 'planning', 'lead': config['name'], 'tasks': [], 'summary': ''}
        session['messages'].append(reply)
        if len(session['messages']) == 2 and session.get('name') in (None, '', 'New session'):
            session['name'] = content.splitlines()[0][:80]
            session['auto_named'] = True  # The first reply may still improve on this.
        session['updated_at'] = now()
        self.store.put(tenant_id, 'sessions', session)
        project.update(last_session_id=session_id, last_model_id=model_id, last_mode=mode)
        self.store.put(tenant_id, 'projects', project)
        self.store.event(tenant_id, session_id, 'turn.started',
                         ('Adaptive is choosing an agent' if adaptive_turn else f'{config["name"]} is working') + f' in {project["name"]}.',
                         message_id=reply['id'], mode=mode)
        key = (tenant_id, session_id)
        task = asyncio.create_task(self.run_turn(tenant_id, project_id, session_id, reply['id'], config, mode))
        self.turns[key] = task
        task.add_done_callback(lambda finished: self.turns.pop(key, None) if self.turns.get(key) is finished else None)
        task.add_done_callback(lambda finished: self.drain(tenant_id, project_id, session_id))
        return session

    def drain(self, tenant_id, project_id, session_id):
        """The next queued message takes its turn, unless the conversation was stopped or is busy again."""
        try:
            session = self.store.get(tenant_id, 'sessions', session_id)
        except Exception:
            return  # The conversation went away with its project; nothing left to run.
        queued = session.get('queue') or []
        if not queued or self.busy(tenant_id, session_id):
            return
        item, session['queue'] = queued[0], queued[1:]
        self.store.put(tenant_id, 'sessions', session)
        try:
            self.send(tenant_id, project_id, session_id, item['content'], item['model_id'], item['mode'], team=item.get('team', False), context=item.get('context'))
        except (ValueError, FileNotFoundError, TenantIsolationViolationException) as exc:
            self.store.event(tenant_id, session_id, 'queue.dropped', f'A queued message could not start: {exc}')

    def unqueue(self, tenant_id, session_id, item_id):
        session = self.store.get(tenant_id, 'sessions', session_id)
        session['queue'] = [q for q in session.get('queue') or [] if q['id'] != item_id]
        return self.store.put(tenant_id, 'sessions', session)

    def previous_agent(self, session, message_id):
        """The last agent that actually answered before this message, for the handover decision."""
        for message in reversed(session['messages']):
            if message['id'] == message_id:
                continue
            if message['role'] == 'assistant' and message.get('model_id'):
                return message
        return None

    async def route(self, tenant_id, project_id, session, message):
        """Choose an agent for an Adaptive turn and write the decision onto the message."""
        content = next(m['content'] for m in reversed(session['messages']) if m['role'] == 'user')
        tenant = self.store.tenant_internal(tenant_id)
        classifier = None
        if tenant.get('router_model_id'):
            try:
                classifier = self.store.get(tenant_id, 'models', tenant['router_model_id'])
            except Exception:
                classifier = None  # A classifier that was disconnected is not a reason to refuse a turn.
        # Folder scans read up to thousands of files; off the event loop, so the window stays responsive.
        repo = await asyncio.to_thread(adaptive.repository_signals, self.files, tenant_id, project_id)
        requirements, classified_by = await adaptive.classify(content, repo, classifier, self.store.decrypt)
        # A local model whose server is not running is not available, however good its profile:
        # several can be configured while only one holds the GPU, and a turn sent to a server
        # that is down would fail and escalate to a cloud agent for no reason.
        await benchmark.refresh(self.store.directory)
        agents = self.agents(tenant_id)
        online = await localhealth.availability(agents)
        offline = [m for m in agents if online.get(m['id']) is False]
        chosen, ranked = adaptive.choose(requirements, [m for m in agents if online.get(m['id']) is not False], lambda model_id: cooling(self.broker, tenant_id, model_id))
        if chosen is None:
            raise ProviderError('Adaptive found no agent that can take a turn.' + (' Every local agent is configured but none of their servers is running.' if offline else ' Connect a Claude, Codex or OpenCode agent.'))
        message['routing'] = {'mode': 'adaptive', 'status': 'chosen', 'classified_by': classified_by,
                              'requirements': requirements.model_dump(), 'repository': repo,
                              'chosen': {'id': chosen['id'], 'name': chosen['name'], 'because': chosen['because']},
                              'candidates': [{k: r[k] for k in ('id', 'name', 'cost_class', 'sufficient', 'deficit')} for r in ranked[:6]],
                              'offline': [m['name'] for m in offline],
                              'attempts': [], 'escalations': 0}
        return self.store.get(tenant_id, 'models', chosen['id']), ranked

    def save_turn(self, tenant_id, session_id, message_id, session_fields=None, **message_fields):
        """Save fields of the running message (and of the session) onto a fresh read, so whatever the user did
        meanwhile, a queued message, a rename, a pin, stays. The turn's own copy is updated to match."""
        fresh = self.store.get(tenant_id, 'sessions', session_id)
        target = next((m for m in fresh['messages'] if m['id'] == message_id), None)
        if target is not None:
            target.update(message_fields)
        fresh.update(session_fields or {})
        self.store.put(tenant_id, 'sessions', fresh)
        return fresh

    def bind(self, tenant_id, session_id, message_id, config, note):
        """Point the running message at the agent now taking it, and tell the stream."""
        session = self.store.get(tenant_id, 'sessions', session_id)
        message = next(m for m in session['messages'] if m['id'] == message_id)
        previous = self.previous_agent(session, message_id)
        message.update(model_id=config['id'], model_name=config['name'], provider=config['provider'],
                       switched_from=previous['model_name'] if previous and previous['model_id'] != config['id'] else None)
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'turn.agent', note, message_id=message_id)
        return session, message

    async def run_turn(self, tenant_id, project_id, session_id, message_id, config, mode):
        project = self.store.get(tenant_id, 'projects', project_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        message = next(m for m in session['messages'] if m['id'] == message_id)
        adaptive_turn = message['routing']['mode'] == 'adaptive'
        root = str(self.files.root(tenant_id, project_id, session_id))
        token = secrets.token_urlsafe(24)
        self.turn_tokens[token] = (tenant_id, project_id, session_id, message_id)
        extras = self.extras(tenant_id, token, mode, project)
        # Every project gets at least 90 minutes; a project may ask for more, never less.
        extras['timeout'] = max(MIN_TURN_MINUTES, int(project.get('turn_minutes') or 0)) * 60
        rules = (self.store.tenant_internal(tenant_id) or {}).get('rules')
        memory = project.get('memory')
        environment = ' '.join(filter(None, [(self.files.python_env(root) or {}).get('note'), browser_note(project, root)])) or None
        board_text = board.render(board.tasks(self.store, tenant_id, project_id))
        environment = ' '.join(filter(None, [environment, sandbox_note(sandbox.policy(project))])) or None
        # Read only cannot change the folder, so it is not read twice to prove that.
        # In a repository, the whole working tree is also checkpointed as hidden git objects, so a
        # turn can be put back exactly, binaries included, without touching the user's branch.
        before, before_ref = (await asyncio.gather(asyncio.to_thread(fingerprint, self.files, tenant_id, project_id, session_id),
                                                   self.checkpoint(root, f'Frontier: before turn {message_id}'))) if mode != 'read' else ({}, None)
        status, error, result = 'complete', None, None
        attempts, tried, handoff_text, ranked = [], set(), None, []
        try:
            if adaptive_turn:
                config, ranked = await self.route(tenant_id, project_id, session, message)
                self.save_turn(tenant_id, session_id, message_id, routing=message['routing'])
                session, message = self.bind(tenant_id, session_id, message_id, config,
                                             f'Adaptive chose {config["name"]}: {message["routing"]["chosen"]["because"]}.')
            elif (await localhealth.availability([config])).get(config['id']) is False:
                # Fail in a second with the address, rather than in a minute with the tool's error.
                raise ProviderError(f'{config["name"]} is not running: nothing at {localhealth.where(config)} is serving it. Start that server, or pick another agent.')
            if message.get('team'):
                # The lead plans and reviews under Read only; its workers take the real posture.
                # A continued team keeps its first checkpoint, so the lead reviews everything the team did.
                message['team']['checkpoint'] = message['team'].get('checkpoint') or before_ref
                session = self.save_turn(tenant_id, session_id, message_id, team=message['team'])
                message = next(m for m in session['messages'] if m['id'] == message_id)
                visible, summary = conversation(session)
                objective = message['team'].get('objective') or next(m['content'] for m in reversed(session['messages']) if m['role'] == 'user')
                lead_prompt = build_prompt(visible, False, None, 'read', summary, rules=rules, memory=memory, environment=environment, board=board_text)
                result = await teamwork.Team(self, tenant_id, project_id, session_id, message_id, config, mode, root).run(lead_prompt, objective)
            rounds = 0 if message.get('team') else 1 + (MAX_ESCALATIONS if adaptive_turn else 0)
            spare = 2  # How many times a spent agent may hand this turn to another agent.
            while rounds > 0:
                rounds -= 1
                tried.add(config['id'])
                visible, summary = conversation(session)
                prompt = build_prompt(visible, bool(message.get('switched_from')), handoff_text, mode, summary,
                                      first=sum(1 for m in session['messages'] if m['role'] == 'user') == 1, rules=rules, memory=memory, environment=environment, board=board_text)
                native = resumable(session, config, root, project, message)
                result, error = None, None
                try:
                    if native:
                        # The same tool takes this turn, so its own session continues with its full memory;
                        # only what was said since it last spoke is sent. A session it cannot find falls back to the replay.
                        fresh = [m for m in session['messages'][native['upto']:] if m['id'] != message_id]
                        try:
                            result = await self.broker.invoke_agent(tenant_id, config, build_prompt(fresh, False, handoff_text, mode, rules=rules, memory=memory, environment=environment, board=board_text),
                                                                    mode, root, {**extras, 'resume': native['id']})
                            result.resumed = True
                        except ProviderError as exc:
                            if exc.exhausted:
                                raise
                            session['native'] = None
                            self.save_turn(tenant_id, session_id, message_id, session_fields={'native': None})
                            self.store.event(tenant_id, session_id, 'turn.agent', f'{config["name"]} could not resume its own session ({exc}); the conversation is replayed instead.', message_id=message_id)
                    if result is None:
                        result = await self.broker.invoke_agent(tenant_id, config, prompt, mode, root, extras)
                except ProviderError as exc:
                    if not exc.exhausted:
                        error = str(exc)
                    else:
                        # Every login for this agent is spent. Another connected agent continues the same
                        # turn with a handoff, as an Adaptive escalation would; with none left, it fails.
                        following = self.fallback(tenant_id, tried) if spare else None
                        if following is None:
                            raise
                        spare -= 1
                        rounds += 1
                        attempts.append({'id': config['id'], 'name': config['name'], 'outcome': 'out of usage', 'reply': ''})
                        message['routing'].update(attempts=attempts, fallbacks=message['routing'].get('fallbacks', 0) + 1)
                        self.save_turn(tenant_id, session_id, message_id, routing=message['routing'])
                        handoff_text = adaptive.render_handoff(adaptive.handoff(session, message, attempts))
                        spent = config['name']
                        config = following
                        session, message = self.bind(tenant_id, session_id, message_id, config, f'{spent} is out of usage; {config["name"]} continues this turn.')
                        continue
                if not adaptive_turn:
                    break
                changed = changes_between(before, await asyncio.to_thread(fingerprint, self.files, tenant_id, project_id, session_id)) if mode != 'read' else []
                reason = adaptive.struggled(result.text if result else '', error, mode, changed)
                following = adaptive.stronger(ranked, tried) if reason else None
                attempts.append({'id': config['id'], 'name': config['name'], 'outcome': reason or 'completed',
                                 'reply': result.text if result else ''})
                if not reason or following is None:
                    break
                # Escalate: a stronger agent continues this same turn, told what happened so far.
                message['routing'].update(attempts=attempts, escalations=message['routing'].get('escalations', 0) + 1)
                message['changes'] = changed
                self.save_turn(tenant_id, session_id, message_id, routing=message['routing'], changes=changed)
                handoff_text = adaptive.render_handoff(adaptive.handoff(session, message, attempts))
                config = self.store.get(tenant_id, 'models', following['id'])
                session, message = self.bind(tenant_id, session_id, message_id, config, f'Escalating to {config["name"]}: {reason}.')
            if result is None and error:
                status = 'failed'
            for attempt in attempts if adaptive_turn else []:
                # An Adaptive turn that had to escalate counts against the agent that struggled.
                if attempt['outcome'] != 'out of usage' and any(m['id'] == attempt['id'] for m in self.store.list(tenant_id, 'models')):
                    adaptive.record_outcome(self.store, tenant_id, self.store.get(tenant_id, 'models', attempt['id']),
                                            **({'done': 1} if attempt['outcome'] == 'completed' else {'failed': 1}))
        except asyncio.CancelledError:
            status, error = 'cancelled', 'Stopped. Anything the agent had already written to the folder is still there.'
            raise
        except ProviderError as exc:
            status, error = 'failed', str(exc)
        except (OSError, ValueError) as exc:
            status, error = 'failed', str(exc)
        except Exception:
            # A turn runs detached, so an unexpected failure has nowhere else to surface. The
            # message has to close, or the conversation stays busy forever.
            logging.getLogger('frontier.agent').exception('A turn stopped unexpectedly')
            status, error = 'failed', 'The turn stopped unexpectedly. Anything the agent had already written to the folder is still there.'
        finally:
            self.turn_tokens.pop(token, None)
            self.abandon_approvals(session_id)
            if result is not None and status == 'complete':
                error = None
            after_ref = await self.checkpoint(root, f'Frontier: after turn {message_id}') if before_ref else None
            after_print = None
            if mode != 'read':
                try:
                    after_print = await asyncio.to_thread(fingerprint, self.files, tenant_id, project_id, session_id)
                except (OSError, ValueError, asyncio.CancelledError):
                    after_print = None
            self.finish(tenant_id, project_id, session_id, message_id, status, error, result, before, mode, attempts, after=after_print,
                        checkpoint={'before': before_ref, 'after': after_ref} if before_ref and after_ref else None)

    async def checkpoint(self, root, label):
        try:
            return await gitops.checkpoint(root, label)
        except (gitops.GitError, OSError, asyncio.CancelledError):
            return None  # A folder that is not a repository, or a git that cannot run, simply has no checkpoint.

    def finish(self, tenant_id, project_id, session_id, message_id, status, error, result, before, mode, attempts=(), checkpoint=None, after=None):
        session = self.store.get(tenant_id, 'sessions', session_id)
        message = next((m for m in session['messages'] if m['id'] == message_id), None)
        if message is None or message['status'] != 'running':
            return
        changes = []
        if mode != 'read':
            try:
                changes = changes_between(before, after if after is not None else fingerprint(self.files, tenant_id, project_id, session_id))
            except (OSError, ValueError):
                changes = []  # A folder that vanished mid-turn is already reported by the turn itself.
        title, text = take_title(result.text if result else '')
        if title and session.get('auto_named'):
            session['name'] = title
            session['auto_named'] = False
        message.update(status=status, error=error, changes=changes, finished_at=now(), checkpoint=checkpoint,
                       content=text or (error or ''),
                       input_tokens=result.input_tokens if result else None,
                       output_tokens=result.output_tokens if result else None,
                       cost=self.cost(tenant_id, (result.served_by if result else None) or message.get('model_id'), result))
        if attempts and message.get('routing'):
            message['routing']['attempts'] = [{k: v for k, v in a.items() if k != 'reply'} for a in attempts]
        if result and result.blocked:
            message['sandbox_blocked'] = result.blocked[:30]
        if session.get('native') and status == 'complete':
            # The tool's own session now holds this turn too; a reply from anyone else means it no longer
            # has the whole conversation, so later turns replay it.
            if result and result.resumed and not (result.served_by and result.served_by != message['model_id']):
                session['native'].update(id=result.session_id or session['native']['id'], upto=len(session['messages']))
            else:
                session['native'] = None
        if result and result.served_by and result.served_by != message['model_id']:
            # Another connected subscription answered because the selected one was spent. The
            # message names the agent that replied, or the conversation credits the wrong one.
            served = self.store.get(tenant_id, 'models', result.served_by)
            message.update(model_id=result.served_by, model_name=served['name'])
            self.store.event(tenant_id, session_id, 'account.switched',
                             f'{served["name"]} took over after the previous subscription ran out of usage.', message_id=message_id)
        session['updated_at'] = now()
        self.store.put(tenant_id, 'sessions', session)
        if status in ('complete', 'failed'):
            self.notify(tenant_id, project_id, session, message, 'done' if status == 'complete' else 'failed', text or error or '')
        summary = f'{len(changes)} file{"" if len(changes) == 1 else "s"} changed.' if changes else 'No files changed.'
        self.store.event(tenant_id, session_id, f'turn.{status}',
                         error or (summary if mode != 'read' else 'Answered without touching the folder.'),
                         message_id=message_id)

    async def rewind(self, tenant_id, project_id, session_id, message_id, restore_files=True):
        """Cut the conversation back to before one of the user's messages, and optionally put the
        folder back to how it was then, so that message can be edited and sent again."""
        self.ensure_idle(tenant_id, session_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        index = next((i for i, m in enumerate(session['messages']) if m['id'] == message_id and m['role'] == 'user'), None)
        if index is None:
            raise ValueError('Pick one of your own messages to edit from.')
        dropped = session['messages'][index:]
        restored = []
        if restore_files:
            root = self.files.root(tenant_id, project_id, session_id)
            replies = [m for m in dropped if m['role'] == 'assistant' and m.get('status') != 'running']
            first_checkpoint = next((m['checkpoint']['before'] for m in replies if m.get('checkpoint')), None)
            if first_checkpoint:
                restored = await gitops.rollback(root, first_checkpoint)
            else:
                for reply in reversed(replies):
                    restored += self.undo_changes(tenant_id, project_id, session_id, reply.get('changes') or [])
        session['messages'] = session['messages'][:index]
        session['queue'] = []
        if (session.get('native') or {}).get('upto', 0) > index:
            # The tool's own session still remembers the turns rewound away; later turns replay the transcript instead.
            session['native'] = None
        if session.get('summary') and not any(m['id'] == session['summary']['through'] for m in session['messages']):
            session['summary'] = None
        session['updated_at'] = now()
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'session.rewound', f'Rewound {len(dropped)} messages' + (f' and put back {len(set(restored))} files.' if restored else '.'))
        return {'session': session, 'content': dropped[0]['content'], 'restored': sorted(set(restored))}

    async def remember(self, tenant_id, project_id, session_id):
        """Ask the conversation's agent what this session taught about the project, and keep it on the project."""
        self.ensure_idle(tenant_id, session_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        project = self.store.get(tenant_id, 'projects', project_id)
        visible, summary = conversation(session)
        if len([m for m in visible if m.get('content')]) < 2 and not summary:
            raise ValueError('There is nothing to remember yet.')
        config = self.writer(tenant_id, session, project)
        prompt = ('From the conversation below, list 3 to 8 facts about this project worth remembering in future conversations: '
                  'conventions, decisions and their reasons, where things live, commands that work, gotchas. Plain bullets, each under '
                  '30 words, nothing about this conversation itself. Reply with the bullets only.\n\n'
                  + (('Summary of earlier messages:\n' + summary + '\n\n') if summary else '') + '\n\n'.join(transcript(visible)))
        result = await self.broker.invoke_agent(tenant_id, config, prompt, 'read', str(self.files.root(tenant_id, project_id, session_id)),
                                                self.protections(tenant_id, project_id))
        bullets = [line.strip() for line in (result.text or '').splitlines() if line.strip().startswith(('-', '*', '•'))]
        if not bullets:
            raise ValueError(f'{config["name"]} returned nothing to remember.')
        project = self.store.get(tenant_id, 'projects', project_id)
        stamp = now()[:10]
        project['memory'] = ((project.get('memory') or '').rstrip() + f'\n\n{stamp} · {session["name"]}\n' + '\n'.join(bullets)).strip()[-20000:]
        self.store.put(tenant_id, 'projects', project)
        self.store.event(tenant_id, session_id, 'project.remembered', f'{config["name"]} added {len(bullets)} notes to the project memory.')
        return project

    async def fanout(self, tenant_id, project_id, content, model_ids, mode):
        """The same message to several agents at once, each in its own worktree and session."""
        project = self.store.get(tenant_id, 'projects', project_id)
        if not model_ids:
            raise ValueError('Choose at least one agent.')
        sessions = []
        for model_id in dict.fromkeys(model_ids):
            config = self.select(tenant_id, model_id)
            session = {'id': uid(), 'project_id': project_id, 'name': f'{content.splitlines()[0][:60]} · {config["name"]}', 'messages': [], 'commands': [],
                       'created_at': now(), 'updated_at': now(), 'auto_named': False}
            branch = gitops.branch_name(content.splitlines()[0] + ' ' + config['name'], session['id'][:6])
            location = self.files.worktree_location(tenant_id, project_id, branch)
            await gitops.worktree_add(project['root'], location, branch, copy=project.get('worktree_copy') or [])
            session['worktree'] = {'path': str(location), 'branch': branch}
            self.store.put(tenant_id, 'sessions', session)
            self.send(tenant_id, project_id, session['id'], content, model_id, mode)
            sessions.append(self.store.get(tenant_id, 'sessions', session['id']))
        return sessions

    def writer(self, tenant_id, session, project):
        """The agent that writes on the conversation's behalf: its last one, the project's, or any."""
        agents = self.agents(tenant_id)
        if not agents:
            raise ValueError('Connect a Claude, Codex or OpenCode agent first.')
        preferred = [m.get('model_id') for m in reversed(session['messages']) if m.get('role') == 'assistant'] + [project.get('last_model_id')]
        return next((a for pick in preferred if pick and pick != ADAPTIVE for a in agents if a['id'] == pick), agents[0])

    async def compact(self, tenant_id, project_id, session_id):
        """Replace everything said so far with a summary the current agent writes, so the next turn
        is sent that instead. The messages stay in the conversation for the user; only the agent
        stops seeing them.
        """
        self.ensure_idle(tenant_id, session_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        project = self.store.get(tenant_id, 'projects', project_id)
        visible, summary = conversation(session)
        done = [m for m in visible if m.get('status') != 'running']
        if len(done) < 2:
            raise ValueError('There is nothing to compact yet.')
        config = self.writer(tenant_id, session, project)
        prompt = COMPACT_ASK + ('\n\nSummary of messages compacted earlier:\n' + summary if summary else '') + '\n\n' + '\n\n'.join(transcript(done))
        result = await self.broker.invoke_agent(tenant_id, config, prompt, 'read', str(self.files.root(tenant_id, project_id, session_id)),
                                                self.protections(tenant_id, project_id))
        text = (result.text or '').strip()
        if not text:
            raise ValueError(f'{config["name"]} returned no summary.')
        session = self.store.get(tenant_id, 'sessions', session_id)
        session['summary'] = {'text': text, 'through': done[-1]['id'], 'created_at': now(), 'model_name': config['name'],
                              'count': (session.get('summary') or {}).get('count', 0) + len(done)}
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'session.compacted', f'{config["name"]} summarised {len(done)} messages.')
        return session

    async def revert(self, tenant_id, project_id, session_id, message_id):
        """Put every file this turn touched back to how it was before it, and record that.

        With a checkpoint the repository restores the exact bytes, binaries included, and files
        the turn created are deleted. Without one, the recorded before-text of each change is
        written back, which covers everything the fingerprint could read.
        """
        self.ensure_idle(tenant_id, session_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        message = next((m for m in session['messages'] if m['id'] == message_id), None)
        if message is None or message['role'] != 'assistant':
            raise ValueError('That turn is not in this conversation.')
        if message.get('reverted_at'):
            raise ValueError('This turn was already reverted.')
        root = self.files.root(tenant_id, project_id, session_id)
        checkpoint = message.get('checkpoint')
        if checkpoint:
            restored = await gitops.restore(root, checkpoint['before'], checkpoint['after'])
        else:
            restored = self.undo_changes(tenant_id, project_id, session_id, message.get('changes') or [])
        if not restored:
            raise ValueError('This turn left no file changes to revert.')
        message['reverted_at'] = now()
        session['updated_at'] = now()
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'turn.reverted',
                         f'{len(restored)} file{"" if len(restored) == 1 else "s"} put back to before this turn.', message_id=message_id)
        return session

    def cost(self, tenant_id, model_id, result):
        """USD for this turn from the model's own per-million rates, or None when either is unknown."""
        if not result or not model_id or result.input_tokens is None or result.output_tokens is None:
            return None
        try:
            model = self.store.get(tenant_id, 'models', model_id)
        except Exception:
            return None
        rates = model.get('input_price'), model.get('output_price')
        if rates[0] is None or rates[1] is None:
            return None
        return round((result.input_tokens * rates[0] + result.output_tokens * rates[1]) / 1_000_000, 6)

    async def cancel(self, tenant_id, session_id):
        task = self.turns.get((tenant_id, session_id))
        if not task or task.done():
            raise ValueError('This conversation is not running.')
        # Stopping is the user stepping in, so whatever was waiting behind this turn is dropped too.
        session = self.store.get(tenant_id, 'sessions', session_id)
        if session.get('queue'):
            session['queue'] = []
            self.store.put(tenant_id, 'sessions', session)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    def recover(self):
        """A turn cannot survive a restart: its subprocess died with the application.

        The message is closed out rather than left running forever, because a message stuck in
        running blocks its conversation from ever being used again.
        """
        import json
        with self.store.db() as db:
            rows = db.execute("SELECT tenant_id,data FROM entities WHERE kind='sessions'").fetchall()
        for row in rows:
            session = json.loads(row['data'])
            stuck = [m for m in session['messages'] if m.get('role') == 'assistant' and m.get('status') == 'running']
            if not stuck:
                continue
            for message in stuck:
                message.update(status='failed', finished_at=now(),
                               error='Frontier closed while this turn was running. Anything already written to the folder is still there.')
                message['content'] = message['content'] or message['error']
            self.store.put(row['tenant_id'], 'sessions', session)
