"""Team mode: one agent leads, the others do the work.

The lead takes a Read-only turn to plan the work as tasks. Frontier hands each task to an agent,
the lead's choice or Adaptive's by capability, as its own session, in its own worktree when the
project is a repository, running independent tasks at the same time. Each worker's changes are
brought back as a patch, the lead reviews the reports and the diff, may send one round of fixes,
and writes the final reply. The lead never edits; the workers never see each other's transcripts.
"""
import asyncio
import json
import re

from . import adaptive, gitops
from .adaptive import ADAPTIVE, TaskRequirements
from .broker import AgentResult, ProviderError
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
    'so its instructions must stand alone.'
)
REVIEW_ASK = (
    'You are the lead. Your workers have finished; their reports and the resulting diff of the project are below. '
    'Judge whether the request is now done. Reply with ONLY a JSON object inside a ```json fence: '
    '{"verdict": "done" or "fix", "fixes": [{"task_id": "t1", "instructions": "what to change"}], '
    '"reply": "your final message to the user, in markdown, describing what was done, by whom, and anything left open"}. '
    'Ask for fixes only for real defects a worker can correct in one more pass; otherwise say done.'
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


def assign(task, agents, lead, in_cooldown=lambda model_id: False):
    """The agent for one task: the lead's explicit pick if it is connected, else the cheapest that covers the needs."""
    wanted = (task.get('agent') or '').strip().lower()
    if wanted:
        for agent in agents:
            if agent['name'].lower() == wanted or agent.get('model_name', '').lower() == wanted:
                return agent
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


def waves(tasks):
    """Groups of tasks that can run together: dependencies satisfied, and parallel ones batched."""
    done, result = set(), []
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
        result = await self.runner.broker.invoke_agent(self.tenant_id, self.lead, prompt, 'read', self.root)
        self.tokens[0] += result.input_tokens or 0
        self.tokens[1] += result.output_tokens or 0
        return result.text or ''

    async def run(self, conversation_prompt, objective):
        from .agent import build_prompt  # Late import: agent imports this module.
        session, message = self.state()
        agents = [a for a in self.runner.agents(self.tenant_id) if a.get('enabled', True)]
        team = message['team']
        team.update(status='planning', lead=self.lead['name'], agents=[a['name'] for a in agents])
        self.save(message, session, f'{self.lead["name"]} is planning the work.')
        roster = 'CONNECTED AGENTS: ' + ', '.join(a['name'] for a in agents)
        raw = await self.ask_lead(conversation_prompt + '\n\n' + PLAN_ASK + '\n' + roster)
        plan = normalise_plan(parse_json(raw))
        if not plan['tasks']:
            raise ProviderError(f'{self.lead["name"]} did not return a plan the team could run. Its reply: {raw[:600]}')
        session, message = self.state()
        team = message['team']
        team.update(status='working', summary=plan['summary'], tasks=plan['tasks'])
        for task in team['tasks']:
            agent = assign(task, agents, self.lead, lambda mid: self.runner.broker.cooldowns.get((self.tenant_id, mid), 0) > __import__('time').monotonic())
            task.update(model_id=agent['id'], model_name=agent['name'])
        self.save(message, session, f'Planned {len(team["tasks"])} tasks.')
        repo = await gitops.toplevel(self.root)
        for batch in waves(team['tasks']):
            if repo and len(batch) > 1:
                await asyncio.gather(*(self.work(task['id'], objective, team, repo) for task in batch))
            else:
                for task in batch:
                    await self.work(task['id'], objective, team, repo)
        for round_number in range(2):
            session, message = self.state()
            team = message['team']
            team['status'] = 'reviewing'
            self.save(message, session, f'{self.lead["name"]} is reviewing the team’s work.')
            verdict = parse_json(await self.ask_lead(await self.review_prompt(objective, team, repo))) or {}
            reply = (verdict.get('reply') or '').strip()
            fixes = [f for f in (verdict.get('fixes') or []) if isinstance(f, dict) and f.get('task_id') and f.get('instructions')]
            if verdict.get('verdict') == 'fix' and fixes and round_number == 0:
                session, message = self.state()
                team = message['team']
                team['status'] = 'fixing'
                self.save(message, session, f'{self.lead["name"]} asked for {len(fixes)} fixes.')
                for fix in fixes:
                    await self.work(str(fix['task_id']), objective, team, repo, fix=fix['instructions'])
                continue
            session, message = self.state()
            team = message['team']
            team['status'] = 'done'
            self.save(message, session, 'The team finished.')
            return AgentResult(reply or self.summary(team), self.tokens[0] or None, self.tokens[1] or None)
        return AgentResult(self.summary(team), self.tokens[0] or None, self.tokens[1] or None)

    def summary(self, team):
        return 'The team finished.\n\n' + '\n'.join(f'- **{t["title"]}** ({t["model_name"]}): {t["status"]}' + (f' — {t["report"][:300]}' if t.get('report') else '') for t in team['tasks'])

    async def review_prompt(self, objective, team, repo):
        reports = '\n\n'.join(f'TASK {t["id"]} — {t["title"]} — {t["model_name"]} — {t["status"]}' + (f' (merge: {t["merge"]})' if t.get('merge') else '') + f'\n{t["report"][:3000]}' for t in team['tasks'])
        diff = ''
        if repo and team.get('checkpoint'):
            try:
                after = await gitops.checkpoint(self.root, f'Frontier: team review {self.message_id}')
                diff = await gitops.run(self.root, 'diff', '--stat', team['checkpoint'], after)
                body = await gitops.run(self.root, 'diff', team['checkpoint'], after)
                diff += '\n' + body[:40000] + ('\n… (diff truncated)' if len(body) > 40000 else '')
            except gitops.GitError as exc:
                diff = f'(diff unavailable: {exc})'
        return f'{REVIEW_ASK}\n\nREQUEST FROM THE USER:\n{objective}\n\nPLAN:\n{team.get("summary")}\n\nREPORTS:\n{reports}\n\nDIFF OF THE PROJECT AFTER THE TEAM WORKED:\n{diff or "(not a git repository; see the reports)"}'

    def update_task(self, task_id, note=None, **fields):
        """Change one task on a fresh read of the session, so parallel workers never overwrite each other."""
        session, message = self.state()
        task = next(t for t in message['team']['tasks'] if t['id'] == task_id)
        task.update(fields)
        self.save(message, session, note)
        return task

    async def work(self, task_id, objective, team, repo, fix=None):
        """One task, done by its agent in its own session, then folded back into the lead's folder."""
        task = self.update_task(task_id, status='fixing' if fix else 'working')
        self.update_task(task_id, f'{task["model_name"]} started {task["title"]}.')
        store = self.runner.store
        project = store.get(self.tenant_id, 'projects', self.project_id)
        if not task.get('session_id'):
            worker = {'id': uid(), 'project_id': self.project_id, 'name': f'{task["title"]} · {task["model_name"]}', 'messages': [], 'commands': [],
                      'created_at': now(), 'updated_at': now(), 'auto_named': False, 'team_parent': self.session_id}
            if repo:
                branch = gitops.branch_name(task['title'] + ' ' + task['model_name'], worker['id'][:6])
                location = self.runner.files.worktree_location(self.tenant_id, self.project_id, branch)
                try:
                    await gitops.worktree_add(project['root'], location, branch, copy=project.get('worktree_copy') or [])
                    worker['worktree'] = {'path': str(location), 'branch': branch}
                except gitops.GitError as exc:
                    self.update_task(task_id, f'Could not create a worktree: {exc}', status='failed', report=f'Could not create a worktree: {exc}')
                    return
            store.put(self.tenant_id, 'sessions', worker)
            task = self.update_task(task_id, session_id=worker['id'])
        reports = [t for t in self.state()[1]['team']['tasks'] if t['id'] != task_id and t.get('report')]
        prompt = (('The lead reviewed your work and asks for this change:\n' + fix + '\n\nOriginal task — ' + task['title'] + ':\n' + task['instructions']) if fix
                  else worker_prompt(objective, team, task, reports))
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
        fields = dict(status='done' if reply.get('status') == 'complete' else 'failed', report=(reply.get('content') or reply.get('error') or '')[:6000],
                      changed=[c['path'] for c in reply.get('changes') or []][:50])
        if repo and worker.get('worktree') and reply.get('checkpoint'):
            try:
                async with self.apply_lock:
                    applied = await gitops.apply_between(self.root, reply['checkpoint']['before'], reply['checkpoint']['after'])
                fields['merge'] = 'applied' if applied else 'nothing to apply'
            except gitops.GitError as exc:
                fields['merge'] = f'conflict: {str(exc)[:300]}'
        self.update_task(task_id, f'{task["model_name"]} finished {task["title"]}: {fields["status"]}.', **fields)
