"""Verified delivery through agent-stack: an independent review by the other model family, bound to the
exact content it saw, and a local commit of only that content.

agent-stack is installed globally (~/.agent-stack) by its own installer; Frontier only calls it. A team in a
project that turned on Verified delivery stages the integrated result, has agent-stack `route` it (which seals
a review packet and picks the opposite provider), runs the review, and on GO lets agent-stack `finish` make
the commit. A timeout, a malformed reply or any error is never a GO: the work stays uncommitted and the user
is told why.
"""
import asyncio
import json
import os
import re
import time
from pathlib import Path

from . import gitops
from .localprocess import child_env, child_flags, terminate

HOME = Path(os.environ.get('AGENT_STACK_HOME') or Path.home()/'.agent-stack')
REVIEW_BUDGET = 45*60  # agent-stack's own ceiling for one review cycle.
# ponytail: on Windows a Codex reviewer cannot read agent-stack's protected review snapshot (Access is denied), so
# Claude-written work is reviewed by Claude Fable instead, which agent-stack records as same-provider assurance.
# Drop this once agent-stack's Codex review works on Windows.
CLAUDE_WORK_REVIEWER = ('claude', 'claude-fable-5') if os.name == 'nt' else None
POLL = 15
REF_HINT = re.compile(r'no applicable (acceptance|decision|dependency|verification) contract')


class AgentStackError(RuntimeError):
    pass


def launcher(entry):
    """argv for one agent-stack entry point. On Windows only the .cmd launcher ships, and the review entry has
    none, so both run as the node.exe recorded at install time with the runtime script, as its docs require."""
    if os.name != 'nt':
        return [str(HOME/'bin'/('agent-stack' if entry == 'agent-stack' else 'agent-stack-review'))]
    try:
        node = re.search(r'"([^"]+node\.exe)"', (HOME/'bin'/'agent-stack.cmd').read_text(encoding='utf-8', errors='replace')).group(1)
    except (OSError, AttributeError):
        node = 'node'
    return [node, str(HOME/'runtime'/('agent-stack.mjs' if entry == 'agent-stack' else 'provider-review.mjs'))]


def installed():
    return (HOME/'runtime'/'agent-stack.mjs').is_file() and (HOME/'runtime'/'provider-review.mjs').is_file()


async def call(entry, args, cwd, timeout=300):
    proc = await asyncio.create_subprocess_exec(*launcher(entry), *args, cwd=str(cwd), env=child_env(), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except TimeoutError:
        await terminate(proc)
        raise AgentStackError(f'agent-stack {args[0]} took longer than {timeout} seconds and was stopped.') from None
    return proc.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')


def first_json(text):
    """agent-stack prints one JSON object per line (finish adds a second, its review seal)."""
    for line in text.splitlines():
        if line.startswith('{'):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


async def exclude_own_files(root):
    """agent-stack's run artifacts and Frontier's own folder never become part of what is reviewed and committed."""
    exclude = Path((await gitops.run(root, 'rev-parse', '--git-path', 'info/exclude')).strip())
    exclude = exclude if exclude.is_absolute() else Path(root)/exclude
    lines = exclude.read_text(encoding='utf-8').splitlines() if exclude.is_file() else []
    missing = [p for p in ('.agent-stack/', '.frontier/') if p not in lines]
    if missing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text('\n'.join(lines + missing) + '\n', encoding='utf-8')


async def route(root, change, session, reason, author, refs=None, reviewer=None):
    """Stage everything and seal it for review. agent-stack finds the project's README, AGENTS.md, package.json
    and the like as the review's reference contracts; a kind the project lacks points at a changed file instead."""
    await exclude_own_files(root)
    await gitops.run(root, 'add', '-A')
    paths = [p for p in (await gitops.run(root, 'diff', '--cached', '--name-only', '-z', 'HEAD')).split('\0') if p]
    if not paths:
        return None
    refs = dict(refs or {})
    for _ in range(5):
        args = ['route', '--root', str(root), '--change', change, '--session', session, '--route', 'standard', '--reason', reason[:500],
                '--author-provider', author, *[a for p in paths for a in ('--changed-path', p)],
                *[a for kind, p in refs.items() for a in (f'--{kind}-ref', p)],
                *(['--reviewer-provider', reviewer[0], '--reviewer-model', reviewer[1], '--reviewer-effort', 'max'] if reviewer else []), '--json']
        code, out, err = await call('agent-stack', args, root)
        sealed = first_json(out) if code == 0 else None
        if sealed and sealed.get('routeToken'):
            return {**sealed, 'paths': paths}
        missing = REF_HINT.search(err)
        if code == 0 or not missing or missing.group(1) in refs:
            raise AgentStackError(f'agent-stack could not seal the work for review: {(err or out).strip()[:600]}')
        tests = [p for p in paths if re.search(r'(^|/)(tests?/|test_)|\.(test|spec)\.', p)]
        refs[missing.group(1)] = (tests if missing.group(1) in ('acceptance', 'verification') and tests else paths)[0]
    raise AgentStackError('agent-stack kept asking for review references.')


async def review(root, sealed):
    """Run the opposite provider's review and wait for its verdict. 'review-running' means reattach to the same
    attempt, never start another; a busy state file while it writes is the same wait."""
    started = time.monotonic()
    while True:
        code, out, err = await call('agent-stack-review', ['--route-token', sealed['routeToken'], '--brief', sealed['reviewPacketPath']], root, timeout=REVIEW_BUDGET)
        result = first_json(out) if out.strip() else None
        running = (result or {}).get('status') == 'review-running' or 'compare-and-swap precondition failed' in err
        if not running:
            if code == 0 and result and result.get('verdict') in ('GO', 'NO-GO'):
                return result
            if (result or {}).get('status') == 'operator-decision-required':
                # The reviewer ran but ended without a valid verdict; agent-stack will not retry it on its own.
                raise AgentStackError(f'The {result.get("model")} reviewer ({result.get("provider")}) ended without a valid verdict, so '
                                      f'nothing counts as reviewed. `agent-stack review recover` shows why (route {result.get("routeId")}).')
            raise AgentStackError(f'The review did not return a verdict: {(err or out).strip()[:600]}')
        if time.monotonic() - started > REVIEW_BUDGET:
            raise AgentStackError('The review was still running after 45 minutes; it was not counted as a GO.')
        await asyncio.sleep(POLL)


async def finish(root, sealed, message):
    args = ['finish', '--repo', str(root), '--route-token', sealed['routeToken'], '--brief', sealed['reviewPacketPath'],
            '--baseline-token', sealed['baseline']['token'], '--message', ' '.join(message.split())[:200]]
    code, out, err = await call('agent-stack', args, root)
    result = first_json(out) if code == 0 else None
    if not result or not result.get('head'):
        raise AgentStackError(f'agent-stack did not commit the reviewed work: {(err or out).strip()[:600]}')
    return result
