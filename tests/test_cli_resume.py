"""Resuming a Claude or Codex conversation from the command line: the picker, and the tool's own session continued natively."""
import asyncio
import json
from pathlib import Path
from backend import maintenance
from backend.agent import AgentRunner
from backend.broker import AgentResult, ProviderError, agent_argv
from tests.harness import ScriptedAgent, setup_store, turn


def cli_history(home, root):
    claude = home/'.claude'/'projects'/maintenance.claude_slug(root); claude.mkdir(parents=True)
    (claude/'c-uuid.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'type': 'user', 'entrypoint': 'cli', 'timestamp': '2026-05-01T10:00:00Z', 'message': {'role': 'user', 'content': 'Fix the widget please.'}},
        {'type': 'assistant', 'timestamp': '2026-05-01T10:00:05Z', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Fixed it.'}]}},
    ]), encoding='utf-8')
    # Frontier's own `claude -p` turns land in the same folder; they are not conversations to resume.
    (claude/'frontier-turn.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'type': 'user', 'entrypoint': 'sdk-cli', 'timestamp': '2026-05-03T10:00:00Z', 'message': {'role': 'user', 'content': 'The conversation so far follows.'}},
        {'type': 'assistant', 'timestamp': '2026-05-03T10:00:05Z', 'message': {'role': 'assistant', 'content': 'ok'}},
    ]), encoding='utf-8')
    codex = home/'.codex'/'sessions'/'2026'/'05'/'02'; codex.mkdir(parents=True)
    for name, originator, stamp in (('rollout-a.jsonl', 'codex_cli_rs', '2026-05-02'), ('rollout-exec.jsonl', 'codex_exec', '2026-05-04')):
        (codex/name).write_text('\n'.join(json.dumps(r) for r in [
            {'type': 'session_meta', 'payload': {'id': f'x-{originator}', 'cwd': str(root), 'originator': originator, 'timestamp': f'{stamp}T09:00:00Z'}},
            {'type': 'response_item', 'timestamp': f'{stamp}T09:00:02Z', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Add a test.'}]}},
            {'type': 'response_item', 'timestamp': f'{stamp}T09:00:09Z', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Added one.'}]}},
        ]), encoding='utf-8')


def test_the_picker_lists_conversations_people_had_and_resuming_one_imports_it_once(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    root = tmp_path/'proj'; root.mkdir()
    cli_history(home, root)
    store = setup_store(tmp_path/'state')
    project = store.put('tenant-a', 'projects', {'id': 'p1', 'name': 'P', 'root': str(root), 'created_at': '2026-01-01T00:00:00Z'})
    rows = maintenance.cli_conversations(store, 'tenant-a', project)
    assert [(r['source'], r['key'], r['first']) for r in rows] == [('codex', 'x-codex_cli_rs', 'Add a test.'), ('claude', 'c-uuid', 'Fix the widget please.')]
    session = maintenance.resume_cli(store, 'tenant-a', project, 'claude', 'c-uuid')
    assert session['native'] == {'provider': 'claude_cli', 'id': 'c-uuid', 'upto': 2}
    assert maintenance.resume_cli(store, 'tenant-a', project, 'claude', 'c-uuid')['id'] == session['id']
    assert next(r for r in maintenance.cli_conversations(store, 'tenant-a', project) if r['key'] == 'c-uuid')['session_id'] == session['id']


def test_the_same_tool_resumes_its_own_session_and_another_agent_falls_back_to_the_replay(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    root = tmp_path/'proj'; root.mkdir()
    cli_history(home, root)
    store = setup_store(tmp_path/'state')
    project = store.put('tenant-a', 'projects', {'id': 'p1', 'name': 'P', 'root': str(root), 'created_at': '2026-01-01T00:00:00Z'})
    session = maintenance.resume_cli(store, 'tenant-a', project, 'claude', 'c-uuid')
    agent = ScriptedAgent()
    async def respond(config, prompt, mode, where):
        return AgentResult('Continued.', 1, 1, session_id='c-uuid-2')
    agent.respond = respond
    runner = AgentRunner(store, agent)
    after = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Now add docs.', 'claude_cli', 'edit'))
    first = agent.calls[-1]
    assert first['extras']['resume'] == 'c-uuid'
    assert 'Now add docs.' in first['prompt'] and 'Fix the widget please.' not in first['prompt']  # Only what is new.
    assert after['native'] == {'provider': 'claude_cli', 'id': 'c-uuid-2', 'upto': 4}
    asyncio.run(turn(runner, store, 'tenant-a', project, after, 'And a changelog.', 'claude_cli', 'edit'))
    assert agent.calls[-1]['extras']['resume'] == 'c-uuid-2' and 'Now add docs.' not in agent.calls[-1]['prompt']
    # Another agent answers from the replay, and from then on Claude's own session is incomplete, so it replays too.
    later = asyncio.run(turn(runner, store, 'tenant-a', project, after, 'Review it.', 'codex_cli', 'edit'))
    assert 'resume' not in (agent.calls[-1]['extras'] or {}) and 'Fix the widget please.' in agent.calls[-1]['prompt']
    assert later['native'] is None
    asyncio.run(turn(runner, store, 'tenant-a', project, later, 'Thanks.', 'claude_cli', 'edit'))
    assert 'resume' not in (agent.calls[-1]['extras'] or {})


def test_a_session_the_tool_cannot_find_falls_back_to_the_replay(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    root = tmp_path/'proj'; root.mkdir()
    cli_history(home, root)
    store = setup_store(tmp_path/'state')
    project = store.put('tenant-a', 'projects', {'id': 'p1', 'name': 'P', 'root': str(root), 'created_at': '2026-01-01T00:00:00Z'})
    session = maintenance.resume_cli(store, 'tenant-a', project, 'codex', 'x-codex_cli_rs')
    agent = ScriptedAgent()
    async def respond(config, prompt, mode, where):
        if agent.calls[-1]['extras'].get('resume'):
            raise ProviderError('No conversation found with that id.')
        return AgentResult('Replayed.', 1, 1)
    agent.respond = respond
    after = asyncio.run(turn(AgentRunner(store, agent), store, 'tenant-a', project, session, 'Continue.', 'codex_cli', 'edit'))
    assert after['messages'][-1]['status'] == 'complete' and after['messages'][-1]['content'] == 'Replayed.'
    assert 'Add a test.' in agent.calls[-1]['prompt'] and after['native'] is None


def test_resume_spells_itself_for_each_tool(tmp_path):
    claude = agent_argv('claude_cli', ['claude'], 'sonnet', 'edit', tmp_path, tmp_path/'f', {'resume': 'abc'})
    assert claude[claude.index('--resume')+1] == 'abc'
    codex = agent_argv('codex_cli', ['codex'], 'gpt-6-sol', 'read', tmp_path, tmp_path/'f', {'resume': 'abc'})
    assert codex[codex.index('exec'):codex.index('exec')+2] == ['exec', 'resume'] and codex[-2:] == ['abc', '-']
    assert 'sandbox_mode="read-only"' in codex and '-C' not in codex
    auto = agent_argv('codex_cli', ['codex'], 'gpt-6-sol', 'auto', tmp_path, tmp_path/'f', {'resume': 'abc'})
    assert '--dangerously-bypass-approvals-and-sandbox' in auto and not any(a.startswith('sandbox_mode') for a in auto)


def test_subscription_limits_read_both_plans_and_back_off_when_refused(tmp_path, monkeypatch):
    home = tmp_path/'home'; (home/'.claude').mkdir(parents=True); (home/'.codex').mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.delenv('CLAUDE_CONFIG_DIR', raising=False); monkeypatch.delenv('CODEX_HOME', raising=False)
    (home/'.claude'/'.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'c'}}), encoding='utf-8')
    (home/'.codex'/'auth.json').write_text(json.dumps({'tokens': {'access_token': 'x'}}), encoding='utf-8')
    calls = []
    def fetch(url, headers):
        calls.append(url)
        if 'anthropic' in url:
            return None, 'rate limited', 120
        return {'plan_type': 'pro', 'rate_limit': {'primary_window': {'used_percent': 11, 'limit_window_seconds': 604800, 'reset_at': 1790528925}, 'secondary_window': None}}, None, None
    monkeypatch.setattr(maintenance, 'fetch_json', fetch); monkeypatch.setattr(maintenance, 'LIMITS_CACHE', {})
    claude, codex = maintenance.subscription_limits()
    assert claude['error'] == 'rate limited' and codex['plan'] == 'pro' and codex['limits'][0]['label'] == 'Week' and codex['limits'][0]['percent'] == 11
    maintenance.subscription_limits(force=True)
    assert sum('anthropic' in u for u in calls) == 1 and sum('chatgpt' in u for u in calls) == 2  # A refusal is waited out, even on refresh.
