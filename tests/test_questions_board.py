"""Question cards (an agent asks mid-turn and waits) and the project task board."""
import asyncio
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend import board, permission_tool
from backend.agent import AgentRunner, build_prompt
from backend.app import create_app
from backend.broker import AgentResult
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup


def test_a_question_waits_for_the_users_answer_and_returns_it_to_the_agent(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_PORT', '8765')
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    seen = {}
    async def respond(config, prompt, mode, root):
        token = agent.calls[-1]['extras']['approval']['token']
        question = runner.request_approval(token, 'ask_user', {'question': 'Tabs or spaces?', 'options': ['Tabs', 'Spaces']}, kind='question')
        pending = runner.pending('tenant-a', session['id'])
        seen['pending'] = pending
        runner.decide('tenant-a', session['id'], question, True, 'Tabs, always.')
        seen['state'] = await runner.approval_state(question)
        return AgentResult('Used tabs.', 1, 1)
    agent = ScriptedAgent(respond=respond)
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Format it.', 'claude_cli', 'read'))
    assert seen['pending'][0]['kind'] == 'question' and seen['pending'][0]['input']['options'] == ['Tabs', 'Spaces']
    assert seen['state'] == {'decision': 'allow', 'message': 'Tabs, always.'}
    assert any('The agent asks: Tabs or spaces?' in e['message'] for e in store.events('tenant-a', session['id']))


def test_the_question_tool_speaks_mcp_and_turns_the_answer_into_text(monkeypatch):
    answers = iter([{'id': 'q1'}, {'decision': None}, {'decision': 'allow', 'message': 'Use Postgres.'}])
    sent = []
    monkeypatch.setattr(permission_tool, 'call', lambda method, path, body=None: sent.append((method, path, body)) or next(answers))
    monkeypatch.delenv('FRONTIER_TOOLS', raising=False)
    names = [t['name'] for t in permission_tool.handle({'id': 1, 'method': 'tools/list'})['tools']]
    assert names == ['approve', 'ask_user', 'board_list', 'board_add', 'board_update']
    reply = permission_tool.handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'ask_user', 'arguments': {'question': 'Which database?', 'options': ['Postgres', 'SQLite']}}})
    assert reply['content'][0]['text'] == 'The user answered: Use Postgres.'
    assert sent[0][2] == {'tool_name': 'ask_user', 'kind': 'question', 'input': {'question': 'Which database?', 'options': ['Postgres', 'SQLite']}}
    monkeypatch.setenv('FRONTIER_TOOLS', 'board')  # Codex and OpenCode: the board only.
    assert [t['name'] for t in permission_tool.handle({'id': 3, 'method': 'tools/list'})['tools']] == ['board_list', 'board_add', 'board_update']
    assert permission_tool.handle({'id': 4, 'method': 'tools/call', 'params': {'name': 'ask_user', 'arguments': {'question': 'x'}}})['isError']


def test_the_board_is_kept_by_hand_shown_to_agents_and_updated_by_them(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_PORT', '8765')
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent()), client=('127.0.0.1', 5000)) as c:
        t = setup(c)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        base = f'/api/t/{t}/projects/{p["id"]}/board'
        login = c.post(base, json={'title': 'Add login', 'notes': 'Email and password.'}).json()
        c.post(base, json={'title': 'Old chore', 'status': 'done'})
        assert c.put(f'{base}/{login["id"]}', json={'status': 'doing'}).json()['status'] == 'doing'
        assert c.put(f'{base}/{login["id"]}', json={'status': 'sideways'}).status_code == 422
        rendered = board.render(c.get(base).json())
        assert f'[Doing] Add login (id {login["id"]}): Email and password.' in rendered and 'Old chore' not in rendered
    assert 'PROJECT TASK BOARD' in build_prompt([{'role': 'user', 'content': 'Go.'}], False, board=rendered)
    assert 'PROJECT TASK BOARD' not in build_prompt([{'role': 'user', 'content': 'Go.'}], False)


def test_an_agent_moves_a_board_task_through_its_turn(tmp_path, monkeypatch):
    monkeypatch.setenv('HARNESS_DESKTOP_PORT', '8765')
    results = {}
    async def respond(config, prompt, mode, root):
        results['prompt'] = prompt
        token = holder['agent'].calls[-1]['extras']['approval']['token']
        headers = {'X-Frontier-Turn': token}
        c = holder['client']  # The tool is another process; here, another thread calling the same routes.
        results['list'] = (await asyncio.to_thread(c.get, '/internal/board', headers=headers)).json()
        task_id = results['list'][0]['id']
        results['update'] = (await asyncio.to_thread(c.post, f'/internal/board/{task_id}', json={'status': 'done', 'notes': 'Shipped.'}, headers=headers)).json()
        results['add'] = (await asyncio.to_thread(c.post, '/internal/board', json={'title': 'Write docs'}, headers=headers)).json()
        results['stranger'] = (await asyncio.to_thread(c.get, '/internal/board', headers={'X-Frontier-Turn': 'nope'})).status_code
        return AgentResult('Done.', 1, 1)
    holder = {'agent': ScriptedAgent(respond=respond)}
    with TestClient(create_app(str(tmp_path/'state'), holder['agent']), client=('127.0.0.1', 5000)) as c:
        holder['client'] = c
        t = setup(c)
        model = c.post(f'/api/t/{t}/models', json={'name': 'Claude', 'provider': 'claude_cli', 'model_name': 'sonnet'}).json()['id']
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        c.post(f'/api/t/{t}/projects/{p["id"]}/board', json={'title': 'Add login'})
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Login work'}).json()
        c.post(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}/instructions', json={'content': 'Build the login.', 'model_id': model, 'mode': 'edit'})
        for _ in range(200):
            if c.get(f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}').json()['messages'][-1]['status'] != 'running':
                break
            __import__('time').sleep(0.02)
        tasks = c.get(f'/api/t/{t}/projects/{p["id"]}/board').json()
    assert 'Add login' in results['prompt'] and 'PROJECT TASK BOARD' in results['prompt']
    assert results['update']['status'] == 'done' and results['update']['updated_by'] == 'Claude'
    assert results['add']['source'] == 'Claude · Login work' and results['stranger'] == 403
    assert {(x['title'], x['status']) for x in tasks} == {('Add login', 'done'), ('Write docs', 'todo')}


def test_codex_and_opencode_get_the_board_server_and_team_tasks_land_on_the_board(tmp_path):
    from backend.broker import agent_argv
    approval = {'command': ['frontier-backend.exe', '--permission-tool'], 'url': 'http://127.0.0.1:1', 'token': 'tok'}
    server = {'name': 'frontier', 'transport': 'stdio', 'trusted': True, 'command': 'frontier-backend.exe', 'args': ['--permission-tool'],
              'env': {'FRONTIER_URL': 'http://127.0.0.1:1', 'FRONTIER_TOKEN': 'tok', 'FRONTIER_TOOLS': 'board'}}
    argv = agent_argv('codex_cli', ['codex'], 'gpt-6-sol', 'edit', tmp_path, tmp_path/'f', {'mcp_servers': [server], 'approval': approval})
    assert 'mcp_servers.frontier.command="frontier-backend.exe"' in argv and any('FRONTIER_TOOLS' in a for a in argv)
    assert 'mcp_servers.frontier.default_tools_approval_mode="approve"' in argv  # Exec has no one to approve a board update.
    store = setup_store(tmp_path/'state')
    board.mirror(store, 'tenant-a', 'p1', 'team:m:1', 'Write the API', 'doing', session_id='s1', source='team · Codex')
    board.mirror(store, 'tenant-a', 'p1', 'team:m:1', 'Write the API', 'done', notes='Added routes.')
    [task] = board.tasks(store, 'tenant-a', 'p1')
    assert task['status'] == 'done' and task['notes'] == 'Added routes.' and task['session_id'] == 's1' and task['source'] == 'team · Codex'


def test_claudes_own_question_tool_becomes_question_cards_and_its_answers_go_back_keyed_by_question(monkeypatch):
    replies = iter([{'id': 'q1'}, {'decision': 'allow', 'message': 'Otter'}])
    sent = []
    monkeypatch.setattr(permission_tool, 'call', lambda method, path, body=None: sent.append(body) or next(replies))
    native = {'questions': [{'question': 'Which animal?', 'header': 'Animal', 'multiSelect': False,
                             'options': [{'label': 'Dog', 'description': 'A dog'}, {'label': 'Cat', 'description': 'A cat'}]}]}
    decision = permission_tool.decide({'tool_name': 'AskUserQuestion', 'input': native})
    assert sent[0] == {'tool_name': 'ask_user', 'kind': 'question', 'input': {'question': 'Which animal?', 'options': ['Dog', 'Cat']}}
    assert decision == {'behavior': 'allow', 'updatedInput': {**native, 'answers': {'Which animal?': 'Otter'}}}
    replies = iter([{'id': 'q2'}, {'decision': 'deny'}])
    monkeypatch.setattr(permission_tool, 'call', lambda method, path, body=None: next(replies))
    assert permission_tool.decide({'tool_name': 'AskUserQuestion', 'input': native})['behavior'] == 'deny'
