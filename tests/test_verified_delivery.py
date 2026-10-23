"""Verified delivery: a team's work is reviewed by agent-stack and committed only on its GO."""
import asyncio
import subprocess
from pathlib import Path
from backend import agentstack
from backend.agent import AgentRunner
from backend.broker import AgentResult
from backend.team import findings_as_fixes
from tests.harness import ScriptedAgent, open_project, setup_store
from tests.test_batch12 import repo, git

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


def test_the_review_loop_keeps_fixing_until_the_lead_approves(tmp_path, monkeypatch):
    rounds, finished, counter = [], [], {'n': 0, 'fixes': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            rounds.append(prompt)
            if len(rounds) >= 3:
                return AgentResult('{"verdict": "done", "fixes": [], "reply": "Done."}', 5, 5)
            return AgentResult('{"verdict": "fix", "fixes": [{"task_id": "a", "instructions": "Adjust a.txt."}], "reply": ""}', 5, 5)
        if 'The lead reviewed your work' in prompt:
            counter['fixes'] += 1
            counter['n'] += 1
            (Path(root)/'a.txt').write_text(f'A{counter["n"]}', encoding='utf-8')
            return AgentResult('Adjusted a.txt.', 1, 1)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'change': change, 'reviewer': {'provider': 'codex', 'model': 'gpt-6-sol'}}
    async def review(root, sealed):
        return {'verdict': 'GO', 'findings': []}
    async def finish(root, sealed, message):
        finished.append(sealed['change']); return {'head': 'abcdef1234567890'}
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert len(rounds) == 3 and counter['fixes'] == 2  # Two fix rounds, approved on the third review.
    assert reply['team']['review_round'] == 3 and reply['team']['review_exit'] == 'done'
    assert len(finished) == 1 and 'Committed locally as `abcdef1234`' in reply['content']


def test_the_review_loop_stops_at_the_round_cap_without_committing(tmp_path, monkeypatch):
    seen = {'n': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)  # The lead would wave the NO-GO through.
        if 'The lead reviewed your work' in prompt:
            seen['n'] += 1
            (Path(root)/'a.txt').write_text(f'A{seen["n"]}', encoding='utf-8')
            return AgentResult('Adjusted a.txt.', 1, 1)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'reviewer': {'provider': 'codex', 'model': 'gpt-6-sol'}}
    async def review(root, sealed):
        seen['n'] += 1  # A fresh finding id each round, so only the cap can stop the loop.
        return {'verdict': 'NO-GO', 'findings': [{**FINDING, 'id': f'F{seen["n"]}'}]}
    async def finish(*args):
        raise AssertionError('nothing may be committed without GO')
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True, 'team_max_rounds': 2})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['review_exit'] == 'cap' and reply['team']['review_round'] == 2
    assert '**Not committed.**' in reply['content'] and '2-round review limit' in reply['content']


def test_the_review_loop_stalls_when_the_gate_repeats_the_same_findings(tmp_path, monkeypatch):
    fixes_seen = {'n': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        if 'The lead reviewed your work' in prompt:
            fixes_seen['n'] += 1
            (Path(root)/'a.txt').write_text(f'A{fixes_seen["n"]}', encoding='utf-8')
            return AgentResult('Adjusted a.txt.', 1, 1)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'reviewer': {'provider': 'codex', 'model': 'gpt-6-sol'}}
    async def review(root, sealed):
        return {'verdict': 'NO-GO', 'findings': [FINDING]}  # The very same finding every round.
    async def finish(*args):
        raise AssertionError('nothing may be committed without GO')
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['review_exit'] == 'stalled' and reply['team']['review_round'] == 2
    assert '**Not committed.**' in reply['content'] and 'review loop stalling' in reply['content']
    assert fixes_seen['n'] == 1  # One fix round was tried before the repeat was caught.


def test_the_review_loop_stalls_when_a_fix_round_changes_nothing(tmp_path):
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "fix", "fixes": [{"task_id": "a", "instructions": "Tweak a.txt."}], "reply": ""}', 5, 5)
        if 'The lead reviewed your work' in prompt:
            return AgentResult('Nothing to change.', 1, 1)  # The fix worker touches no file.
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['review_exit'] == 'stalled' and reply['team']['review_round'] == 1
    assert '**Not committed.**' in reply['content'] and 'review loop stalling' in reply['content']


def test_a_cap_exit_still_reports_failing_tests(tmp_path):
    counter = {'n': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "fix", "fixes": [{"task_id": "a", "instructions": "Make the test pass."}], "reply": ""}', 5, 5)
        if 'The lead reviewed your work' in prompt:
            counter['n'] += 1
            (Path(root)/'a.txt').write_text(f'X{counter["n"]}', encoding='utf-8')  # Changes the file, but never to 'Z'.
            return AgentResult('Adjusted a.txt.', 1, 1)
        (Path(root)/'a.txt').write_text('X', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    (root/'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    (root/'test_a.py').write_text("from pathlib import Path\ndef test_a():\n    assert Path('a.txt').read_text() == 'Z'\n", encoding='utf-8')
    git(root, 'add', '-A'); git(root, 'commit', '-q', '-m', 'tests')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**store.get('tenant-a', 'projects', project['id']), 'team_max_rounds': 2})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['review_exit'] == 'cap'
    assert '**Tests:**' in reply['content'] and 'failed' in reply['content']


def test_a_failed_independent_review_exits_at_once_without_a_second_review(tmp_path, monkeypatch):
    calls = {'review': 0, 'route': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        calls['route'] += 1
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'reviewer': {'provider': 'claude', 'model': 'claude-fable-5'}}
    async def review(root, sealed):
        calls['review'] += 1
        raise agentstack.AgentStackError('The review did not return a verdict: timed out')
    async def finish(*args):
        raise AssertionError('nothing may be committed without GO')
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert calls['review'] == 1 and calls['route'] == 1  # A broken gate is never retried inside the loop.
    assert reply['team']['review_exit'] == 'review-failed' and reply['team']['review_round'] == 1
    assert '**Not committed.**' in reply['content'] and 'timed out' in reply['content']


def test_with_no_independent_gate_the_lead_cycles_fixes_until_it_says_done(tmp_path):
    rounds, counter = [], {'n': 0, 'fixes': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            rounds.append(prompt)
            if len(rounds) >= 3:
                return AgentResult('{"verdict": "done", "fixes": [], "reply": "Done."}', 5, 5)
            return AgentResult('{"verdict": "fix", "fixes": [{"task_id": "a", "instructions": "Adjust a.txt."}], "reply": ""}', 5, 5)
        if 'The lead reviewed your work' in prompt:
            counter['fixes'] += 1
            counter['n'] += 1
            (Path(root)/'a.txt').write_text(f'A{counter["n"]}', encoding='utf-8')
            return AgentResult('Adjusted a.txt.', 1, 1)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert len(rounds) == 3 and counter['fixes'] == 2  # Two fix rounds, then the lead approves on its own.
    assert reply['team']['review_round'] == 3 and reply['team']['review_exit'] == 'done'
    assert 'verified' not in reply['team']  # No independent gate ran at all.
    assert (root/'a.txt').read_text(encoding='utf-8') == 'A2'


def test_the_review_loop_stalls_when_no_one_names_an_actionable_fix(tmp_path, monkeypatch):
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            # The lead asks for a fix on a task that does not exist, so nothing actionable comes back.
            return AgentResult('{"verdict": "fix", "fixes": [{"task_id": "ghost", "instructions": "Fix the unfixable."}], "reply": ""}', 5, 5)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'reviewer': {'provider': 'codex', 'model': 'gpt-6-sol'}}
    async def review(root, sealed):
        return {'verdict': 'NO-GO', 'findings': []}  # A veto with nothing itemised to send back to the workers.
    async def finish(*args):
        raise AssertionError('nothing may be committed without GO')
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'verified_delivery': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['review_exit'] == 'stalled' and reply['team']['review_round'] == 1
    assert '**Not committed.**' in reply['content'] and 'review loop stalling' in reply['content']


def test_the_gate_approver_flow_runs_the_gate_every_round_and_commits_only_on_a_counting_go(tmp_path, monkeypatch):
    rounds, finished, asked, verdicts = [], [], [], ['NO-GO', 'GO']
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "agent": "Claude", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            rounds.append(prompt)
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)  # The lead would approve on its own every round.
        (Path(root)/'a.txt').write_text('A\n' if 'The lead reviewed your work' in prompt else 'A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        asked.append(reviewer)
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'change': change,
                'reviewer': {'provider': 'codex', 'model': 'gpt-6-sol'}}
    async def review(root, sealed):
        verdict = verdicts.pop(0)
        return {'verdict': verdict, 'findings': [FINDING] if verdict == 'NO-GO' else []}
    async def finish(root, sealed, message):
        finished.append(sealed['change']); return {'head': 'abcdef1234567890'}
    monkeypatch.setattr('backend.team.ensure_review', lambda *a: None)
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'gate_approver': True})  # verified_delivery left off.
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert len(rounds) == 2  # The lead's round-1 done did not exit; the NO-GO forced a fix round.
    assert reply['team']['review_exit'] == 'done' and reply['team']['review_round'] == 2
    assert len(asked) == 2 and all(r is not None for r in asked)  # An explicit reviewer is always passed, so the review runs at max effort.
    assert asked[0] == ('codex', 'gpt-5.6-sol')  # The writer is Claude/sonnet; the eligible reviewer is the other family.
    assert len(finished) == 1 and 'Committed locally as `abcdef1234`' in reply['content']
    assert reply['team']['verified']['reviewer_model'] == 'gpt-6-sol' and reply['team']['verified']['same_family'] is False
    assert reply['team']['verified']['counts'] is True


def test_a_go_from_the_writer_s_own_model_is_discarded_and_the_change_rereviewed_once(tmp_path, monkeypatch):
    asked, finished, calls = [], [], {'route': 0, 'review': 0}
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "agent": "Claude", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/'a.txt').write_text('A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        calls['route'] += 1; asked.append((change, reviewer))
        # agent-stack overrode the requested reviewer with the writer's own model on the first pass.
        provider, model = ('claude', 'sonnet') if calls['route'] == 1 else ('codex', 'gpt-6-sol')
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'change': change,
                'reviewer': {'provider': provider, 'model': model}}
    async def review(root, sealed):
        calls['review'] += 1
        return {'verdict': 'GO', 'findings': []}
    async def finish(root, sealed, message):
        finished.append(sealed['change']); return {'head': 'abcdef1234567890'}
    monkeypatch.setattr('backend.team.ensure_review', lambda *a: None)
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'gate_approver': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert calls['route'] == 2 and calls['review'] == 2  # The same-model GO was discarded and the change re-reviewed once.
    assert asked[0][0] == asked[1][0]  # The same sealed change was re-routed.
    assert asked[0][1] == asked[1][1] == ('codex', 'gpt-5.6-sol')  # Never the writer's own model was requested.
    assert reply['team']['review_exit'] == 'done' and len(finished) == 1
    assert reply['team']['verified']['reviewer_model'] == 'gpt-6-sol'


def test_the_gate_approver_flow_stalls_when_no_reviewer_is_eligible(tmp_path, monkeypatch):
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "Two steps.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "agent": "Claude", "parallel": true}, '
                               '{"id": "b", "title": "Write B", "instructions": "Create b.txt.", "agent": "Codex", "parallel": true}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/('b.txt' if 'YOUR TASK — Write B' in prompt else 'a.txt')).write_text('B' if 'YOUR TASK — Write B' in prompt else 'A', encoding='utf-8')
        return AgentResult('Wrote a file.', 1, 1)
    async def route(*args, **kwargs):
        raise AssertionError('no route should happen when no reviewer is eligible')
    monkeypatch.setattr('backend.team.ensure_review', lambda *a: None)
    for name, fake in (('installed', lambda: True), ('route', route), ('review', lambda *a: None), ('finish', lambda *a: None)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'gate_approver': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt and b.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['team']['review_exit'] == 'stalled' and reply['team']['review_round'] == 1
    assert 'no reviewer model is eligible' in reply['content'].lower()
    assert 'sonnet' in reply['content'] and 'gpt-5.6-sol' in reply['content']  # The writer models are named.


def test_the_gate_approver_flow_records_a_same_family_counting_go(tmp_path, monkeypatch):
    from backend.schemas import ModelConfig
    asked, finished = [], []
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "Two steps.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "agent": "Claude", "parallel": true}, '
                               '{"id": "b", "title": "Write B", "instructions": "Create b.txt.", "agent": "Codex", "parallel": true}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/('b.txt' if 'YOUR TASK — Write B' in prompt else 'a.txt')).write_text('B' if 'YOUR TASK — Write B' in prompt else 'A', encoding='utf-8')
        return AgentResult('Wrote a file.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer=None):
        asked.append(reviewer)
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt', 'b.txt'], 'change': change,
                'reviewer': {'provider': 'claude', 'model': 'opus'}}
    async def review(root, sealed):
        return {'verdict': 'GO', 'findings': []}
    async def finish(root, sealed, message):
        finished.append(sealed['change']); return {'head': 'abcdef1234567890'}
    monkeypatch.setattr('backend.team.ensure_review', lambda *a: None)
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    opus = ModelConfig(name='Claude Opus', provider='claude_cli', model_name='opus')
    store.put('tenant-a', 'models', {'id': 'claude-opus', **opus.model_dump(exclude={'api_key'}), 'encrypted_key': None, 'key_hint': 'Subscription login', 'status': 'untested'})
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**project, 'gate_approver': True})
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt and b.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert asked[0] == ('claude', 'opus')  # Both families wrote; the only eligible reviewer is a different model in the writer's family.
    assert reply['team']['review_exit'] == 'done' and reply['team']['review_round'] == 1 and len(finished) == 1
    assert reply['team']['verified']['same_family'] is True and reply['team']['verified']['reviewer_model'] == 'opus'
    assert 'same family as the authors' in reply['content'] and 'weaker check' in reply['content']
