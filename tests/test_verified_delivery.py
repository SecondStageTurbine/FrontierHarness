"""Verified delivery: a team's work is reviewed by agent-stack and committed only on its GO."""
import asyncio
import subprocess
from pathlib import Path
from backend import agentstack
from backend.agent import AgentRunner
from backend.broker import AgentResult
from backend.team import findings_as_fixes
from tests.harness import ScriptedAgent, open_project, setup_store
from tests.test_batch12 import repo

FINDING = {'id': 'F1', 'severity': 'P1', 'category': 'correctness', 'evidence': 'a.txt says A only', 'impact': 'a.txt lacks the newline',
           'requiredChange': 'End a.txt with a newline', 'closingProbe': 'a.txt ends with \\n', 'affectedPaths': ['a.txt'], 'verificationTargets': []}


def test_a_no_go_goes_back_to_the_workers_even_when_the_lead_says_done_and_only_a_go_is_committed(tmp_path, monkeypatch):
    prompts, verdicts, finished = [], ['NO-GO', 'GO'], []
    async def respond(config, prompt, mode, root):
        prompts.append(prompt)
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)  # The lead would let the NO-GO through.
        (Path(root)/'a.txt').write_text('A\n' if 'independent review found' in prompt else 'A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'change': change,
                'reviewer': {'provider': 'codex', 'model': 'gpt-6-sol'}}
    async def review(root, sealed):
        verdict = verdicts.pop(0)
        return {'verdict': verdict, 'findings': [FINDING] if verdict == 'NO-GO' else []}
    async def finish(root, sealed, message):
        finished.append((sealed['change'], message))
        return {'head': 'abcdef1234567890'}
    monkeypatch.setattr(agentstack, 'installed', lambda: True)
    monkeypatch.setattr(agentstack, 'route', route)
    monkeypatch.setattr(agentstack, 'review', review)
    monkeypatch.setattr(agentstack, 'finish', finish)
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert any('End a.txt with a newline' in p for p in prompts if 'independent review found' in p)  # The worker got the finding.
    assert any('INDEPENDENT REVIEW (agent-stack, gpt-6-sol (codex)): NO-GO' in p for p in prompts)  # The lead saw it.
    assert (root/'a.txt').read_text(encoding='utf-8') == 'A\n'
    assert len(finished) == 1 and finished[0][0] == f'frontier-{reply["id"][:12]}'  # The same change rechecked after NO-GO.
    assert 'Committed locally as `abcdef1234`' in reply['content']
    assert reply['team']['verified']['status'] == 'committed' and 'sealed' not in reply['team']['verified']


def test_a_review_that_never_returns_go_is_not_committed(tmp_path, monkeypatch):
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'reviewer': {'provider': 'claude', 'model': 'claude-fable-5'}}
    async def review(root, sealed):
        raise agentstack.AgentStackError('The review did not return a verdict: timed out')
    async def finish(*args):
        raise AssertionError('nothing may be committed without GO')
    monkeypatch.setattr(agentstack, 'installed', lambda: True)
    monkeypatch.setattr(agentstack, 'route', route)
    monkeypatch.setattr(agentstack, 'review', review)
    monkeypatch.setattr(agentstack, 'finish', finish)
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert '**Not committed.**' in reply['content'] and 'timed out' in reply['content']
    assert reply['team']['verified']['status'] == 'failed'


def test_findings_go_to_the_task_that_changed_the_file():
    tasks = [{'id': 'a', 'title': 'Write A', 'changed': ['a.txt']}, {'id': 'b', 'title': 'Write B', 'changed': ['b.txt']},
             {'id': 'r', 'title': 'Review the work', 'needs': ['review']}]
    fixes = findings_as_fixes([{**FINDING, 'affectedPaths': ['b.txt']}, {**FINDING, 'id': 'F2', 'affectedPaths': ['elsewhere.txt']}], tasks)
    assert {f['task_id'] for f in fixes} == {'a', 'b'} and 'F2' in next(f for f in fixes if f['task_id'] == 'a')['instructions']


def test_route_points_a_missing_reference_kind_at_a_changed_test_and_keeps_its_own_files_out(tmp_path, monkeypatch):
    root = repo(tmp_path)
    (root/'app.py').write_text('x = 1\n', encoding='utf-8')
    (root/'test_app.py').write_text('def test_x(): pass\n', encoding='utf-8')
    (root/'.agent-stack').mkdir(); (root/'.agent-stack'/'run.json').write_text('{}', encoding='utf-8')
    calls = []
    async def call(entry, args, cwd, timeout=300):
        calls.append(args)
        if '--decision-ref' not in args:
            return 2, '', 'reviewed route authoritative reference minimum incomplete: no applicable decision contract; add --decision-ref\n'
        return 0, '{"routeToken": "t", "reviewPacketPath": "p", "baseline": {"token": "b"}, "reviewer": {"provider": "claude", "model": "claude-fable-5"}}\n', ''
    monkeypatch.setattr(agentstack, 'call', call)
    sealed = asyncio.run(agentstack.route(root, 'frontier-x', 'frontier-s', 'Add x', 'codex'))
    assert sorted(sealed['paths']) == ['app.py', 'test_app.py']  # Staged, without agent-stack's own run files.
    assert calls[-1][calls[-1].index('--decision-ref')+1] == 'app.py' and len(calls) == 2
    staged = subprocess.run(['git', 'diff', '--cached', '--name-only'], cwd=root, capture_output=True, text=True).stdout.split()
    assert '.agent-stack/run.json' not in staged


def test_routing_skips_a_subscription_whose_window_is_spent_until_it_resets(tmp_path, monkeypatch):
    from backend import maintenance
    from backend.broker import ModelBroker, cooling
    store = setup_store(tmp_path/'state')
    broker = ModelBroker(store)
    monkeypatch.setattr(maintenance, 'LIMITS_CACHE', {})
    assert not cooling(broker, 'tenant-a', 'claude_cli')  # Nothing read yet: nothing assumed.
    maintenance.LIMITS_CACHE[('claude_cli', 'None')] = (0, {'error': None, 'limits': [
        {'label': 'Current session', 'percent': 100.0, 'resets_at': '2999-01-01T00:00:00+00:00'}]})
    assert cooling(broker, 'tenant-a', 'claude_cli')
    maintenance.LIMITS_CACHE[('claude_cli', 'None')][1]['limits'][0]['resets_at'] = '2000-01-01T00:00:00+00:00'
    assert not cooling(broker, 'tenant-a', 'claude_cli')  # The window has reset since it was read.


def test_claude_written_work_goes_to_fable_and_the_reply_says_it_was_a_same_family_review(tmp_path, monkeypatch):
    from backend.team import Team
    asked = {}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        asked['reviewer'] = reviewer
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'],
                'reviewer': {'provider': 'claude', 'model': 'claude-fable-5', 'assurance': 'operator-selected-same-provider'}}
    async def review(root, sealed):
        return {'verdict': 'GO', 'findings': []}
    async def finish(root, sealed, message):
        return {'head': 'abcdef1234567890'}
    monkeypatch.setattr(Team, 'author_provider', lambda self, team: 'claude')
    monkeypatch.setattr(agentstack, 'CLAUDE_WORK_REVIEWER', ('claude', 'claude-fable-5'))
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert asked['reviewer'] == ('claude', 'claude-fable-5')
    assert 'same family as the authors' in reply['content'] and 'weaker check' in reply['content']


def test_an_edit_files_turn_is_told_it_has_the_network():
    # Codex runs Edit files with network_access=true; telling the agent otherwise made it refuse web pages.
    from backend.agent import POSTURE
    assert 'use the network' in POSTURE['edit'] and 'network are off limits' not in POSTURE['edit']
