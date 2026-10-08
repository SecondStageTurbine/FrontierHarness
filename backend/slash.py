"""Slash commands: Frontier's own (/goal, /resume), the agents' own, and the ones only a tool's interactive screen has.

A message that is one agent command runs as that tool's own command where the tool can run commands without its
interactive screen: Claude Code takes `/name args` as its prompt, OpenCode takes `run --command name args`. Codex has
no such mode, so its commands, like Claude's screen-only ones, open the tool in the Terminal panel instead.

/goal is Frontier's, for every agent: the session keeps one objective, and after each turn Frontier sends the next one
until the agent reports the goal complete or blocked, a turn fails or is stopped, or the turn budget runs out.
"""
import re
from pathlib import Path

COMMAND = re.compile(r'^/([A-Za-z0-9][\w:.-]*)(?:[ \t]+([\s\S]*))?$')
FRONTIER = [
    {'name': 'goal', 'description': 'Keep the agents working toward one objective, turn after turn, until it is done or blocked. '
                                    '/goal clear stops it; /goal resume picks a paused one back up.'},
    {'name': 'resume', 'description': 'Continue a Claude Code or Codex conversation from this folder'},
]
# Commands that exist only in a tool's interactive screen; run from Frontier they open that tool in the Terminal panel.
CODEX_SCREEN = {
    'review': 'Review the current changes', 'init': 'Create an AGENTS.md for this project', 'status': 'Session, model and limits',
    'diff': 'Show the git diff', 'model': 'Choose the model and reasoning effort', 'approvals': 'Choose what Codex may do without asking',
    'compact': 'Summarize the conversation to free context', 'new': 'Start a new conversation', 'mention': 'Mention a file',
    'mcp': 'List the MCP tools', 'resume': 'Resume a saved conversation', 'logout': 'Sign out of Codex',
}
CLAUDE_SCREEN = {'clear', 'config', 'color', 'focus', 'model', 'effort', 'fast', 'rename', 'mcp', 'doctor', 'agents', 'output-style',
                 'reload-plugins', 'reload-skills', 'heapdump', 'autocompact', 'list-agents', 'design-consent', 'design-revoke',
                 'design-sync', 'auto-mode-setup', 'import', 'usage', 'context', 'skill-doctor', 'goal', 'loop'}
MAX_GOAL_TURNS = 25
GOAL_LINE = re.compile(r'^\s*GOAL:\s*(complete|blocked|continuing)\b\s*[—\-:]*\s*(.*)$', re.I | re.M)


def parse(text):
    """`/name args` when the whole message is one command for an agent; Frontier's own names never reach one."""
    found = COMMAND.match((text or '').strip())
    if not found or found.group(1).lower() in {c['name'] for c in FRONTIER}:
        return None
    return {'name': found.group(1), 'args': (found.group(2) or '').strip(), 'raw': text.strip()}


def opencode_commands(root):
    found = []
    for base in (Path(root)/'.opencode', Path.home()/'.config'/'opencode'):
        for folder in ('command', 'commands'):
            found += [f.stem for f in sorted((base/folder).glob('*.md'))] if (base/folder).is_dir() else []
    return found


def catalog(root, claude_seen, on_disk):
    """Every command the composer offers: Frontier's, what Claude Code reported in its last turn (its built-ins,
    plugins and skills), what is on disk for each tool, and the screen-only ones that open in the Terminal panel."""
    entries, seen = [], set()
    def add(name, source, description='', kind='agent'):
        key = (name.lower(), source if kind == 'terminal' else '')
        if key not in seen:
            seen.add(key)
            entries.append({'name': name, 'source': source, 'description': description, 'kind': kind})
    for c in FRONTIER:
        add(c['name'], 'Frontier', c['description'], 'frontier')
    described = {s['name']: s.get('description') or '' for s in on_disk}
    for name in claude_seen:
        if name.startswith('__') or name in CLAUDE_SCREEN:
            continue
        add(name, 'Claude', described.get(name, ''))
    for s in on_disk:
        add(s['name'], {'claude': 'Claude', 'codex': 'Codex', 'gemini': 'Gemini'}.get(s['provider'], s['provider']), s.get('description') or '')
    for name in opencode_commands(root):
        add(name, 'OpenCode')
    for name, description in CODEX_SCREEN.items():
        add(name, 'Codex', description + ' (opens Codex in the Terminal panel)', 'terminal')
    for name in sorted(CLAUDE_SCREEN - {'goal', 'loop'}):
        add(name, 'Claude', 'Opens Claude Code in the Terminal panel', 'terminal')
    return entries


def goal_note(goal):
    return (f'This session has a standing goal set by the user: {goal["objective"]}\n'
            'Work toward it in this turn, as far as you can. End your reply with exactly one last line, one of: '
            '"GOAL: complete — <the evidence that it is done, checked>", "GOAL: blocked — <what stops you that only the user can '
            'resolve>", or "GOAL: continuing" when more turns are needed. While you say continuing, Frontier sends the next turn '
            'for you; do not ask the user whether to go on.')


def goal_verdict(reply):
    """The last GOAL line of a reply, as (status, detail), or (None, '') when the agent gave none."""
    found = list(GOAL_LINE.finditer(reply or ''))
    return (found[-1].group(1).lower(), found[-1].group(2).strip()) if found else (None, '')
