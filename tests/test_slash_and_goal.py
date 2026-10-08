"""Slash commands that run as the agent's own, and /goal, which keeps any agent working toward one objective."""
import asyncio
from pathlib import Path
from backend import slash
from backend.agent import AgentRunner
from backend.broker import AgentResult, agent_argv
from tests.harness import ScriptedAgent, open_project, setup_store


def run(runner, store, project, session, content, model_id='claude_cli', mode='edit'):
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], content, model_id, mode)
        # A goal sends its next turn when one finishes, so wait until nothing is running any more.
        for _ in range(200):
            turn = runner.turns.get(('tenant-a', session['id']))
            if turn is None:
                await asyncio.sleep(0.02)
                if runner.turns.get(('tenant-a', session['id'])) is None:
                    return
                continue
            await asyncio.gather(turn, return_exceptions=True)
    asyncio.run(scenario())
    return store.get('tenant-a', 'sessions', session['id'])


def test_a_goal_keeps_going_until_the_agent_reports_it_complete(tmp_path):
    replies = iter(['Set up the module.\nGOAL: continuing', 'Wrote the tests, all pass.\nGOAL: complete — 12 tests pass'])
    prompts = []
    async def respond(config, prompt, mode, root):
        prompts.append(prompt)
        return AgentResult(next(replies), 1, 1)
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    after = run(runner, store, project, session, '/goal Add a parser module with tests')
    assert len(prompts) == 2 and all('standing goal set by the user: Add a parser module with tests' in p for p in prompts)
    users = [m['content'] for m in after['messages'] if m['role'] == 'user']
    assert users[0] == 'Goal: Add a parser module with tests' and users[1].startswith('Keep working toward the goal (turn 2')
    assert after['goal']['status'] == 'complete' and after['goal']['note'] == '12 tests pass' and after['goal']['turns'] == 2


def test_a_goal_pauses_when_the_agent_stops_reporting_and_can_be_resumed_or_cleared(tmp_path):
    calls = []
    async def respond(config, prompt, mode, root):
        calls.append(prompt)
        return AgentResult('Did some work.', 1, 1)  # No GOAL line.
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    after = run(runner, store, project, session, '/goal Tidy the README')
    assert len(calls) == 2 and after['goal']['status'] == 'paused' and 'twice' in after['goal']['note']
    after = run(runner, store, project, session, '/goal resume', model_id='codex_cli')
    assert after['goal']['status'] == 'paused' and after['goal']['model_id'] == 'codex_cli' and len(calls) == 4
    cleared = runner.send('tenant-a', project['id'], session['id'], '/goal clear', 'claude_cli', 'edit')
    assert cleared['goal']['status'] == 'cleared' and len(calls) == 4  # Clearing starts no turn.


def test_a_goal_never_outruns_its_turn_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(slash, 'MAX_GOAL_TURNS', 3)
    async def respond(config, prompt, mode, root):
        return AgentResult('More to do.\nGOAL: continuing', 1, 1)
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    after = run(runner, store, project, session, '/goal Refactor everything')
    assert after['goal']['status'] == 'paused' and after['goal']['turns'] == 3 and 'Paused after 3 turns' in after['goal']['note']


def test_an_agent_command_reaches_claude_as_its_prompt_and_opencode_as_its_own_command(tmp_path):
    agent = ScriptedAgent()
    store = setup_store(tmp_path/'state')
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', tmp_path)
    run(runner, store, project, session, '/hello banana', model_id='claude_cli')
    assert agent.calls[-1]['prompt'] == '/hello banana'
    run(runner, store, project, session, '/hello kiwi', model_id='opencode_cli')
    assert agent.calls[-1]['prompt'] == '' and agent.calls[-1]['extras']['slash'] == {'name': 'hello', 'args': 'kiwi', 'raw': '/hello kiwi'}
    run(runner, store, project, session, '/hello pear', model_id='codex_cli')
    assert 'USER:\n/hello pear' in agent.calls[-1]['prompt']  # Codex has no such mode: the message stays in the conversation.
    argv = agent_argv('opencode_cli', ['opencode'], 'm', 'edit', tmp_path, tmp_path/'f', {'slash': {'name': 'hello', 'args': 'kiwi', 'raw': ''}})
    assert argv[argv.index('--command'):argv.index('--command')+3] == ['--command', 'hello', 'kiwi']


def test_the_menu_lists_frontiers_commands_the_agents_and_the_screen_only_ones(tmp_path):
    (tmp_path/'.opencode'/'command').mkdir(parents=True)
    (tmp_path/'.opencode'/'command'/'ship.md').write_text('Ship it', encoding='utf-8')
    entries = slash.catalog(tmp_path, ['init', 'review', 'config', '__hidden'], [{'name': 'deploy', 'provider': 'claude', 'description': 'Deploy'}])
    by = {(e['name'], e['source'], e['kind']) for e in entries}
    assert ('goal', 'Frontier', 'frontier') in by and ('resume', 'Frontier', 'frontier') in by
    assert ('init', 'Claude', 'agent') in by and ('deploy', 'Claude', 'agent') in by and ('ship', 'OpenCode', 'agent') in by
    assert ('config', 'Claude', 'terminal') in by and ('review', 'Codex', 'terminal') in by and not any(e['name'] == '__hidden' for e in entries)
    assert slash.parse('/goal x') is None and slash.parse('/review the diff') == {'name': 'review', 'args': 'the diff', 'raw': '/review the diff'}
    assert slash.parse('not a command') is None and slash.goal_verdict('ok\nGOAL: blocked - needs a key') == ('blocked', 'needs a key')
