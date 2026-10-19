"""A session listening to Slack: a new message becomes a turn, and the finished reply goes to its thread."""
import asyncio
from urllib.parse import parse_qs

import httpx

from backend import slack_listen
from backend.store import uid
from tests.harness import setup_store, open_project


class Runner:
    def __init__(self, store):
        self.store, self.sent = store, []

    def busy(self, tenant_id, session_id):
        return False

    def agents(self, tenant_id):
        return [model for model in self.store.list(tenant_id, 'models')
                if model['provider'] in ('claude_cli', 'codex_cli', 'opencode_cli', 'gemini_cli') and model.get('enabled', True)]

    def send(self, tenant_id, project_id, session_id, content, model_id, mode):
        self.sent.append((content, model_id, mode))
        session = self.store.get(tenant_id, 'sessions', session_id)
        session['messages'] += [{'id': uid(), 'role': 'user', 'content': content}, {'id': uid(), 'role': 'assistant', 'content': '', 'status': 'running'}]
        return self.store.put(tenant_id, 'sessions', session)


def test_a_message_becomes_a_turn_and_the_reply_goes_to_its_thread(tmp_path):
    store = setup_store(tmp_path / 'state');runner = Runner(store)
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    store.put('tenant-a', 'mcp_servers', {'id': 's', 'name': 'slack', 'env': {'SLACK_MCP_XOXC_TOKEN': 'xoxc-1', 'SLACK_MCP_XOXD_TOKEN': 'xoxd-1'}})
    session['slack'] = {'channel': 'C1', 'name': 'agent-chat', 'user': 'UME', 'anyone': False, 'model_id': 'adaptive', 'mode': 'edit', 'since': '100.0'}
    store.put('tenant-a', 'sessions', session)
    posted = []
    history = [{'ts': '101.0', 'user': 'UME', 'text': 'run the tests'},
               {'ts': '102.0', 'user': 'UOTHER', 'text': 'someone else'},          # Not the user's, and anyone is off.
               {'ts': '103.0', 'user': 'UME', 'text': 'x', 'bot_id': 'B1'},         # An app's post.
               {'ts': '99.0', 'user': 'UME', 'text': 'before listening began'}]

    def slack(request):
        assert request.headers['authorization'] == 'Bearer xoxc-1' and request.headers['cookie'] == 'd=xoxd-1'
        method, params = request.url.path.rsplit('/', 1)[1], {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if method == 'chat.postMessage':
            posted.append(params)
        return httpx.Response(200, json={'ok': True, 'messages': history if method == 'conversations.history' else []})

    async def tick():
        headers = slack_listen.credentials(store, 'tenant-a')
        async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as client:
            await slack_listen.step(store, runner, 'tenant-a', store.get('tenant-a', 'sessions', session['id']), client, headers)

    asyncio.run(tick())
    assert len(runner.sent) == 1 and 'run the tests' in runner.sent[0][0]
    asyncio.run(tick())  # Still running: nothing posted, nothing new started.
    assert not posted and len(runner.sent) == 1
    s = store.get('tenant-a', 'sessions', session['id'])
    s['messages'][-1].update(status='complete', content='All 12 passed.')
    store.put('tenant-a', 'sessions', s)
    asyncio.run(tick())
    assert posted == [{'channel': 'C1', 'thread_ts': '101.0', 'text': '🤖 Frontier: All 12 passed.'}]
    asyncio.run(tick())  # Nothing newer than the handled message.
    assert len(runner.sent) == 1 and not store.get('tenant-a', 'sessions', session['id'])['slack'].get('pending')


def test_other_peoples_thread_replies_count_but_the_users_own_never_do(tmp_path):
    store = setup_store(tmp_path / 'state');runner = Runner(store)
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    store.put('tenant-a', 'mcp_servers', {'id': 's', 'name': 'slack', 'env': {'SLACK_MCP_XOXP_TOKEN': 'xoxp-1'}})
    session['slack'] = {'channel': 'C1', 'name': 'ao', 'user': 'UME', 'anyone': True, 'model_id': 'adaptive', 'mode': 'edit', 'since': '100.0'}
    store.put('tenant-a', 'sessions', session)
    posted = []
    thread = [{'ts': '50.0', 'thread_ts': '50.0', 'user': 'UME', 'text': 'old parent', 'latest_reply': '102.0'},
              {'ts': '101.0', 'thread_ts': '50.0', 'user': 'UME', 'text': 'what the agent posted as me'},
              {'ts': '102.0', 'thread_ts': '50.0', 'user': 'UNELSON', 'text': 'Nelson: please re-run the import'}]

    def slack(request):
        method, params = request.url.path.rsplit('/', 1)[1], {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if method == 'chat.postMessage':
            posted.append(params)
        return httpx.Response(200, json={'ok': True, 'messages': thread[:1] if method == 'conversations.history' else thread if method == 'conversations.replies' else []})

    async def tick():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as client:
            await slack_listen.step(store, runner, 'tenant-a', store.get('tenant-a', 'sessions', session['id']), client, slack_listen.credentials(store, 'tenant-a'))

    asyncio.run(tick())
    assert len(runner.sent) == 1 and 're-run the import' in runner.sent[0][0]
    s = store.get('tenant-a', 'sessions', session['id'])
    s['messages'][-1].update(status='complete', content='**Done**, see [the run](https://x.test/1).')
    store.put('tenant-a', 'sessions', s)
    asyncio.run(tick())
    assert posted[0]['thread_ts'] == '50.0' and posted[0]['text'] == '🤖 Frontier: <@UNELSON> *Done*, see <https://x.test/1|the run>.'
    asyncio.run(tick())
    assert len(runner.sent) == 1


def test_named_slack_model_uses_nearest_connected_sol_and_reports_substitution(tmp_path):
    store = setup_store(tmp_path / 'state'); runner = Runner(store)
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    store.delete('tenant-a', 'models', 'codex_cli')
    store.put('tenant-a', 'models', {'id': 'sol6', 'name': 'Codex Sol 6', 'provider': 'codex_cli',
                                     'model_name': 'gpt-6-sol', 'enabled': True})
    store.put('tenant-a', 'models', {'id': 'sol55', 'name': 'Codex Sol 5.5', 'provider': 'codex_cli',
                                     'model_name': 'gpt-5.5-sol', 'enabled': True})
    store.put('tenant-a', 'mcp_servers', {'id': 's', 'name': 'slack', 'env': {'SLACK_MCP_XOXP_TOKEN': 'xoxp-1'}})
    session['slack'] = {'channel': 'C1', 'name': 'ao', 'user': 'UME', 'anyone': True,
                        'model_id': 'adaptive', 'mode': 'edit', 'since': '100.0'}
    store.put('tenant-a', 'sessions', session)
    posted = []
    history = [{'ts': '101.0', 'user': 'UOTHER', 'text':
                'Run a fresh S13 review on gpt-5.6-sol from a plain Codex CLI session, then P9 the same way, or both on Claude after 12:10.'}]

    def slack(request):
        method, params = request.url.path.rsplit('/', 1)[1], {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if method == 'chat.postMessage':
            posted.append(params)
        return httpx.Response(200, json={'ok': True, 'messages': history if method == 'conversations.history' else []})

    async def tick():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as client:
            await slack_listen.step(store, runner, 'tenant-a', store.get('tenant-a', 'sessions', session['id']),
                                    client, slack_listen.credentials(store, 'tenant-a'))

    asyncio.run(tick())
    assert len(runner.sent) == 1 and runner.sent[0][1] == 'sol6'
    s = store.get('tenant-a', 'sessions', session['id'])
    s['messages'][-1].update(status='complete', content='Review complete.')
    store.put('tenant-a', 'sessions', s)
    asyncio.run(tick())
    assert 'gpt-5.6-sol' in posted[0]['text'] and 'gpt-6-sol' in posted[0]['text']


def test_named_slack_model_uses_exact_connected_model_and_plain_text_keeps_adaptive(tmp_path):
    store = setup_store(tmp_path / 'state'); runner = Runner(store)
    models = runner.agents('tenant-a')
    assert slack_listen.requested_agent('Please use gpt-5.6-sol for this review.', models)['id'] == 'codex_cli'
    assert slack_listen.requested_agent('Do not use Claude; use gpt-5.6-sol.', models)['id'] == 'codex_cli'
    assert slack_listen.requested_agent('Run the tests.', models) is None


def test_adaptive_slack_handoff_pins_codex_and_waits_for_the_review(tmp_path):
    store = setup_store(tmp_path / 'state'); runner = Runner(store)
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    store.put('tenant-a', 'mcp_servers', {'id': 's', 'name': 'slack', 'env': {'SLACK_MCP_XOXP_TOKEN': 'xoxp-1'}})
    session['slack'] = {'channel': 'C1', 'name': 'reviews', 'user': 'UME', 'anyone': True,
                        'model_id': 'adaptive', 'mode': 'edit', 'since': '100.0'}
    store.put('tenant-a', 'sessions', session)
    posted = []
    history = [{'ts': '101.0', 'user': 'UREVIEW', 'text': 'We need a Codex reviewer.'},
               {'ts': '102.0', 'user': 'UREVIEW', 'text': 'Review PR 334 and report in this thread.'}]

    def slack(request):
        method, params = request.url.path.rsplit('/', 1)[1], {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if method == 'chat.postMessage':
            posted.append(params)
        return httpx.Response(200, json={'ok': True, 'messages': history if method == 'conversations.history' else []})

    async def tick():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as client:
            await slack_listen.step(store, runner, 'tenant-a', store.get('tenant-a', 'sessions', session['id']),
                                    client, slack_listen.credentials(store, 'tenant-a'))

    asyncio.run(tick())
    pinned = store.get('tenant-a', 'sessions', session['id'])['slack']
    assert not runner.sent and pinned['model_id'] == 'codex_cli' and pinned['pinned_agent']['provider'] == 'codex_cli'
    assert posted == [{'channel': 'C1', 'thread_ts': '101.0',
                       'text': slack_listen.PREFIX + '<@UREVIEW> Switched this Slack conversation to Codex (gpt-5.6-sol). It is ready; send the review.'}]
    asyncio.run(tick())
    assert len(runner.sent) == 1 and runner.sent[0][1] == 'codex_cli' and 'Review PR 334' in runner.sent[0][0]


def test_routed_reviewer_assignment_pins_opus_and_runs_the_same_message(tmp_path):
    store = setup_store(tmp_path / 'state'); runner = Runner(store)
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    store.put('tenant-a', 'models', {'id': 'opus', 'name': 'Claude Opus', 'model_name': 'Opus',
                                    'provider': 'claude_cli', 'enabled': True})
    store.put('tenant-a', 'mcp_servers', {'id': 's', 'name': 'slack', 'env': {'SLACK_MCP_XOXP_TOKEN': 'xoxp-1'}})
    session['slack'] = {'channel': 'C1', 'name': 'reviews', 'user': 'UME', 'anyone': True,
                        'model_id': 'adaptive', 'mode': 'edit', 'since': '100.0'}
    store.put('tenant-a', 'sessions', session)
    text = ('opynTech-platform PR 440 is routed to OpynBuildReviewer (Opus) for first review. '
            'One fix first: edit the PR body only, then reply here when done.')

    def slack(request):
        method = request.url.path.rsplit('/', 1)[1]
        return httpx.Response(200, json={'ok': True, 'messages': [{'ts': '101.0', 'user': 'UREVIEW', 'text': text}]
                                         if method == 'conversations.history' else []})

    async def tick():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as client:
            await slack_listen.step(store, runner, 'tenant-a', store.get('tenant-a', 'sessions', session['id']),
                                    client, slack_listen.credentials(store, 'tenant-a'))

    asyncio.run(tick())
    pinned = store.get('tenant-a', 'sessions', session['id'])['slack']
    assert pinned['model_id'] == 'opus' and pinned['pinned_agent']['id'] == 'opus'
    assert len(runner.sent) == 1 and runner.sent[0][1] == 'opus' and text in runner.sent[0][0]


def test_only_explicit_reviewer_handoffs_pin_an_agent(tmp_path):
    store = setup_store(tmp_path / 'state'); runner = Runner(store); models = runner.agents('tenant-a')
    assert slack_listen.requested_handoff('We need a Codex reviewer.', models)['id'] == 'codex_cli'
    assert slack_listen.requested_handoff('Please switch this conversation to Claude.', models)['provider'] == 'claude_cli'
    assert slack_listen.requested_handoff('The writer used Codex for this change.', models) is None
