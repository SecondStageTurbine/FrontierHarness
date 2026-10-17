"""Messages that arrive while a turn runs: a Claude turn takes them at its next step; otherwise they wait, and Slack is told."""
import asyncio
import sys
from urllib.parse import parse_qs

import httpx

from backend import slack_listen
from backend.agent import AgentRunner
from backend.broker import Steer, read_claude, run_cli
from tests.harness import ScriptedAgent, open_project, setup_store
from tests.test_slack_listen import Runner

# Reads the prompt, then waits for one more message on its input before reporting its result, as Claude does.
FAKE_CLAUDE = '''import json, sys
first = json.loads(sys.stdin.readline())
second = json.loads(sys.stdin.readline())
print(json.dumps({"type": "result", "result": "saw: " + second["message"]["content"]}), flush=True)
sys.stdin.read()
'''


def test_a_message_pushed_into_a_running_claude_turn_reaches_it(tmp_path):
    script = tmp_path/'fake_claude.py'
    script.write_text(FAKE_CLAUDE, encoding='utf-8')
    steer = Steer()

    async def run():
        turn = asyncio.create_task(run_cli([sys.executable, str(script)], '{"type":"user","message":{"content":"start"}}\n', str(tmp_path), steer=steer))
        while steer.stdin is None:
            await asyncio.sleep(0.05)
        assert steer.push('what is 2+2?')
        code, out, err = await asyncio.wait_for(turn, 20)
        return code, out

    code, out = asyncio.run(run())
    assert code == 0 and read_claude(out, '', code, 'claude_cli').text == 'saw: what is 2+2?'
    assert not steer.push('too late')  # The result closed the input.


def running_session(tmp_path):
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent())
    folder = tmp_path/'project'; folder.mkdir()
    project, session = open_project(store, runner, 'tenant-a', folder)
    session['messages'] = [{'id': 'u1', 'role': 'user', 'content': 'build it', 'created_at': '2026-10-16T19:00:00+00:00'},
                           {'id': 'a1', 'role': 'assistant', 'content': '', 'status': 'running', 'model_id': 'claude_cli', 'model_name': 'Claude',
                            'mode': 'auto', 'created_at': '2026-10-16T19:00:00+00:00'}]
    store.put('tenant-a', 'sessions', session)
    return store, runner, session


class Pipe:
    def __init__(self):
        self.lines = []

    def push(self, text):
        self.lines.append(text)
        return True


def test_the_running_claude_turn_takes_a_message_for_the_same_agent_only(tmp_path):
    store, runner, session = running_session(tmp_path)
    assert runner.inject('tenant-a', session['id'], 'also add docs') is None  # No open input: not a Claude turn.
    runner.steers[('tenant-a', session['id'])] = pipe = Pipe()
    assert runner.inject('tenant-a', session['id'], 'review it', 'codex_cli', 'auto') is None
    assert runner.inject('tenant-a', session['id'], '/compact') is None
    after = runner.inject('tenant-a', session['id'], 'also add docs', 'claude_cli', 'auto')
    assert [m['id'] for m in after['messages']][-1] == 'a1' and after['messages'][-2]['content'] == 'also add docs' and after['messages'][-2]['midturn']
    assert pipe.lines[0].endswith('also add docs')


def slack_session(tmp_path, steerable):
    store, runner, session = running_session(tmp_path)
    if steerable:
        runner.steers[('tenant-a', session['id'])] = Pipe()
    store.put('tenant-a', 'mcp_servers', {'id': 's', 'name': 'slack', 'env': {'SLACK_MCP_XOXP_TOKEN': 'xoxp-1'}})
    session = store.get('tenant-a', 'sessions', session['id'])
    session['slack'] = {'channel': 'C1', 'name': 'agent-chat', 'user': 'UME', 'anyone': True, 'model_id': 'claude_cli', 'mode': 'auto', 'since': '100.0',
                        'pending': {'ts': '100.0', 'user': 'UNELSON', 'message_id': 'a1'}}
    store.put('tenant-a', 'sessions', session)
    return store, runner, session


def tick_with(store, runner, session, history):
    posted, reacted = [], []

    def slack(request):
        method, params = request.url.path.rsplit('/', 1)[1], {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if method == 'chat.postMessage':
            posted.append(params)
        if method == 'reactions.add':
            reacted.append(params['timestamp'])
        return httpx.Response(200, json={'ok': True, 'messages': history if method == 'conversations.history' else []})

    async def tick():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as client:
            await slack_listen.step(store, runner, 'tenant-a', store.get('tenant-a', 'sessions', session['id']), client, {'Authorization': 'Bearer x'})

    asyncio.run(tick())
    return posted, reacted


def test_slack_mid_turn_is_handed_to_a_claude_turn_and_answered_in_its_thread(tmp_path, monkeypatch):
    store, runner, session = slack_session(tmp_path, steerable=True)
    posted, reacted = tick_with(store, runner, session, [{'ts': '105.0', 'user': 'UNELSON', 'text': 'what are you on?'}])
    s = store.get('tenant-a', 'sessions', session['id'])
    assert reacted == ['105.0'] and not posted and s['slack']['since'] == '105.0'
    assert 'slack_reply' in s['messages'][-2]['content'] and s['slack']['midturn'] == [{'ts': '105.0', 'user': 'UNELSON'}]
    sent = []

    async def call(client, headers, method, **params):
        sent.append(params)
        return {'ok': True}
    monkeypatch.setattr(slack_listen, 'call', call)
    asyncio.run(slack_listen.reply(store, 'tenant-a', s, '105.0', 'On PR 205, about 10 min.'))
    assert sent[0]['thread_ts'] == '105.0' and sent[0]['text'].endswith('<@UNELSON> On PR 205, about 10 min.')
    assert store.get('tenant-a', 'sessions', session['id'])['slack']['midturn'][0]['answered']
    try:
        asyncio.run(slack_listen.reply(store, 'tenant-a', s, '999.0', 'elsewhere'))
        raise AssertionError('an unknown thread was answered')
    except ValueError:
        pass


def test_slack_mid_turn_waits_with_one_note_when_the_turn_cannot_take_it(tmp_path):
    store, runner, session = slack_session(tmp_path, steerable=False)
    history = [{'ts': '105.0', 'user': 'UNELSON', 'text': 'what are you on?'}]
    posted, reacted = tick_with(store, runner, session, history)
    assert reacted == ['105.0'] and len(posted) == 1 and posted[0]['thread_ts'] == '105.0'
    assert '<@UNELSON> mid-turn (Claude has been working' in posted[0]['text'] and 'queued' in posted[0]['text']
    s = store.get('tenant-a', 'sessions', session['id'])
    assert s['slack']['since'] == '100.0' and s['slack']['acked'] == ['105.0']  # Still to be taken when the turn ends.
    posted, _ = tick_with(store, runner, session, history)
    assert not posted  # One note per message, not one per tick.
