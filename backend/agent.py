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
from collections import deque
from datetime import datetime
import os
import re
import secrets
import sys
from pathlib import Path

from . import adaptive, benchmark, board, gitops, localhealth, push, sandbox, slash, team as teamwork
from .adaptive import ADAPTIVE, MAX_ESCALATIONS
from .broker import CLI_TOOLS, ProviderError, Steer, cooling
from .projects import ProjectFiles
from .store import TenantIsolationViolationException, now, uid

# What the agent may do to the project this turn. The names are ours; each tool spells the
# same three postures differently, and broker.agent_argv is the only place that translates.
MODES = ('read', 'edit', 'auto')
# Oldest turns are dropped rather than refusing to continue: a conversation that stops being
# answerable is worse than one that has forgotten its beginning, and the model is told.
TRANSCRIPT_LIMIT = 120_000
# Summarize while there is still enough room to do it reliably. The hard limit in build_prompt
# remains the last-resort safety net for a failed compaction or one enormous current message.
AUTO_COMPACT_RATIO = .8
# The shortest time an agent gets for one turn, in any project. Engine builds and test gates run long.
MIN_TURN_MINUTES = 90
# How many turns a session's ledger keeps; older records fall off the front.
LEDGER_TURNS = 200
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
    'edit': ('This turn is Edit files: you may change files in the project and use the network (web pages, APIs, '
             'package installs), but git clone, commit and push are not possible — every .git directory is read only and '
             'git credentials are out of reach under this posture, so GitHub refuses with a credentials error even though '
             'the network works. If the task needs a clone, commit or push, do the file work, then say the user should '
             'resend that part under Full auto. Do not ask the user to run git by hand or to grant GitHub access.'),
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
    fresh browser (Edge, Chrome, or Playwright's own Firefox), headed when the user asked to watch and otherwise headless.
    With "my own browser", it is
    the Chrome or Edge the user already has open, reached through the Playwright extension, with their tabs
    and sign-ins; the extension's token, when given, connects without the user approving each time.
    On Windows npx is a .cmd, so it goes through cmd. Its tools run unasked in every tool, Codex included.
    """
    output = str(Path(project['root'])/'.frontier'/'browser')
    if project.get('agent_browser_mode') == 'mine':
        # The Playwright extension exists only for Chrome and Edge, so a user's own browser is never Firefox.
        channel = project.get('agent_browser_channel') if project.get('agent_browser_channel') in ('chrome', 'msedge') else 'chrome'
        args = ['-y', '@playwright/mcp@latest', '--extension', '--browser', channel, '--output-dir', output]
        command, args = (('cmd', ['/c', 'npx', *args]) if os.name == 'nt' else ('npx', args))
        return {'name': 'frontier-browser', 'transport': 'stdio', 'command': command, 'args': args, 'trusted': True, 'enabled': True,
                'env': {'PLAYWRIGHT_MCP_EXTENSION_TOKEN': token} if token else {}}
    args = ['-y', '@playwright/mcp@latest', '--isolated', '--output-dir', output] + ([] if project.get('agent_browser_visible') else ['--headless'])
    channel = project.get('agent_browser_channel')
    if channel in ('firefox', 'chrome'):
        args += ['--browser', channel]  # Firefox is Playwright's own build, fetched by ensure_firefox before the turn.
    elif os.name == 'nt':
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


FIREFOX_READY = False


def ensure_firefox(project):
    """Playwright drives its own build of Firefox, not one the user installed: downloaded once (about 120 MB) before the
    first turn that needs it, so the turn does not fail on a missing browser. A repeat is a one-second no-op."""
    global FIREFOX_READY
    if FIREFOX_READY or not project.get('agent_browser') or project.get('agent_browser_mode') == 'mine' or project.get('agent_browser_channel') != 'firefox':
        return
    import subprocess
    from .localprocess import child_flags
    command = ['cmd', '/c', 'npx'] if os.name == 'nt' else ['npx']
    try:
        done = subprocess.run([*command, '-y', '@playwright/mcp@latest', 'install-browser', 'firefox'], capture_output=True, timeout=900, **child_flags())
        FIREFOX_READY = done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass  # The turn goes on; the browser tool then reports what is missing.


def ensure_own_browser(project):
    """With "my own browser" and the browser closed, the Playwright extension starts it inside the agent's process tree,
    and it dies when the turn ends, taking a half-done sign-in with it. Frontier starts it first, detached, so it stays."""
    if os.name != 'nt' or not project.get('agent_browser') or project.get('agent_browser_mode') != 'mine':
        return
    import subprocess
    exe = 'msedge.exe' if project.get('agent_browser_channel') == 'msedge' else 'chrome.exe'
    running = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {exe}', '/NH'], capture_output=True, text=True, creationflags=0x08000000).stdout
    if exe in running.lower():
        return
    folder = 'Microsoft/Edge/Application' if exe == 'msedge.exe' else 'Google/Chrome/Application'
    found = next((p for p in (Path(os.environ.get(v, ''))/folder/exe for v in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')) if p.is_file()), None)
    if found:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB: not a child of this turn.
        try:
            subprocess.Popen([str(found)], creationflags=0x00000008 | 0x00000200 | 0x01000000, close_fds=True)
        except OSError:
            subprocess.Popen([str(found)], creationflags=0x00000008 | 0x00000200, close_fds=True)  # A job that forbids breakaway.


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
                'your connection starts on a tab of its own (a Playwright "Welcome" page). Navigate that tab to the page you need '
                'and do all your browsing in it, moving it from page to page; do not open new tabs (a tab opened here outlives '
                'your turn, and a goal or a team runs many turns). As your last browser step, close that tab with the tabs close '
                'action, and make no browser call after it, so nothing is left behind, unless you are handing it to the user '
                '(to sign in, approve, or pick something): then leave it open on that page and tell them it is waiting there. Do not touch the user\'s own tabs or '
                'change their settings, and do only what the task asks on sites where they are signed in.')
    return ('A real browser is available through the frontier-browser tools (navigate, click, type, read the page, console '
            'messages, screenshots).' + where + ' Use these tools for any web page, not another browser or computer-use tool. '
            'After changing anything a user sees, open it, check the console for errors, '
            'take a screenshot, and report what you saw, not what you expect.')


CARRY_ON = ('Yes, go ahead now with the work you described. Do not ask again whether to start; stop only for a decision '
            'that only I can make or for something that cannot be undone.')
# A reply that ends by asking leave to start: "Should I start the rebase now?", "Want me to open the PR?".
PROCEED_ASK = re.compile(r"\b(?:(?:should|shall|can|may) I|(?:do you want|would you like|want) me to|(?:ready|ok|okay) to)\b", re.I)
# A choice, or a step that cannot be taken back, stays with the user.
NOT_MINE_TO_TAKE = re.compile(r"\b(?:or|which|merge|deploy|release|publish|delete|remove|drop|force|reset|arm|post|send|email|pay|buy)\b", re.I)


DO_IT = ('Your last reply has been sent. Now start the work it says you are doing or will deliver, and take it as far as you can in this turn. '
         'Stop only for a decision that only I can make or for something that cannot be undone.')
FOLLOW_THROUGH = ('Your reply has been sent. If it names work of yours that is unfinished and not blocked, do it now, as far as you '
                  'can take it in this turn; your final reply is posted to the same Slack thread, so make it the update the sender '
                  'needs. If nothing is yours to do, or you asked the sender something and need the answer first, reply only '
                  '"nothing to do", which is not posted. Stop only for a decision that only I can make or for something that cannot be undone.')
# A reply that says the work is under way, or promises its result, when the turn is about to end: "I'm starting P9.21",
# "I'll build it next", "Estimated first head: within 90 minutes", "ETA 18:30".
COMMITS = re.compile(r"\bI(?:['\u2019]m| am) (?:now )?(?:starting|moving (?:on )?to|picking up|beginning|going to (?:start|build|begin|work))\b"
                     r"|\bI(?:['\u2019]ll| will) (?:now )?(?:start|begin|build|move (?:on )?to|pick up|get started)\b"
                     r"|\b(?:ETA|first head|I expect)\b|\bwithin \d+ (?:min|minutes|hours?)\b", re.I)


def commits_to_work(reply):
    """Whether a reply says the agent is starting work that the turn, by ending, leaves undone."""
    return bool(COMMITS.search(reply or ''))


def asks_to_proceed(reply):
    """Whether a reply ends with a plain request for leave to start the work it describes."""
    question = (reply or '').strip().rsplit('\n', 1)[-1].strip()
    question = re.split(r'(?<=[.!])\s+', question)[-1]
    return question.endswith('?') and bool(PROCEED_ASK.search(question)) and not NOT_MINE_TO_TAKE.search(question)


MIDTURN ='The user sent this while you were working. Take it into account now, without dropping what you were doing:\n\n'


def resumable(session, config, root, project, message):
    """The tool session to resume natively this turn, if the same tool that owns it is answering
    in the project folder itself (a worktree is another folder, which the tool's session does not know)."""
    native = session.get('native')
    if not native or message.get('team') or native['provider'] != config.get('provider') or native.get('model_id', config.get('id')) != config.get('id'):
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


def build_prompt(messages, switched, handoff=None, mode=None, summary=None, first=False, rules=None, memory=None, environment=None, board=None, composition=None):
    """Instructions, then as much of the conversation as fits, ending at the new message.

    A handoff, when present, is what a previous agent left behind on this same turn; it goes
    ahead of the conversation so the next agent continues rather than starts over. A summary
    stands in for messages that were compacted away. A `composition` dict, when given, is filled
    with the characters each part took and how many messages were dropped, for the turn ledger.
    """
    lines = transcript(messages)
    dropped = 0
    while len('\n\n'.join(lines)) > TRANSCRIPT_LIMIT and len(lines) > 1:
        lines.pop(0)
        dropped += 1
    parts, chars = [], {}

    def add(part, text):
        parts.append(text)
        chars[part] = chars.get(part, 0) + len(text)
    add('instructions', (HANDOVER if switched else CONTINUING) + ' ' + HOST)
    if mode in POSTURE:
        add('instructions', ' ' + POSTURE[mode])
    if first:
        add('instructions', ' ' + TITLE_ASK)
    if dropped:
        add('instructions', f' The first {dropped} messages of this conversation were dropped to fit; say so if one is needed.')
    if handoff:
        add('handoff', '\n\n' + handoff)
    if rules and rules.strip():
        add('rules', '\n\nWORKSPACE RULES (set by the user; follow them in every turn):\n' + rules.strip()[:8000])
    if memory and memory.strip():
        add('notes', '\n\nPROJECT NOTES (kept by the user and earlier conversations about this project):\n' + memory.strip()[:20000])
    if environment:
        add('environment', '\n\nPROJECT ENVIRONMENT: ' + environment)
    if board:
        add('board', '\n\nPROJECT TASK BOARD (open tasks shared by every session in this project). When this turn finishes, '
                     'advances or blocks one of them, update it with the frontier board_update tool if you have it; otherwise '
                     'say in your reply which task changed. Add a task with board_add only for real follow-up work the user '
                     'should see, not for steps of this turn.\n' + board)
    if summary:
        add('summary', '\n\nEarlier messages of this conversation were compacted into this summary:\n' + summary)
    add('transcript', '\n\n' + '\n\n'.join(lines))
    if composition is not None:
        composition.update(chars=chars, dropped=dropped)
    return ''.join(parts)


def turn_record(session, message, ledger):
    """One finished turn's line in the session ledger: who answered, how long it took, and where its context went."""
    chars = ledger.get('chars') or {}
    routing = message.get('routing') or {}
    finished = message.get('finished_at') or now()
    try:
        seconds = round((datetime.fromisoformat(finished) - datetime.fromisoformat(ledger['started_at'])).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        seconds = None
    return {'message_id': message['id'], 'status': message.get('status'), 'agent': message.get('model_name'),
            'model': ledger.get('model'), 'started_at': ledger.get('started_at'), 'finished_at': finished, 'seconds': seconds,
            'input_tokens': message.get('input_tokens'), 'output_tokens': message.get('output_tokens'),
            'chars': chars, 'prompt_chars': sum(chars.values()), 'dropped': ledger.get('dropped', 0),
            'compacted': (session.get('summary') or {}).get('count', 0),
            'escalated': bool(routing.get('escalations')), 'fallbacks': routing.get('fallbacks', 0)}


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
        # The open input of each running Claude turn, for messages handed in while it works.
        self.steers: dict[tuple[str, str], Steer] = {}
        # The agent each running turn is on right now, so Adaptive can see a local server whose slots are all taken.
        self.serving: dict[tuple[str, str], dict] = {}
        # What each conversation's agent is doing right now, line by line, for the live view. Kept after the turn
        # ends, until the next one starts, so a finished turn can still be read back. Memory only.
        self.live: dict[tuple[str, str], dict] = {}

    def watch(self, tenant_id, session_id, agent):
        """Start a conversation's live view afresh, and return what feeds it."""
        self.live[(tenant_id, session_id)] = {'agent': agent, 'started': now(), 'lines': deque(maxlen=600)}
        session = self.store.get(tenant_id, 'sessions', session_id)
        # A team worker's lines also show in the team's own conversation, labelled with the task.
        parent, label = session.get('team_parent'), session.get('name', '').split(' · ')[0]
        def feed(text):
            self.say(tenant_id, session_id, text)
            if parent:
                self.say(tenant_id, parent, f'[{label}] {text}')
        return feed

    def say(self, tenant_id, session_id, text):
        view = self.live.setdefault((tenant_id, session_id), {'agent': None, 'started': now(), 'lines': deque(maxlen=600)})
        view['lines'].append({'at': now(), 'text': text})

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

    async def fallback(self, tenant_id, tried, avoid=None, requirements=None, mode='auto'):
        """The agent to continue a turn its agent could not: the cheapest capable one that is connected, enabled, not
        yet tried, not cooling down and, if it is local, running. When `avoid` names a tool that just failed, another
        tool comes first: a tool that is not signed in fails the same way for every model on it."""
        candidates = [a for a in self.agents(tenant_id) if a['id'] not in tried and a.get('enabled') is not False
                      and (mode == 'auto' or a['provider'] != 'deepseek_harness')]
        online = await localhealth.availability(candidates)
        candidates = [a for a in candidates if online.get(a['id']) is not False]
        full = await localhealth.saturated(candidates, list(self.serving.values()))
        candidates = [a for a in candidates if a['id'] not in full] or candidates
        needs = adaptive.TaskRequirements(**requirements) if requirements else adaptive.TaskRequirements(
            task_type='implementation', complexity='medium', risk='low', requirements=adaptive.TASK_BASE['implementation'], reason='continuing a turn')
        ranked = [r for r in adaptive.score(needs, candidates, lambda model_id: cooling(self.broker, tenant_id, model_id)) if not r['cooling']]
        ranked.sort(key=lambda r: r['provider'] == avoid)  # Stable, so the ranking holds within each group.
        return next((a for r in ranked for a in candidates if a['id'] == r['id']), None)

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
        """A running turn's tool asks; the conversation shows a card until someone answers.

        A team worker's permission request is allowed at once: its conversation runs unattended, so a
        waiting card would only end in the timeout's deny."""
        turn = self.turn_tokens.get(token)
        if not turn:
            raise ValueError('This turn is not running.')
        tenant_id, project_id, session_id, message_id = turn
        approval = {'id': uid(), 'tenant_id': tenant_id, 'session_id': session_id, 'message_id': message_id, 'tool_name': tool_name,
                    'input': tool_input, 'tool_use_id': tool_use_id, 'created_at': now(), 'decision': None, 'message': None,
                    'event': asyncio.Event(), 'kind': kind}
        self.approvals[approval['id']] = approval
        session = self.store.get(tenant_id, 'sessions', session_id)
        if kind == 'permission' and session.get('team_parent'):
            # A team worker runs unattended in its own conversation: a card there would sit unseen until the
            # timeout denied it. The posture was chosen when the team started, so the request goes through;
            # the event keeps the trace where the worker's turn is shown.
            approval.update(decision='allow', message='Allowed without asking: a team worker runs unattended.', decided_at=now())
            approval['event'].set()
            self.store.event(tenant_id, session_id, 'approval.decided', f'{tool_name} was allowed without asking (team workers run unattended).',
                             message_id=message_id, approval_id=approval['id'])
            return approval['id']
        text = f'The agent asks: {str(tool_input.get("question") or "")[:200]}' if kind == 'question' else f'{tool_name} needs your permission.'
        self.store.event(tenant_id, session_id, 'approval.requested', text, message_id=message_id, approval_id=approval['id'])
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
        # A finished team whose NC handoff failed may be continued too: that run only retries the post.
        retry = last.get('team', {}).get('handoff', {}).get('state') == 'failed'
        if not last.get('team', {}).get('tasks') or (last.get('status') not in ('cancelled', 'failed', 'interrupted') and not retry):
            raise ValueError('Only a team that stopped before finishing can be continued.')
        # A team from before 0.20.8 did not record its request; it is the message that started it.
        asked = next((m['content'] for m in reversed(session['messages']) if m['role'] == 'user'), '')
        return self.send(tenant_id, project_id, session_id, 'Continue the team\u2019s work where it stopped.', last['model_id'], last['mode'],
                         team={'objective': asked, **last['team']})

    def send(self, tenant_id, project_id, session_id, content, model_id, mode, queue=False, team=False, context=None, unattended=False):
        """Record the message, start the turn, and answer immediately.

        The reply is written into a message that already exists and is marked running, so the
        conversation shows the turn in progress instead of appearing empty until it finishes.
        With Adaptive selected the agent is not yet known; it is chosen at the start of the turn
        and the message says so until then. While a turn is running, `queue` keeps the message
        for the moment it finishes instead of refusing it. An `unattended` turn (one from Slack) has nobody
        watching to pick another agent, so a spent pinned agent hands it on like Adaptive would.
        """
        if mode not in MODES:
            raise ValueError('Choose read only, edit files, or full auto.')
        if not team and re.match(r'/goal\b', content.strip(), re.I):
            return self.goal_command(tenant_id, project_id, session_id, content.strip()[5:].strip(), model_id, mode, queue)
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
            if config['provider'] == 'deepseek_harness' and (mode != 'auto' or team):
                raise ValueError('DeepSeek Harness headless can take Full auto single-agent turns only.')
        if team and mode == 'read':
            raise ValueError('A team needs Edit files or Full auto to do its work.')
        # The switch is decided against the last agent that actually answered, not against the
        # selector, so reselecting the same agent never costs a handover preamble. An Adaptive
        # turn decides this once it has chosen.
        previous = self.previous_agent(session, None)
        switched_from = previous['model_name'] if previous and not adaptive_turn and previous['model_id'] != model_id else None
        session['messages'].append({'id': uid(), 'role': 'user', 'content': content, 'created_at': now(), 'context': [c for c in (context or []) if c],
                                    **({'unattended': True} if unattended else {})})
        reply = {'id': uid(), 'role': 'assistant', 'content': '', 'status': 'running', 'error': None,
                 'model_id': None if adaptive_turn else model_id,
                 'model_name': 'Adaptive' if adaptive_turn else config['name'],
                 'provider': None if adaptive_turn else config['provider'],
                 'mode': mode, 'switched_from': switched_from,
                 'routing': {'mode': 'adaptive', 'status': 'choosing'} if adaptive_turn else {'mode': 'manual', **({'unattended': True} if unattended else {})},
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
        self.launch(tenant_id, project_id, session_id, reply['id'], config, mode)
        return session

    def launch(self, tenant_id, project_id, session_id, message_id, config, mode):
        key = (tenant_id, session_id)
        task = asyncio.create_task(self.run_turn(tenant_id, project_id, session_id, message_id, config, mode))
        self.turns[key] = task
        task.add_done_callback(lambda finished: self.turns.pop(key, None) if self.turns.get(key) is finished else None)
        task.add_done_callback(lambda finished: self.drain(tenant_id, project_id, session_id))

    def resume(self, tenant_id, project_id, session_id, message_id):
        """Run a turn a restart interrupted again, on the same message, agent and posture.

        Only the user starts this. The prompt is rebuilt from the stored transcript and the folder
        holds whatever the interrupted run already wrote, so nothing it knew is lost.
        """
        self.ensure_idle(tenant_id, session_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        message = session['messages'][-1] if session['messages'] else {}
        if message.get('id') != message_id or message.get('status') != 'interrupted':
            raise ValueError('Only the last turn, interrupted by a restart, can be resumed.')
        if message.get('team'):
            raise ValueError('Continue the team instead; its finished tasks are kept.')
        adaptive_turn = (message.get('routing') or {}).get('mode') == 'adaptive'
        config = None if adaptive_turn else self.select(tenant_id, message['model_id'])
        message.update(status='running', error=None, content='', finished_at=None)
        if adaptive_turn:
            message['routing'] = {'mode': 'adaptive', 'status': 'choosing'}
        session['updated_at'] = now()
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'turn.resumed', f'Resumed the interrupted turn with {message["model_name"]}.', message_id=message_id)
        self.launch(tenant_id, project_id, session_id, message_id, config, message.get('mode') or 'edit')
        return session

    def drain(self, tenant_id, project_id, session_id):
        """The next queued message takes its turn, unless the conversation was stopped or is busy again."""
        try:
            session = self.store.get(tenant_id, 'sessions', session_id)
        except Exception:
            return  # The conversation went away with its project; nothing left to run.
        queued = session.get('queue') or []
        if not queued and not self.busy(tenant_id, session_id):
            if (session.get('goal') or {}).get('status') == 'active':
                return self.advance_goal(tenant_id, project_id, session)
            return self.carry_on(tenant_id, project_id, session)
        if not queued or self.busy(tenant_id, session_id):
            return
        item, session['queue'] = queued[0], queued[1:]
        self.store.put(tenant_id, 'sessions', session)
        try:
            self.send(tenant_id, project_id, session_id, item['content'], item['model_id'], item['mode'], team=item.get('team', False), context=item.get('context'))
        except (ValueError, FileNotFoundError, TenantIsolationViolationException) as exc:
            self.store.event(tenant_id, session_id, 'queue.dropped', f'A queued message could not start: {exc}')

    def goal_command(self, tenant_id, project_id, session_id, rest, model_id, mode, queue=False):
        """/goal <objective> sets the session's goal and starts on it; /goal clear drops it; /goal resume (or a bare /goal)
        picks a paused or blocked one back up with a fresh turn budget, on the agent and posture now selected."""
        session = self.store.get(tenant_id, 'sessions', session_id)
        goal, word = session.get('goal'), rest.lower()
        if word in ('clear', 'stop', 'cancel', 'off'):
            if goal:
                goal.update(status='cleared', finished_at=now())
                self.store.put(tenant_id, 'sessions', session)
                self.store.event(tenant_id, session_id, 'goal.cleared', 'The goal was cleared.')
            return session
        if word in ('', 'resume', 'continue'):
            if not goal or goal.get('status') not in ('paused', 'blocked'):
                raise ValueError('Type /goal followed by what you want done.' if not goal or goal.get('status') in ('cleared', 'complete')
                                 else 'The goal is already being worked on.')
            goal.update(status='active', turns=0, misses=0, model_id=model_id, mode=mode, note=None)
            content = 'Continue toward the goal.'
        else:
            goal = {'objective': rest[:4000], 'status': 'active', 'turns': 0, 'misses': 0, 'max_turns': slash.MAX_GOAL_TURNS,
                    'model_id': model_id, 'mode': mode, 'started_at': now(), 'note': None}
            content = f'Goal: {goal["objective"]}'
        session['goal'] = goal
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'goal.set', f'Goal: {goal["objective"][:200]}')
        return self.send(tenant_id, project_id, session_id, content, model_id, mode, queue=queue)

    def advance_goal(self, tenant_id, project_id, session):
        """After a turn: a goal the agent reports complete or blocked ends; one still in progress gets its next turn,
        until a turn fails or is stopped, the agent twice gives no progress line, or the turn budget is spent."""
        goal = session.get('goal')
        last = next((m for m in reversed(session['messages']) if m['role'] == 'assistant'), None)
        if not goal or goal.get('status') != 'active' or not last or last.get('goal_counted'):
            return
        last['goal_counted'] = True
        goal['turns'] = goal.get('turns', 0) + 1
        verdict, detail = slash.goal_verdict(last.get('content'))
        go_on = False
        if last.get('status') != 'complete':
            goal.update(status='paused', note=f'The last turn {"was stopped" if last.get("status") == "cancelled" else "failed"}. Type /goal resume to go on.')
        elif verdict in ('complete', 'blocked'):
            goal.update(status=verdict, note=detail or None, finished_at=now())
        elif goal['turns'] >= goal.get('max_turns', slash.MAX_GOAL_TURNS):
            goal.update(status='paused', note=f'Paused after {goal["turns"]} turns. Type /goal resume for {slash.MAX_GOAL_TURNS} more.')
        else:
            goal['misses'] = 0 if verdict else goal.get('misses', 0) + 1
            if goal['misses'] >= 2:
                goal.update(status='paused', note='The agent twice ended a turn without reporting on the goal. Type /goal resume to go on.')
            else:
                go_on = True
        self.store.put(tenant_id, 'sessions', session)
        if not go_on:
            self.store.event(tenant_id, session['id'], 'goal.' + goal['status'], f'Goal {goal["status"]}: {goal.get("note") or goal["objective"][:200]}')
            return
        try:
            self.send(tenant_id, project_id, session['id'], f'Keep working toward the goal (turn {goal["turns"] + 1} of {goal.get("max_turns", slash.MAX_GOAL_TURNS)}).',
                      goal['model_id'], goal['mode'])
        except (ValueError, FileNotFoundError, TenantIsolationViolationException) as exc:
            goal.update(status='paused', note=f'The next turn could not start: {exc}')
            self.store.put(tenant_id, 'sessions', session)

    def carry_on(self, tenant_id, project_id, session):
        """A turn that ended by asking whether to start the work it just described gets a yes, once; one that
        said it is starting the work, and then ended, is told to do it, once.

        Asking leaves the work waiting on someone who already asked for it. Only a plain go-ahead is
        given: a choice between options, anything irreversible, or a question put to a Slack sender
        still waits for the person asked."""
        messages = session.get('messages') or []
        if len(messages) < 2:
            return
        asked, last = messages[-2], messages[-1]
        if last['role'] != 'assistant' or last.get('status') != 'complete' or last.get('team') or last.get('mode') == 'read' \
                or asked.get('auto_continue'):
            return
        if asked.get('unattended'):
            content = FOLLOW_THROUGH  # A reply to Slack names the work and ends the turn; whether any is left is the agent's to judge.
        elif asks_to_proceed(last.get('content')):
            content = CARRY_ON
        elif commits_to_work(last.get('content')):
            content = DO_IT  # Said it is starting, then ended the turn.
        else:
            return
        adaptive_turn = (last.get('routing') or {}).get('mode') == 'adaptive'
        try:
            session = self.send(tenant_id, project_id, session['id'], content, ADAPTIVE if adaptive_turn else last['model_id'], last['mode'],
                                unattended=bool(asked.get('unattended')))
        except (ValueError, FileNotFoundError, TenantIsolationViolationException) as exc:
            self.store.event(tenant_id, session['id'], 'turn.carry_on_failed', f'Could not go on by itself: {exc}')
            return
        session['messages'][-2]['auto_continue'] = True  # Marks the yes as Frontier's, so it is never given twice in a row.
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session['id'], 'turn.carried_on', f'{last.get("model_name") or "The agent"} ' + ('answered Slack; Frontier told it to do any work of its own left unfinished.' if content == FOLLOW_THROUGH
                                                                     else 'asked whether to start; Frontier told it to go ahead.' if content == CARRY_ON
                                                                     else 'said it was starting work and ended its turn; Frontier told it to do the work.'))

    def inject(self, tenant_id, session_id, content, model_id=None, mode=None):
        """Hand a message to the agent of the running turn, which reads it at its next step, and record it in the
        conversation. None when that turn cannot take one: not a Claude turn, between steps of a fallback, finished,
        or a message meant for another agent or posture, which waits for the turn to end instead."""
        steer = self.steers.get((tenant_id, session_id))
        if steer is None or content.lstrip().startswith('/'):
            return None
        session = self.store.get(tenant_id, 'sessions', session_id)
        running = next((m for m in reversed(session['messages']) if m['role'] == 'assistant' and m['status'] == 'running'), None)
        if running is None or running.get('team') or (model_id and model_id not in (ADAPTIVE, running.get('model_id'))) or (mode and mode != running.get('mode')):
            return None
        if not steer.push(MIDTURN + content):
            return None
        session['messages'].insert(session['messages'].index(running), {'id': uid(), 'role': 'user', 'content': content, 'created_at': now(), 'context': [], 'midturn': True})
        self.store.put(tenant_id, 'sessions', session)
        self.store.event(tenant_id, session_id, 'turn.midturn', f'Handed to {running.get("model_name") or "the agent"} while it works.', message_id=running['id'])
        return session

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

    def classifier(self, tenant_id):
        """The workspace's router model row, if it names one that is still connected."""
        model_id = self.store.tenant_internal(tenant_id).get('router_model_id')
        try:
            return self.store.get(tenant_id, 'models', model_id) if model_id else None
        except Exception:
            return None  # A classifier that was disconnected is not a reason to refuse a turn.

    async def effort(self, tenant_id, session):
        """Jev's reasoning effort for a turn on an agent the user picked; None without a TypeSafe router."""
        classifier = self.classifier(tenant_id)
        if not classifier or classifier['provider'] != 'typesafe' or not classifier.get('encrypted_key'):
            return None
        content = next(m['content'] for m in reversed(session['messages']) if m['role'] == 'user')
        found = await adaptive.typesafe_requirements(content, {}, self.store.decrypt(classifier['encrypted_key']), classifier.get('model_name') or 'jev-latest')
        return found.effort if found else None

    async def route(self, tenant_id, project_id, session, message):
        """Choose an agent for an Adaptive turn and write the decision onto the message."""
        content = next(m['content'] for m in reversed(session['messages']) if m['role'] == 'user')
        classifier = self.classifier(tenant_id)
        # Folder scans read up to thousands of files; off the event loop, so the window stays responsive.
        repo = await asyncio.to_thread(adaptive.repository_signals, self.files, tenant_id, project_id)
        requirements, classified_by = await adaptive.classify(content, repo, classifier, self.store.decrypt)
        # A local model whose server is not running is not available, however good its profile:
        # several can be configured while only one holds the GPU, and a turn sent to a server
        # that is down would fail and escalate to a cloud agent for no reason.
        await benchmark.refresh(self.store.directory)
        agents = [a for a in self.agents(tenant_id) if message['mode'] == 'auto' or a['provider'] != 'deepseek_harness']
        online = await localhealth.availability(agents)
        offline = [m for m in agents if online.get(m['id']) is False]
        up = [m for m in agents if online.get(m['id']) is not False]
        # A local server with a turn in every slot would make this one queue behind it: the next agent takes it instead,
        # unless nothing else can.
        full = await localhealth.saturated(up, [c for key, c in self.serving.items() if key != (tenant_id, session['id'])])
        free = [m for m in up if m['id'] not in full]
        chosen, ranked = adaptive.choose(requirements, free or up, lambda model_id: cooling(self.broker, tenant_id, model_id))
        # An agent the message names takes it, even if it is busy or dearer than the router would pick.
        chosen = adaptive.asked_for(content, adaptive.score(requirements, up)) or chosen
        if chosen is None:
            raise ProviderError('Adaptive found no agent that can take a turn.' + (' Every local agent is configured but none of their servers is running.' if offline else ' Connect a Claude, Codex or OpenCode agent.'))
        message['routing'] = {'mode': 'adaptive', 'status': 'chosen', 'classified_by': classified_by,
                              'requirements': requirements.model_dump(), 'repository': repo,
                              'chosen': {'id': chosen['id'], 'name': chosen['name'], 'because': chosen['because']},
                              'candidates': [{k: r[k] for k in ('id', 'name', 'cost_class', 'sufficient', 'deficit')} for r in ranked[:6]],
                              'offline': [m['name'] for m in offline], 'busy': [m['name'] for m in up if m['id'] in full],
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
        self.say(tenant_id, session_id, f'— {note}')
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
        steer = None if message.get('team') else self.steers.setdefault((tenant_id, session_id), Steer())
        await asyncio.to_thread(ensure_firefox, project)
        await asyncio.to_thread(ensure_own_browser, project)
        extras = self.extras(tenant_id, token, mode, project)
        extras['live'] = self.watch(tenant_id, session_id, message.get('model_name'))
        # Every project gets at least 90 minutes; a project may ask for more, never less.
        extras['timeout'] = max(MIN_TURN_MINUTES, int(project.get('turn_minutes') or 0)) * 60
        rules = (self.store.tenant_internal(tenant_id) or {}).get('rules')
        memory = project.get('memory')
        environment = ' '.join(filter(None, [(self.files.python_env(root) or {}).get('note'), browser_note(project, root)])) or None
        # An adversarial check judges the work, not the story of it, so it is not shown the board's reports.
        board_text = None if session.get('blind') else board.render(board.tasks(self.store, tenant_id, project_id))
        environment = ' '.join(filter(None, [environment, sandbox_note(sandbox.policy(project))])) or None
        if project.get('kind') == 'chats':
            # A chat is about anything; its folder is only somewhere to stand, not a codebase to look through.
            environment = ('This is a general conversation, not about a project. The working folder is an empty scratch '
                           'space for any files the conversation calls for; do not look through it for context.')
        goal = session.get('goal')
        if goal and goal.get('status') == 'active' and not message.get('team'):
            environment = ' '.join(filter(None, [environment, slash.goal_note(goal)]))
        # A message that is one agent command runs as that tool's own command where the tool can do that without its screen.
        asked = next((m['content'] for m in reversed(session['messages']) if m['role'] == 'user'), '')
        command = None if message.get('team') else slash.parse(asked)
        # Read only cannot change the folder, so it is not read twice to prove that.
        # In a repository, the whole working tree is also checkpointed as hidden git objects, so a
        # turn can be put back exactly, binaries included, without touching the user's branch.
        before, before_ref = (await asyncio.gather(asyncio.to_thread(fingerprint, self.files, tenant_id, project_id, session_id),
                                                   self.checkpoint(root, f'Frontier: before turn {message_id}'))) if mode != 'read' else ({}, None)
        status, error, result = 'complete', None, None
        attempts, tried, handoff_text, ranked = [], set(), None, []
        started, composition = now(), {}  # For the turn ledger: what the last prompt sent was made of.
        try:
            if adaptive_turn:
                config, ranked = await self.route(tenant_id, project_id, session, message)
                self.save_turn(tenant_id, session_id, message_id, routing=message['routing'])
                session, message = self.bind(tenant_id, session_id, message_id, config,
                                             f'Adaptive chose {config["name"]}: {message["routing"]["chosen"]["because"]}.')
            elif (await localhealth.availability([config])).get(config['id']) is False:
                # A manual choice is a pin, not a routing hint. Fail on that agent so the user can
                # deliberately change it; only Adaptive is allowed to choose another one.
                raise ProviderError(f'{config["name"]} is not running: nothing at {localhealth.where(config)} is serving it. Start that server, or pick another agent.')
            usage = context_usage(session)
            if usage['chars'] >= usage['limit'] * AUTO_COMPACT_RATIO:
                try:
                    session = await self.compact(tenant_id, project_id, session_id, before_message_id=message_id)
                    message = next(m for m in session['messages'] if m['id'] == message_id)
                except (ProviderError, ValueError) as exc:
                    # A failed summary must not prevent the actual turn. build_prompt still trims
                    # oldest messages at the hard limit, and the event explains why it had to.
                    self.store.event(tenant_id, session_id, 'session.compact_failed',
                                     f'Automatic compaction could not run ({exc}); the turn continued with the context safety limit.')
            # How hard Codex thinks this turn, from Jev. Other tools keep their own setting: Claude's cache is keyed on it.
            effort = (message['routing'].get('requirements') or {}).get('effort') if adaptive_turn else                 await self.effort(tenant_id, session) if config['provider'] == 'codex_cli' and not message.get('team') else None
            if effort:
                message['routing']['effort'] = effort
                self.save_turn(tenant_id, session_id, message_id, routing=message['routing'])
            if message.get('team'):
                # The lead plans and reviews under Read only; its workers take the real posture.
                # A continued team keeps its first checkpoint, so the lead reviews everything the team did.
                message['team']['checkpoint'] = message['team'].get('checkpoint') or before_ref
                session = self.save_turn(tenant_id, session_id, message_id, team=message['team'])
                message = next(m for m in session['messages'] if m['id'] == message_id)
                visible, summary = conversation(session)
                objective = message['team'].get('objective') or next(m['content'] for m in reversed(session['messages']) if m['role'] == 'user')
                lead_prompt = build_prompt(visible, False, None, 'read', summary, rules=rules, memory=memory, environment=environment, board=board_text, composition=composition)
                result = await teamwork.Team(self, tenant_id, project_id, session_id, message_id, config, mode, root).run(lead_prompt, objective)
            rounds = 0 if message.get('team') else 1 + (MAX_ESCALATIONS if adaptive_turn else 0)
            spare = 2  # How many times a spent agent may hand this turn to another agent.
            while rounds > 0:
                rounds -= 1
                tried.add(config['id'])
                self.serving[(tenant_id, session_id)] = config
                extras['effort'] = effort if config['provider'] == 'codex_cli' else None
                visible, summary = conversation(session)
                prompt = build_prompt(visible, bool(message.get('switched_from')), handoff_text, mode, summary,
                                      first=sum(1 for m in session['messages'] if m['role'] == 'user') == 1, rules=rules, memory=memory, environment=environment, board=board_text,
                                      composition=composition)
                native = resumable(session, config, root, project, message)
                cmd = command if command and config['provider'] in ('claude_cli', 'opencode_cli') else None
                if cmd:
                    prompt = cmd['raw'] if config['provider'] == 'claude_cli' else ''
                    composition.update(chars={'command': len(prompt)}, dropped=0)
                turn_extras = {**extras, 'slash': cmd} if cmd and config['provider'] == 'opencode_cli' else extras
                if steer is not None and not cmd:
                    turn_extras = {**turn_extras, 'steer': steer}
                result, error = None, None
                try:
                    if native:
                        # The same tool takes this turn, so its own session continues with its full memory;
                        # only what was said since it last spoke is sent. A session it cannot find falls back to the replay.
                        fresh = [m for m in session['messages'][native['upto']:] if m['id'] != message_id]
                        try:
                            result = await self.broker.invoke_agent(tenant_id, config, prompt if cmd else build_prompt(fresh, False, handoff_text, mode, rules=rules, memory=memory, environment=environment, board=board_text, composition=composition),
                                                                    mode, root, {**turn_extras, 'resume': native['id']})
                            result.resumed = True
                        except ProviderError as exc:
                            if exc.exhausted:
                                raise
                            session['native'] = None
                            self.save_turn(tenant_id, session_id, message_id, session_fields={'native': None})
                            self.store.event(tenant_id, session_id, 'turn.agent', f'{config["name"]} could not resume its own session ({exc}); the conversation is replayed instead.', message_id=message_id)
                    if result is None:
                        result = await self.broker.invoke_agent(tenant_id, config, prompt, mode, root, turn_extras)
                except ProviderError as exc:
                    # Adaptive and automatically assigned team workers may route around an unavailable
                    # or spent agent. A user's manual choice stays pinned and reports the failure instead
                    # of silently changing the model, unless nobody is there to read it (a Slack turn).
                    # An agent stopped after doing work is never handed over because its work is already in the folder.
                    failover = ((adaptive_turn or message['routing'].get('unattended')) and exc.exhausted) or \
                               (bool(session.get('team_parent')) and (exc.exhausted or not exc.worked))
                    following = await self.fallback(tenant_id, tried, None if exc.exhausted else config['provider'],
                                                    (message.get('routing') or {}).get('requirements'), mode=mode) if failover and spare else None
                    if following is None:
                        if exc.exhausted:
                            raise
                        error = str(exc)
                    else:
                        spare -= 1
                        rounds += 1
                        outcome = 'out of usage' if exc.exhausted else 'could not run'
                        if 'context window' in str(exc):  # The task outgrew this model; its record should say so.
                            adaptive.record_outcome(self.store, tenant_id, config, failed=1, overflows=1)
                        attempts.append({'id': config['id'], 'name': config['name'], 'outcome': outcome, 'reply': ''})
                        message['routing'].update(attempts=attempts, fallbacks=message['routing'].get('fallbacks', 0) + 1)
                        self.save_turn(tenant_id, session_id, message_id, routing=message['routing'])
                        handoff_text = adaptive.render_handoff(adaptive.handoff(session, message, attempts))
                        spent = config['name']
                        config = following
                        why = 'is out of usage' if exc.exhausted else f'could not run ({str(exc)[:160]})'
                        session, message = self.bind(tenant_id, session_id, message_id, config, f'{spent} {why}; {config["name"]} continues this turn.')
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
                # Escalate: a stronger agent continues this same turn, told what happened so far, at its own default effort.
                effort = None
                message['routing'].pop('effort', None)
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
            self.serving.pop((tenant_id, session_id), None)
            if steer is not None and self.steers.get((tenant_id, session_id)) is steer:
                self.steers.pop((tenant_id, session_id), None)
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
                        checkpoint={'before': before_ref, 'after': after_ref} if before_ref and after_ref else None,
                        ledger={'started_at': started, 'model': (config or {}).get('model_name'), **composition})

    async def checkpoint(self, root, label):
        try:
            return await gitops.checkpoint(root, label)
        except (gitops.GitError, OSError, asyncio.CancelledError):
            return None  # A folder that is not a repository, or a git that cannot run, simply has no checkpoint.

    def finish(self, tenant_id, project_id, session_id, message_id, status, error, result, before, mode, attempts=(), checkpoint=None, after=None, ledger=None):
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
        if not session.get('native') and status == 'complete' and result and result.session_id and not message.get('team') \
                and not (result.served_by and result.served_by != message['model_id']):
            # The tool's own session now holds the whole conversation, so the same agent's next turn continues it
            # instead of opening a new session and replaying the transcript.
            config = self.store.get(tenant_id, 'models', message['model_id'])
            session['native'] = {'provider': config['provider'], 'model_id': config['id'], 'id': result.session_id, 'upto': len(session['messages'])}
        if result and result.served_by and result.served_by != message['model_id']:
            # Another connected subscription answered because the selected one was spent. The
            # message names the agent that replied, or the conversation credits the wrong one.
            served = self.store.get(tenant_id, 'models', result.served_by)
            message.update(model_id=result.served_by, model_name=served['name'])
            self.store.event(tenant_id, session_id, 'account.switched',
                             f'{served["name"]} took over after the previous subscription ran out of usage.', message_id=message_id)
        if ledger is not None:
            session['ledger'] = (session.get('ledger') or [])[-(LEDGER_TURNS-1):] + [turn_record(session, message, ledger)]
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
        existing = (project.get('memory') or '').strip()
        new_notes = f'{stamp} · {session["name"]}\n' + '\n'.join(bullets)
        merge_prompt = (
            'Reconcile the current project memory with the new dated notes worth remembering. Return the complete replacement '
            'memory only, at most 20,000 characters. Remove exact and semantic duplicates. When facts conflict, keep the newest '
            'fact. Keep every original date heading exactly as written for facts that remain, and keep each retained fact under its '
            'original date heading. Order date sections oldest to newest. Do not add commentary or markdown fences.\n\n'
            'CURRENT PROJECT MEMORY:\n' + (existing or '(empty)') + '\n\nNEW DATED NOTES:\n' + new_notes
        )
        merged = await self.broker.invoke_agent(tenant_id, config, merge_prompt, 'read',
                                                str(self.files.root(tenant_id, project_id, session_id)),
                                                self.protections(tenant_id, project_id))
        memory = (merged.text or '').strip()
        if not memory:
            raise ValueError(f'{config["name"]} returned no reconciled project memory.')
        project['memory'] = memory[-20_000:]
        self.store.put(tenant_id, 'projects', project)
        self.store.event(tenant_id, session_id, 'project.remembered',
                         f'{config["name"]} reconciled {len(bullets)} new notes with the project memory.')
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

    async def compact(self, tenant_id, project_id, session_id, before_message_id=None):
        """Replace completed history with a summary the current agent writes.

        The messages stay in the conversation for the user; only the agent stops seeing them.
        During an automatic pre-turn compaction, the in-flight user/assistant pair stays visible.
        """
        if before_message_id is None:
            self.ensure_idle(tenant_id, session_id)
        session = self.store.get(tenant_id, 'sessions', session_id)
        project = self.store.get(tenant_id, 'projects', project_id)
        visible, summary = conversation(session)
        if before_message_id is None:
            candidates = visible
        else:
            boundary = next((i for i, m in enumerate(visible) if m['id'] == before_message_id), len(visible))
            # The message immediately before the running assistant is the current user request.
            # Keep both messages visible; compact only the completed history preceding that pair.
            candidates = visible[:max(0, boundary - 1)]
        done = [m for m in candidates if m.get('status') != 'running']
        if len(done) < 2:
            if before_message_id is not None:
                return session
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

        The message is closed out as interrupted rather than left running forever, because a message
        stuck in running blocks its conversation from ever being used again. It is not rerun on its
        own: the user resumes it with one click, or moves on.
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
                message.update(status='interrupted', finished_at=now(),
                               error='Frontier closed while this turn was running. Anything already written to the folder is still there.')
                message['content'] = message['content'] or message['error']
            self.store.put(row['tenant_id'], 'sessions', session)
            for message in stuck:
                self.store.event(row['tenant_id'], session['id'], 'turn.interrupted',
                                 f'{message.get("model_name") or "The agent"} was interrupted when Frontier closed.', message_id=message['id'])
