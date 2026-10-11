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
