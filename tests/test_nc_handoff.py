"""After a gate_approver team commits, the handoff packet goes to NC's Slack channel exactly once per commit."""
import asyncio
from pathlib import Path

import pytest

from backend import agentstack, slack_listen
from backend.agent import AgentRunner
from backend.broker import AgentResult
from tests.harness import ScriptedAgent, open_project, setup_store
from tests.test_batch12 import repo, git
from tests.test_verified_delivery import FINDING


def team_run(tmp_path, monkeypatch, verdicts, settings, reviewer=('codex', 'gpt-6-sol'), failing_tests=False, slack_error=None):
    """One team run against stubbed agent-stack and a stubbed slack_listen.call; returns what the test needs to look at."""
    posts = []
    async def respond(config, prompt, mode, root):
        if '"tasks"' in prompt and 'REPORTS:' not in prompt:
            return AgentResult('{"summary": "One step.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt.", "agent": "Claude", "parallel": false}]}', 5, 5)
        if 'REPORTS:' in prompt:
            return AgentResult('{"verdict": "done", "reply": "Done."}', 5, 5)
        (Path(root)/'a.txt').write_text('A\n' if 'The lead reviewed your work' in prompt else 'A', encoding='utf-8')
        return AgentResult('Wrote a.txt.', 1, 1)
    async def route(root, change, session, reason, author, refs=None, reviewer_=None, **kw):
        return {'routeToken': 't', 'reviewPacketPath': 'p', 'baseline': {'token': 'b'}, 'paths': ['a.txt'], 'change': change,
                'reviewer': {'provider': reviewer[0], 'model': reviewer[1]}}
    async def review(root, sealed):
        verdict = verdicts.pop(0) if verdicts else 'NO-GO'
        if verdict == 'failed':
            raise agentstack.AgentStackError('agent-stack crashed')
        return {'verdict': verdict, 'findings': [FINDING] if verdict == 'NO-GO' else []}
    async def finish(root, sealed, message):
        git(root, 'add', 'a.txt'); git(root, 'commit', '-q', '-m', message)
        return {'head': git(root, 'rev-parse', 'HEAD').strip()}
    async def call(client, headers, method, **params):
        posts.append((method, params))
        if slack_error[0] if slack_error else None:
            raise ValueError(f'Slack {method}: {slack_error[0]}')
        return {'ok': True, 'ts': '1.2'}
    monkeypatch.setattr('backend.team.ensure_review', lambda *a: None)
    for name, fake in (('installed', lambda: True), ('route', route), ('review', review), ('finish', finish)):
        monkeypatch.setattr(agentstack, name, fake)
    monkeypatch.setattr(slack_listen, 'call', call)
    monkeypatch.setattr(slack_listen, 'credentials', lambda store, tenant_id: {})
    store = setup_store(tmp_path/'state'); root = repo(tmp_path)
    if failing_tests:
        (root/'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        (root/'test_a.py').write_text("from pathlib import Path\ndef test_a():\n    assert Path('a.txt').read_text() == 'Z'\n", encoding='utf-8')
        git(root, 'add', '-A'); git(root, 'commit', '-q', '-m', 'tests')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    store.put('tenant-a', 'projects', {**store.get('tenant-a', 'projects', project['id']), **settings})
    def turn(start):
        async def scenario():
            start()
            await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
        asyncio.run(scenario())
        return store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    reply = turn(lambda: runner.send('tenant-a', project['id'], session['id'], 'Create a.txt.\nMore detail.', 'claude_cli', 'edit', team=True))
    assert reply['status'] == 'complete', reply.get('error')
    cont = lambda: turn(lambda: runner.continue_team('tenant-a', project['id'], session['id']))
    return reply, posts, root, store, session, cont  # Every Slack call made, so anything but a post shows up too.


def test_a_committed_approver_run_posts_one_packet_and_a_continue_never_posts_it_again(tmp_path, monkeypatch):
    reply, posts, root, store, session, cont = team_run(tmp_path, monkeypatch, ['NO-GO', 'GO'], {'gate_approver': True, 'nc_channel': 'CNC123'})
    head = git(root, 'rev-parse', 'HEAD').strip()
    assert len(posts) == 1 and posts[0][1]['channel'] == 'CNC123'
    text = posts[0][1]['text']
    assert 'Ready for PR/CI/merge:* Create a.txt.' in text and 'More detail' not in text
    assert '*Branch:* main' in text and f'`{head}`' in text
    assert 'gpt-6-sol (codex), counting GO on round 2 of 2' in text and 'Cross-family review' in text
    assert '1 raised, 1 fixed' in text and 'no open findings' in text and '*Tests:* none configured' in text
    assert reply['team']['handoff']['commit'] == head and reply['team']['handoff']['state'] == 'posted' and reply['team']['handoff']['ts']
    assert 'Handed to NC' in reply['content']
    # A run that stopped after the post is continued: the posted marker is terminal, so NC gets nothing twice.
    whole = store.get('tenant-a', 'sessions', session['id'])
    whole['messages'][-1]['status'] = 'interrupted'
    store.put('tenant-a', 'sessions', whole)
    again = cont()
    assert again['status'] == 'complete', again.get('error')
    assert len(posts) == 1 and 'already posted' in again['content'] and git(root, 'rev-parse', 'HEAD').strip() == head


def test_a_failed_post_keeps_the_commit_and_one_continue_retries_it(tmp_path, monkeypatch):
    error = ['not_in_channel']
    reply, posts, root, store, session, cont = team_run(tmp_path, monkeypatch, ['GO'], {'gate_approver': True, 'nc_channel': 'CNC123'}, slack_error=error)
    head = git(root, 'rev-parse', 'HEAD').strip()
    assert reply['status'] == 'complete' and reply['team']['review_exit'] == 'done'
    assert f'Committed as `{head}`' in reply['content'] and 'NC handoff failed' in reply['content'] and 'not_in_channel' in reply['content']
    assert reply['team']['handoff'] == {'commit': head, 'state': 'failed', 'error': 'Slack chat.postMessage: not_in_channel'}
    assert [m for m, _ in posts] == ['chat.postMessage']  # No conversations.join or anything else on the Slack side.
    error[0] = None
    again = cont()
    assert again['status'] == 'complete', again.get('error')
    assert len(posts) == 2 and again['team']['handoff']['state'] == 'posted' and again['team']['handoff']['commit'] == head
    assert git(root, 'rev-parse', 'HEAD').strip() == head  # Nothing was reviewed or committed again.
    with pytest.raises(ValueError):
        cont()  # Posted is terminal: a finished team cannot be continued any more.


def test_no_nc_channel_means_no_post_and_the_reply_says_the_handoff_was_skipped(tmp_path, monkeypatch):
    reply, posts, *_ = team_run(tmp_path, monkeypatch, ['GO'], {'gate_approver': True})
    assert posts == [] and 'NC handoff skipped' in reply['content'] and 'handoff' not in reply['team']


@pytest.mark.parametrize('verdicts,settings,exit', [
    (['GO'], {'verified_delivery': True}, 'done'),  # Committed, but not the approver flow.
    (['NO-GO'], {'gate_approver': True, 'team_max_rounds': 1}, 'cap'),
    (['NO-GO', 'NO-GO'], {'gate_approver': True}, 'stalled'),
    (['failed'], {'gate_approver': True}, 'review-failed'),
])
def test_only_a_committed_approver_run_posts(tmp_path, monkeypatch, verdicts, settings, exit):
    reply, posts, *_ = team_run(tmp_path, monkeypatch, verdicts, {**settings, 'nc_channel': 'CNC123'})
    assert reply['team']['review_exit'] == exit and posts == [] and 'handoff' not in reply['team']


def test_the_packet_flags_a_same_family_review_and_failing_tests(tmp_path, monkeypatch):
    reply, posts, *_ = team_run(tmp_path, monkeypatch, ['GO'], {'gate_approver': True, 'nc_channel': 'CNC123'}, reviewer=('claude', 'opus'), failing_tests=True)
    assert reply['team']['verified']['same_family'] is True and len(posts) == 1
    text = posts[0][1]['text']
    assert 'Same-family review:* opus' in text and 'Cross-family' not in text
    assert '*Tests:* FAILED (' in text
