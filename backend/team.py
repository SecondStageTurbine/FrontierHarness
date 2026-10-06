"""Team mode: one agent leads, the others do the work.

The lead takes a Read-only turn to plan the work as tasks. Frontier hands each task to an agent,
the lead's choice or Adaptive's by capability, as its own session, in its own worktree when the
project is a repository, running independent tasks at the same time. Each worker's changes are
brought back as a patch. Another model family always reviews the integrated work; where the project
asks for it, an adversarial check then tries to break the result in a throwaway worktree. The
project's own tests run, then the lead reviews the reports, the diff and the test result, may send
one round of fixes, and writes the final reply. The lead never edits; the workers never see each other's transcripts.
"""
import asyncio
import json
import re
from pathlib import Path

from . import board
from . import adaptive, agentstack, benchmark, gitops, localhealth
from .adaptive import TaskRequirements
from .broker import CLI_TOOLS, AgentResult, ProviderError, cooling as broker_cooling
from .store import now, uid

MAX_TASKS = 6
CAPABILITIES = ('coding', 'reasoning', 'planning', 'debugging', 'architecture', 'review', 'tool_use', 'repository', 'instruction_following', 'speed')

PLAN_ASK = (
    'You are the lead of a team of coding agents working on this project. Do not do the work yourself. '
    'Split the request at the end of the conversation into tasks other agents will carry out in this folder, '
    'and reply with ONLY a JSON object inside a ```json fence, in this shape:\n'
    '{"summary": "one paragraph of what the team will deliver", "tasks": [{"id": "t1", "title": "short title", '
    '"instructions": "everything the worker needs to do this task well: files, behaviour, tests, acceptance", '
    '"needs": ["coding", "debugging"], "agent": null, "parallel": true, "depends_on": []}]}\n'
    f'Rules: 1 to {MAX_TASKS} tasks. "needs" lists what the task demands most from: {", ".join(CAPABILITIES)}. '
    '"agent" may name one of the connected agents listed below to insist on it, else null. Tasks with "parallel": true '
    'and no dependencies run at the same time in separate copies of the folder, so give them disjoint files. Put shared '
    'groundwork first with "parallel": false. Every task must be doable by an agent that has not read this conversation, '
    'so its instructions must stand alone. A review task is always given to a different model family from you and from '
    'the agents whose work it checks, and you review again at the end, so the work gets two independent reviews.\n'
    'Staff the team from the whole roster below, not from your own model family: every tool and every local model on it '
    'is available. Match each task to the agent whose strengths fit it; give simple, well-specified tasks to cheaper and '
    'local agents (a local one runs on this computer at no usage cost) and keep the strongest for the hard ones: '
    'integration, debugging across the codebase, and anything the rest depends on. Name the agent for every task in '
    '"agent", exactly as the roster writes it; a task you leave unnamed goes to the cheapest agent whose strengths cover '
    'its "needs", which suits only small, self-contained work. A local agent has a small context window (the roster '
    'gives its size): it can read only so much in one task before it has to stop. Give local agents tasks that touch a '
    'few files; a task that reads long logs, large diffs or much of the codebase, including most reviews, goes to a '
    'cloud agent.'
)


def roster(agents, online, lead):
    """The connected agents as the lead is shown them: tool, model, where it runs, cost, and what it is best at."""
    lines = []
    for agent in agents:
        cap = adaptive.profile(agent) or {}
        local = localhealth.server_for(agent) is not None or cap.get('location') == 'local'
        best = sorted((c for c in CAPABILITIES if c != 'speed'), key=lambda c: -cap.get(c, 0))[:3]
        window = localhealth.context_limit(agent)
        track = '; '.join(filter(None, [benchmark.describe(agent.get('benchmark')), adaptive.describe_track(agent.get('track'))]))
        where = (('local on this computer, no usage cost' + (f', context window {window // 1000}K tokens' if window else ''))
                 if local else f'cloud, {cap.get("cost_class", "unknown")} cost')
        lines.append(f'- {agent["name"]}: {CLI_TOOLS.get(agent["provider"], ("", "", agent["provider"]))[2]}, model {agent.get("model_name")}; {where}; '
                     f'best at {", ".join(f"{c} {cap.get(c, 0)}" for c in best)}; speed {cap.get("speed", 5)}; {track}' + (' (you, the lead)' if agent['id'] == lead['id'] else ''))
    offline = [a['name'] for a in online.get('offline', [])]
    return ('CONNECTED AGENTS (strengths are 0 to 10, already adjusted by the public DeepSWE and Terminal-Bench '
            'benchmarks where they list the model and by each agent\'s track record on this computer; the DeepSWE cost is per task at API prices, so '
            'compare it between agents rather than read it as a bill):\n' + '\n'.join(lines)
            + (f'\nOffline right now, so not on the team: {", ".join(offline)}.' if offline else ''))
REVIEW_ASK = (
    'You are the lead. Your workers have finished; their reports and the resulting diff of the project are below. '
    'Judge whether the request is now done. Reply with ONLY a JSON object inside a ```json fence: '
    '{"verdict": "done" or "fix", "fixes": [{"task_id": "t1", "instructions": "what to change"}], '
    '"requirements": [{"what": "one thing the request requires", "status": "met" or "unmet" or "unverified"}], '
    '"suspicions": ["a possible defect you could not confirm"], '
    '"reply": "your final message to the user, in markdown, describing what was done, by whom, and anything left open"}. '
    'Start by listing what the request requires, as kinds of behaviour rather than single examples, and judge each '
    'against the code itself. The workers\' reports and their passing tests are claims, not proof: read the changed '
    'code, and weigh what a test asserts, since a test that would still pass with the behaviour wrong proves nothing. '
    'Look hardest at edge cases, error paths and where the tasks meet. '
    'Ask for fixes only for real defects a worker can correct in one more pass; otherwise say done. '
    'If the project\'s tests failed because of the team\'s work, ask for fixes; say done with failing tests only '
    'when the failure plainly has nothing to do with this work, and say so in your reply.'
)
ADVERSARY_NOTE = (
    'A task marked ADVERSARIAL CHECK tried to break the result in a throwaway copy of the folder; nothing it changed '
    'is in the project. Its FINDINGS are claims too: ask for a fix only for one you confirm by reading the code, and '
    'send it to the task whose work it concerns, never to the adversarial check. Its OBSERVATIONS are leads to look '
    'at, not defects.'
)
GATE_NOTE = (
    'Verified delivery is on: the INDEPENDENT REVIEW below is by another model family, bound to the exact files staged '
    'for commit. On NO-GO, send a fix for every finding you cannot disprove from the code; its findings go back to the '
    'workers in any case. Only a GO is committed.'
)
ADVERSARY_ASK = (
    'You did not write this work, and you are not here to improve it: try to break it. The changes in this folder are '
    'meant to fulfil the user\'s request below. Attack them where defects hide: edge cases, invalid and empty input, '
    'error paths, interactions between features, and anything the request requires that the work may have missed. '
    'Run real probes (commands, small scripts, the project\'s tests) in this folder; it is a throwaway copy and nothing '
    'you change here is kept, so do not fix anything. Reply with a report in three parts. FINDINGS: each a defect '
    'against the request, with the exact reproduction (command or input) and what happened against what the request '
    'requires; write "none" if you found none. OBSERVATIONS: suspicions you could not reproduce. HELD: what you '
    'attacked that held up.'
)


def parse_json(text):
    """The first JSON object in a reply, fenced or bare; None when there is none."""
    if not text:
        return None
    fenced = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find('{')
    if start >= 0:
        candidates.append(text[start:text.rfind('}')+1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def normalise_plan(plan):
    tasks = []
    seen = set()
    for index, raw in enumerate((plan or {}).get('tasks') or []):
        if not isinstance(raw, dict) or not (raw.get('instructions') or '').strip():
            continue
        task_id = str(raw.get('id') or f't{index+1}')
        if task_id in seen:
            task_id = f'{task_id}-{index+1}'
        seen.add(task_id)
        tasks.append({'id': task_id, 'title': (raw.get('title') or f'Task {index+1}')[:80], 'instructions': raw['instructions'].strip()[:8000],
                      'needs': [n for n in (raw.get('needs') or []) if n in CAPABILITIES][:5], 'agent': raw.get('agent') or None,
                      'parallel': bool(raw.get('parallel', True)), 'depends_on': [str(d) for d in (raw.get('depends_on') or []) if str(d) in seen or True][:5],
                      'status': 'pending', 'session_id': None, 'model_id': None, 'model_name': None, 'report': '', 'merge': None})
        if len(tasks) >= MAX_TASKS:
            break
    for task in tasks:
        task['depends_on'] = [d for d in task['depends_on'] if d in seen and d != task['id']]
    return {'summary': (plan or {}).get('summary') or '', 'tasks': tasks}


def ensure_review(tasks, agents, lead):
    """Every team's work gets a review by another model family, whether or not the lead planned one.

    Added last and after every other task, so it sees the integrated result; `assign` then gives it
    to a family other than the lead's and the authors'. With only the lead's family connected, the
    lead's own final review is all there is, so nothing is added.
    """
    if not tasks or any(is_review(t) for t in tasks) or all(a['provider'] == lead['provider'] for a in agents):
        return
    ids = [t['id'] for t in tasks]
    tasks.append({'id': 'review', 'title': 'Review the integrated result', 'needs': ['review'], 'agent': None, 'parallel': False, 'depends_on': ids,
                  'instructions': 'The other tasks are finished and their changes are in this folder. Review them against the user\'s request: '
                                  'read the changed files, run the project\'s tests or build if it has them, and fix clear defects directly. '
                                  'Report what you checked, what you fixed, and anything still wrong.',
                  'status': 'pending', 'session_id': None, 'model_id': None, 'model_name': None, 'report': '', 'merge': None})


def ensure_adversary(tasks):
    """An adversarial check after every other task: a fresh agent that tries to break the integrated result.

    It works in its own worktree and its changes are never folded back, so it needs a repository.
    It is given the request and the code, not the plan or the reports, and it counts as a review, so
    `assign` gives it to a family other than the lead's and the authors'. The lead checks its
    findings before any becomes a fix.
    """
    if not tasks or any(t.get('adversary') for t in tasks):
        return
    tasks.append({'id': 'adversary', 'title': 'Try to break the result', 'needs': ['debugging', 'review'], 'agent': None, 'parallel': False,
                  'depends_on': [t['id'] for t in tasks], 'adversary': True, 'instructions': ADVERSARY_ASK,
                  'status': 'pending', 'session_id': None, 'model_id': None, 'model_name': None, 'report': '', 'merge': None})


def project_tests(root):
    """The project's own test command when it plainly has one, else None. Only commands the project
    check runner accepts, so the gate runs exactly what the user could run from the Terminal panel."""
    root = Path(root)
    try:
        scripts = json.loads((root/'package.json').read_text(encoding='utf-8')).get('scripts')
    except (OSError, ValueError, AttributeError):
        scripts = None
    test = scripts.get('test') if isinstance(scripts, dict) else None
    if isinstance(test, str) and test.strip() and 'no test specified' not in test:
        return 'npm test'
    try:
        pyproject = (root/'pyproject.toml').read_text(encoding='utf-8')
    except OSError:
        pyproject = ''
    if (root/'pytest.ini').exists() or (root/'conftest.py').exists() or '[tool.pytest' in pyproject or any((root/'tests').glob('test_*.py')):
        return 'python -m pytest -q'
    return None


REVIEW_WORDS = re.compile(r'\b(review|audit|verify|verification|integrat\w*|final gate|check)\b', re.I)


def is_review(task):
    return 'review' in (task.get('needs') or []) or bool(REVIEW_WORDS.search(task.get('title') or ''))


def findings_as_fixes(findings, tasks):
    """An independent review's findings as fixes, each to the task that changed the files it names, else the first worker's."""
    workers = [t for t in tasks if not t.get('adversary') and not is_review(t)] or [t for t in tasks if not t.get('adversary')]
    grouped = {}
    for f in findings:
        paths = set(f.get('affectedPaths') or [])
        task = next((t for t in workers if paths & set(t.get('changed') or [])), workers[0] if workers else None)
        if task:
            grouped.setdefault(task['id'], []).append(
                f'[{f.get("id")} {f.get("severity")}] {f.get("impact")}\nEvidence: {f.get("evidence")}\n'
                f'Required change: {f.get("requiredChange")}\nHow it will be checked: {f.get("closingProbe")}')
    return [{'task_id': tid, 'instructions': 'The independent review found these defects; fix each one:\n\n' + '\n\n'.join(items)}
            for tid, items in grouped.items()]


def assign(task, agents, lead, in_cooldown=lambda model_id: False, authors=()):
    """The agent for one task: the lead's explicit pick if it is connected, else the cheapest that covers the needs.

    A review task goes to a different model family from the lead and, where one is connected, from
    the agents whose work it reviews, so the lead's own review is a second, independent pair of eyes.
    """
    wanted = (task.get('agent') or '').strip().lower()
    candidates = agents
    if is_review(task):
        other = {lead['provider'], *authors}
        fresh = [a for a in agents if a['provider'] not in other]
        not_lead = [a for a in agents if a['provider'] != lead['provider']]
        candidates = fresh or not_lead or agents
    if wanted:
        for agent in candidates:
            if agent['name'].lower() == wanted or agent.get('model_name', '').lower() == wanted:
                return agent
    agents = candidates
    needs = {c: 5 for c in CAPABILITIES if c != 'speed'}
    for need in task.get('needs') or []:
        needs[need] = 8
    requirements = TaskRequirements(task_type='implementation', complexity='medium', risk='low', requirements=needs, reason=f'team task {task["id"]}')
    best, _ = adaptive.choose(requirements, agents, in_cooldown)
    if best:
        return next(a for a in agents if a['id'] == best['id'])
    return lead


def worker_prompt(objective, plan, task, reports):
    lines = ['You are one agent on a team working in this project folder. Do your task completely, then reply with a short report: what you changed, which files, how you verified it, and anything you could not do.',
             f'\nOVERALL REQUEST FROM THE USER:\n{objective}', f'\nTEAM PLAN:\n{plan.get("summary") or "(none)"}']
    if reports:
        lines.append('\nWHAT OTHER TEAM MEMBERS ALREADY DID:\n' + '\n'.join(f'- {r["title"]} ({r["model_name"]}): {r["report"][:600]}' for r in reports))
    lines.append(f'\nYOUR TASK — {task["title"]}:\n{task["instructions"]}')
    return '\n'.join(lines)


def waves(tasks, finished=()):
    """Groups of tasks that can run together: dependencies satisfied, and parallel ones batched."""
    done, result = set(finished), []
    remaining = list(tasks)
    while remaining:
        ready = [t for t in remaining if all(d in done for d in t['depends_on'])]
        if not ready:
            ready = remaining[:1]  # A dependency cycle or a missing id must not stall the team.
        batch = [t for t in ready if t['parallel']] or ready[:1]
        if len(batch) > 1 and not all(t['parallel'] for t in batch):
            batch = batch[:1]
        result.append(batch)
        for t in batch:
            done.add(t['id'])
            remaining.remove(t)
    return result


class Team:
    def __init__(self, runner, tenant_id, project_id, session_id, message_id, lead, mode, root):
        self.runner, self.tenant_id, self.project_id, self.session_id, self.message_id = runner, tenant_id, project_id, session_id, message_id
        self.lead, self.mode, self.root = lead, mode, root
        self.tokens = [0, 0]
        self.apply_lock = asyncio.Lock()  # Patches land in the lead's folder one at a time; git's index is not shared safely.

    def state(self):
        session = self.runner.store.get(self.tenant_id, 'sessions', self.session_id)
        message = next(m for m in session['messages'] if m['id'] == self.message_id)
        return session, message

    def save(self, message, session, note=None):
        self.runner.store.put(self.tenant_id, 'sessions', session)
        if note:
            self.runner.store.event(self.tenant_id, self.session_id, 'team.update', note, message_id=self.message_id)

    async def ask_lead(self, prompt):
        tried = {self.lead['id']}
        while True:
            try:
                live = lambda text: self.runner.say(self.tenant_id, self.session_id, f'[lead · {self.lead["name"]}] {text}')
                result = await self.runner.broker.invoke_agent(self.tenant_id, self.lead, prompt, 'read', self.root,
                                                               {**self.runner.protections(self.tenant_id, self.project_id), 'live': live})
                break
            except ProviderError as exc:
                # A lead that cannot run (not signed in, out of usage, its service down) hands the lead's seat to
                # another agent, so the team goes on; a lead that ran out of time has nothing to hand over.
                following = None if exc.worked or len(tried) > 2 else await self.runner.fallback(self.tenant_id, tried, self.lead['provider'])
                if following is None:
                    raise
                tried.add(following['id'])
                session, message = self.state()
                message['team']['lead'] = following['name']
                self.save(message, session, f'{self.lead["name"]} could not lead ({str(exc)[:160]}); {following["name"]} takes over.')
                self.lead = following
        self.tokens[0] += result.input_tokens or 0
        self.tokens[1] += result.output_tokens or 0
        return result.text or ''

    async def run(self, conversation_prompt, objective):
        session, message = self.state()
        await benchmark.refresh(self.runner.store.directory)
        connected = [a for a in self.runner.agents(self.tenant_id) if a.get('enabled', True)]
        # A local agent whose server is not running cannot take a task; the lead is told who is out.
        up = await localhealth.availability(connected)
        agents = [a for a in connected if up.get(a['id']) is not False]
        # A local agent costs nothing to run, so assignment counts it as free, whatever its tool's default says.
        agents = [{**a, 'cost_class': 'free'} if localhealth.server_for(a) and not a.get('cost_class') else a for a in agents]
        self.agents = agents
        self.agents = agents
        team = message['team']
        cooling = lambda mid: broker_cooling(self.runner.broker, self.tenant_id, mid)
        repo = await gitops.toplevel(self.root)
        if team.get('tasks'):
            await self.resume(agents, repo, cooling)
        else:
            team.update(status='planning', lead=self.lead['name'], agents=[a['name'] for a in agents], objective=objective)
            self.save(message, session, f'{self.lead["name"]} is planning the work.')
            listing = roster(agents, {'offline': [a for a in connected if up.get(a['id']) is False]}, self.lead)
            raw = await self.ask_lead(conversation_prompt + '\n\n' + PLAN_ASK + '\n' + listing)
            plan = normalise_plan(parse_json(raw))
            if not plan['tasks']:
                raise ProviderError(f'{self.lead["name"]} did not return a plan the team could run. Its reply: {raw[:600]}')
            ensure_review(plan['tasks'], agents, self.lead)
            if repo and self.runner.store.get(self.tenant_id, 'projects', self.project_id).get('team_adversary'):
                ensure_adversary(plan['tasks'])
            session, message = self.state()
            team = message['team']
            team.update(status='working', summary=plan['summary'], tasks=plan['tasks'])
            # Work first, then reviews, so a review knows which model families wrote what it checks.
            for task in sorted(team['tasks'], key=is_review):
                authors = {a['provider'] for t in team['tasks'] if t.get('model_id') and not is_review(t) for a in agents if a['id'] == t['model_id']}
                agent = assign(task, agents, self.lead, cooling, authors)
                task.update(model_id=agent['id'], model_name=agent['name'], cross_review=is_review(task))
            self.save(message, session, f'Planned {len(team["tasks"])} tasks.')
            for task in team['tasks']:
                self.to_board(task)
        session, message = self.state()
        team = message['team']
        finished = {t['id'] for t in team['tasks'] if t['status'] == 'done'}
        for batch in waves([t for t in team['tasks'] if t['id'] not in finished], finished):
            if repo and len(batch) > 1:
                await asyncio.gather(*(self.work(task['id'], objective, team, repo) for task in batch))
            else:
                for task in batch:
                    await self.work(task['id'], objective, team, repo)
        previous, gate = None, None
        verified = bool(repo and self.runner.store.get(self.tenant_id, 'projects', self.project_id).get('verified_delivery'))
        for round_number in range(2):
            session, message = self.state()
            team = message['team']
            team['status'] = 'reviewing'
            self.save(message, session, f'{self.lead["name"]} is reviewing the team’s work.')
            tests = await self.run_tests()
            gate = await self.verify(objective, gate) if verified else None
            verdict = parse_json(await self.ask_lead(await self.review_prompt(objective, team, repo, tests, previous, gate))) or {}
            previous = verdict
            reply = (verdict.get('reply') or '').strip()
            planned = {t['id'] for t in team['tasks'] if not t.get('adversary')}
            fixes = [f for f in (verdict.get('fixes') or []) if isinstance(f, dict) and str(f.get('task_id')) in planned and f.get('instructions')]
            vetoed = bool(gate and gate['status'] == 'NO-GO')
            if vetoed and not fixes:
                # The lead may not wave an independent NO-GO through: its findings go back to the workers as they stand.
                fixes = findings_as_fixes(gate['findings'], team['tasks'])
            if (verdict.get('verdict') == 'fix' or vetoed) and fixes and round_number == 0:
                session, message = self.state()
                team = message['team']
                team['status'] = 'fixing'
                self.save(message, session, f'{self.lead["name"]} asked for {len(fixes)} fixes.')
                for fix in fixes:
                    task = next(t for t in team['tasks'] if t['id'] == str(fix['task_id']))
                    if task.get('model_id'):
                        self.record(task['model_id'], fixes=1)
                for fix in fixes:
                    await self.work(str(fix['task_id']), objective, team, repo, fix=fix['instructions'])
                continue
            session, message = self.state()
            team = message['team']
            team['status'] = 'done'
            self.save(message, session, 'The team finished.')
            reply = reply or self.summary(team)
            if gate:
                reply += await self.deliver(gate, objective)
            if tests and not tests['passed']:
                # The lead may judge a failure unrelated, but the user is never left thinking the tests passed.
                reply += f'\n\n**Tests:** `{tests["command"]}` failed after the team\'s work. The output is in the Terminal panel.'
            return AgentResult(reply, self.tokens[0] or None, self.tokens[1] or None)
        return AgentResult(self.summary(team), self.tokens[0] or None, self.tokens[1] or None)

    async def resume(self, agents, repo, cooling):
        """Continue a stopped team: finished tasks stand, the rest run again from where their workers got to.

        A worker stopped mid-task in its own worktree had not been folded back yet, so what it had
        written is brought into the lead's folder first; the fresh worker then starts from it.
        """
        session, message = self.state()
        team = message['team']
        team['status'] = 'working'
        here = {a['id'] for a in agents}
        for task in team['tasks']:
            if task['status'] == 'done':
                continue
            try:
                worker = self.runner.store.get(self.tenant_id, 'sessions', task['session_id']) if task.get('session_id') else None
            except Exception:
                worker = None
            last = next((m for m in reversed((worker or {}).get('messages', [])) if m['role'] == 'assistant'), {})
            if repo and worker and worker.get('worktree') and last.get('checkpoint') and not task.get('merge') and not task.get('adversary'):
                try:
                    applied = await gitops.apply_between(self.root, last['checkpoint']['before'], last['checkpoint']['after'])
                    task['merge'] = 'partial work applied' if applied else None
                except gitops.GitError as exc:
                    task['merge'] = f'conflict: {str(exc)[:300]}'
            if task['model_id'] not in here:
                authors = {a['provider'] for t in team['tasks'] if not is_review(t) for a in agents if a['id'] == t.get('model_id')}
                agent = assign({**task, 'agent': None}, agents, self.lead, cooling, authors)
                task.update(model_id=agent['id'], model_name=agent['name'])
            task.update(status='pending', session_id=None, report='', rerouted=False)
        self.save(message, session, f'{self.lead["name"]}\u2019s team continues where it stopped.')
        for task in team['tasks']:
            self.to_board(task)

    def record(self, model_id, **counts):
        try:
            model = self.runner.store.get(self.tenant_id, 'models', model_id)
        except Exception:
            return  # Disconnected since; its record goes with it.
        adaptive.record_outcome(self.runner.store, self.tenant_id, model, **counts)

    def summary(self, team):
        return 'The team finished.\n\n' + '\n'.join(f'- **{t["title"]}** ({t["model_name"]}): {t["status"]}' + (f' — {t["report"][:300]}' if t.get('report') else '') for t in team['tasks'])

    async def run_tests(self):
        """The project's own tests on the integrated result, recorded in the lead's session like a check the user ran."""
        command = project_tests(self.root)
        if not command:
            return None
        session, message = self.state()
        self.save(message, session, f'Running the project’s tests: {command}.')
        # ponytail: no baseline run, so a failure that predates the team is left to the lead to recognise.
        record = await self.runner.files.run_command(self.tenant_id, self.project_id, self.session_id, command)
        return {'command': command, 'passed': record['status'] == 'completed', 'output': (record.get('output') or '')[-4000:]}

    def author_provider(self, team):
        """Whose family wrote most of the changed files, as agent-stack names it; its reviewer is the other one.
        Work by OpenCode or Gemini counts as not Claude's, so Claude reviews it."""
        providers = {a['id']: a['provider'] for a in getattr(self, 'agents', [])}
        weight = {'claude': 0, 'codex': 0}
        for t in team['tasks']:
            if not is_review(t) and not t.get('adversary'):
                weight['claude' if providers.get(t.get('model_id')) == 'claude_cli' else 'codex'] += len(t.get('changed') or []) or 1
        return max(weight, key=weight.get)

    async def verify(self, objective, previous=None):
        """Seal the integrated work and have the other model family review it. After a NO-GO the same change is
        routed again, so the reviewer rechecks its findings; after a GO it is a new change, since a GO is final for what it saw."""
        session, message = self.state()
        team = message['team']
        team['verified'] = {'status': 'sealing'}
        self.save(message, session, 'Sealing the team’s work for an independent review.')
        rounds = (previous or {}).get('round', 0) + (1 if (previous or {}).get('status') == 'GO' else 0)
        change = f'frontier-{self.message_id[:12]}' + (f'-{rounds}' if rounds else '')
        gate = {'round': rounds, 'change': change}
        try:
            if not agentstack.installed():
                raise agentstack.AgentStackError('agent-stack is not installed on this computer, so the work could not be independently reviewed.')
            if not await gitops.has_head(self.root):
                raise agentstack.AgentStackError('The repository has no commits yet; agent-stack reviews changes against one.')
            sealed = await agentstack.route(self.root, change, f'frontier-{self.session_id}', objective, self.author_provider(team))
            if sealed is None:
                gate.update(status='nothing', error='The team left no changes to review.')
            else:
                gate.update(sealed=sealed, reviewer=f'{sealed["reviewer"]["model"]} ({sealed["reviewer"]["provider"]})')
                session, message = self.state()
                message['team']['verified'] = {'status': 'reviewing', 'reviewer': gate['reviewer'], 'files': len(sealed['paths'])}
                self.save(message, session, f'{gate["reviewer"]} is reviewing {len(sealed["paths"])} changed files.')
                result = await agentstack.review(self.root, sealed)
                gate.update(status=result['verdict'], findings=result.get('findings') or [])
        except (agentstack.AgentStackError, gitops.GitError, OSError) as exc:
            gate.update(status='failed', error=str(exc))
        self.show_gate(gate, f'Independent review: {gate["status"]}.')
        return gate

    def show_gate(self, gate, note=None):
        session, message = self.state()
        message['team']['verified'] = {k: v for k, v in gate.items() if k not in ('sealed', 'round')}
        self.save(message, session, note)

    async def deliver(self, gate, objective):
        """Commit what got GO, through agent-stack, which checks it is still exactly the content reviewed."""
        if gate['status'] == 'GO':
            try:
                done = await agentstack.finish(self.root, gate['sealed'], f'{objective.splitlines()[0][:150]} (Frontier team, reviewed by {gate["reviewer"]})')
                gate.update(status='committed', commit=done['head'], files=len(gate['sealed']['paths']))
            except agentstack.AgentStackError as exc:
                gate.update(status='failed', error=str(exc))
        self.show_gate(gate)
        if gate['status'] == 'committed':
            return (f'\n\n**Verified delivery:** {gate["reviewer"]} reviewed the exact staged changes and gave GO. Committed locally as '
                    f'`{gate["commit"][:10]}` ({gate["files"]} files); nothing was pushed.')
        if gate['status'] == 'nothing':
            return '\n\n**Verified delivery:** the team left no changes, so nothing was reviewed or committed.'
        why = gate.get('error') or '\n'.join(f'- {f.get("id")} ({f.get("severity")}): {f.get("impact")}' for f in gate.get('findings') or [])
        return (f'\n\n**Not committed.** The independent review ({gate.get("reviewer") or "agent-stack"}) did not give GO '
                f'({gate["status"]}).\n{why}\nThe changes are staged in the folder for you to look at.')

    async def review_prompt(self, objective, team, repo, tests=None, previous=None, gate=None):
        reports = '\n\n'.join(f'TASK {t["id"]} — {t["title"]} — {t["model_name"]} — {t["status"]}' + (' — ADVERSARIAL CHECK' if t.get('adversary') else '')
                                + (f' (merge: {t["merge"]})' if t.get('merge') else '') + f'\n{t["report"][:3000]}' for t in team['tasks'])
        diff = ''
        if repo and team.get('checkpoint'):
            try:
                after = await gitops.checkpoint(self.root, f'Frontier: team review {self.message_id}')
                diff = await gitops.run(self.root, 'diff', '--stat', team['checkpoint'], after)
                body = await gitops.run(self.root, 'diff', team['checkpoint'], after)
                diff += '\n' + body[:40000] + ('\n… (diff truncated)' if len(body) > 40000 else '')
            except gitops.GitError as exc:
                diff = f'(diff unavailable: {exc})'
        checks = ('(the project has no test command Frontier recognises)' if tests is None else
                  f'{tests["command"]}: {"passed" if tests["passed"] else "FAILED"}\n{tests["output"]}')
        ask = REVIEW_ASK + (' ' + ADVERSARY_NOTE if any(t.get('adversary') for t in team['tasks']) else '')
        if previous:
            # The second look starts from the first: what it required, what it suspected, and what it sent back.
            earlier = json.dumps({k: previous.get(k) for k in ('requirements', 'suspicions', 'fixes') if previous.get(k)}, ensure_ascii=False)[:6000]
            ask += ('\n\nYOUR PREVIOUS REVIEW sent fixes back; the workers have made them. Confirm each fix in the code, start '
                    'with your suspicions, and recheck every requirement, not only the fixed ones. Keep every requirement in '
                    f'your new list; add one only if the request calls for it.\n{earlier}')
        independent = ''
        if gate:
            ask += ' ' + GATE_NOTE
            independent = (f'\n\nINDEPENDENT REVIEW (agent-stack, {gate.get("reviewer") or "another model family"}): {gate["status"]}'
                           + (f'\n{gate["error"]}' if gate.get('error') else '')
                           + (f'\n{json.dumps(gate["findings"], ensure_ascii=False)[:8000]}' if gate.get('findings') else ''))
        return (f'{ask}\n\nREQUEST FROM THE USER:\n{objective}\n\nPLAN:\n{team.get("summary")}\n\nREPORTS:\n{reports}\n\n'
                f'DIFF OF THE PROJECT AFTER THE TEAM WORKED:\n{diff or "(not a git repository; see the reports)"}\n\nTHE PROJECT\'S TESTS:\n{checks}{independent}')

    def update_task(self, task_id, note=None, **fields):
        """Change one task on a fresh read of the session, so parallel workers never overwrite each other."""
        session, message = self.state()
        task = next(t for t in message['team']['tasks'] if t['id'] == task_id)
        task.update(fields)
        self.save(message, session, note)
        self.to_board(task)
        return task

    def to_board(self, task):
        """Each team task is also a task on the project's board, so later sessions see what the team did."""
        status = {'working': 'doing', 'fixing': 'doing', 'done': 'done', 'failed': 'blocked'}.get(task.get('status'), 'todo')
        board.mirror(self.runner.store, self.tenant_id, self.project_id, f'team:{self.message_id}:{task["id"]}', task['title'], status,
                     notes=(task.get('report') or task.get('instructions') or '')[:600], session_id=task.get('session_id'),
                     source=f'team · {task.get("model_name") or "agent"}')

    async def work(self, task_id, objective, team, repo, fix=None):
        """One task, done by its agent in its own session, then folded back into the lead's folder."""
        task = self.update_task(task_id, status='fixing' if fix else 'working')
        self.update_task(task_id, f'{task["model_name"]} started {task["title"]}.')
        store = self.runner.store
        project = store.get(self.tenant_id, 'projects', self.project_id)
        if not task.get('session_id'):
            worker = {'id': uid(), 'project_id': self.project_id, 'name': f'{task["title"]} · {task["model_name"]}', 'messages': [], 'commands': [],
                      'created_at': now(), 'updated_at': now(), 'auto_named': False, 'team_parent': self.session_id,
                      **({'blind': True} if task.get('adversary') else {})}
            if repo:
                branch = gitops.branch_name(task['title'] + ' ' + task['model_name'], worker['id'][:6])
                location = self.runner.files.worktree_location(self.tenant_id, self.project_id, branch)
                try:
                    # The worker starts from the lead's folder as it is now, so it sees what earlier tasks have already folded back.
                    base = await gitops.checkpoint(self.root, f'Frontier: team base for {task["title"]}')
                    await gitops.worktree_add(project['root'], location, branch, copy=project.get('worktree_copy') or [], start=base)
                    worker['worktree'] = {'path': str(location), 'branch': branch}
                except gitops.GitError as exc:
                    self.update_task(task_id, f'Could not create a worktree: {exc}', status='failed', report=f'Could not create a worktree: {exc}')
                    return
            store.put(self.tenant_id, 'sessions', worker)
            task = self.update_task(task_id, session_id=worker['id'])
        reports = [t for t in self.state()[1]['team']['tasks'] if t['id'] != task_id and t.get('report')]
        if fix:
            prompt = 'The lead reviewed your work and asks for this change:\n' + fix + '\n\nOriginal task — ' + task['title'] + ':\n' + task['instructions']
        elif task.get('adversary'):
            prompt = f'{task["instructions"]}\n\nTHE USER\'S REQUEST:\n{objective}'  # No plan, no reports: it judges the work, not the story of it.
        else:
            prompt = worker_prompt(objective, team, task, reports)
        try:
            self.runner.send(self.tenant_id, self.project_id, task['session_id'], prompt, task['model_id'], self.mode)
        except (ValueError, FileNotFoundError) as exc:
            self.update_task(task_id, f'{task["title"]} could not start: {exc}', status='failed', report=str(exc))
            return
        turn = self.runner.turns.get((self.tenant_id, task['session_id']))
        if turn:
            await asyncio.gather(turn, return_exceptions=True)
        worker = store.get(self.tenant_id, 'sessions', task['session_id'])
        reply = worker['messages'][-1]
        if reply.get('model_id') and reply['model_id'] != task['model_id']:
            # The assigned agent could not run and the turn went to another; the task, the card and the track record follow it.
            task = self.update_task(task_id, f'{task["model_name"]} could not run; {reply.get("model_name")} did {task["title"]}.',
                                    model_id=reply['model_id'], model_name=reply.get('model_name'))
        fields = dict(status='done' if reply.get('status') == 'complete' else 'failed', report=(reply.get('content') or reply.get('error') or '')[:6000],
                      changed=[c['path'] for c in reply.get('changes') or []][:50])
        if repo and worker.get('worktree') and reply.get('checkpoint') and not task.get('adversary'):
            try:
                async with self.apply_lock:
                    applied = await gitops.apply_between(self.root, reply['checkpoint']['before'], reply['checkpoint']['after'])
                fields['merge'] = 'applied' if applied else 'nothing to apply'
            except gitops.GitError as exc:
                fields['merge'] = f'conflict: {str(exc)[:300]}'
        self.update_task(task_id, f'{task["model_name"]} finished {task["title"]}: {fields["status"]}.', **fields)
        if not fix and reply.get('status') in ('complete', 'failed'):
            overflow = 'context window' in (reply.get('error') or '')
            self.record(task['model_id'], **({'done': 1} if fields['status'] == 'done' else {'failed': 1, 'overflows': int(overflow)}))
        if fields['status'] == 'failed' and not fix and 'context window' in (reply.get('error') or '') and not task.get('rerouted'):
            # A local model ran out of context: the task was too big for it, so it goes once to a cloud agent.
            cloud = [a for a in getattr(self, 'agents', []) if a['id'] != task['model_id'] and not localhealth.server_for(a)]
            if cloud:
                tasks = self.state()[1]['team']['tasks']
                authors = {a['provider'] for t in tasks if t.get('model_id') and not is_review(t) for a in cloud if a['id'] == t['model_id']}
                agent = assign({**task, 'agent': None}, cloud, self.lead, lambda mid: broker_cooling(self.runner.broker, self.tenant_id, mid), authors)
                self.update_task(task_id, f'{task["model_name"]} ran out of context; {agent["name"]} takes {task["title"]} instead.',
                                 status='pending', model_id=agent['id'], model_name=agent['name'], session_id=None, rerouted=True)
                await self.work(task_id, objective, team, repo)
