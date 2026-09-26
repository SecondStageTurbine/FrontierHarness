"""Team mode, pull requests, rewind, context chips, worktree setup, dev server, lifecycle, credentials,
memory, rules, tool maintenance and history import, and larger attachments."""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import automations, devserver, gitops, maintenance, pullrequests, team
from backend.agent import AgentRunner, build_prompt
from backend.app import create_app
from backend.broker import AgentResult, agent_argv
from tests.harness import ScriptedAgent, open_project, setup_store, turn
from tests.test_api import setup, agent as connect_agent

needs_git = pytest.mark.skipif(not gitops.available(), reason='git is not installed')


def git(root, *args):
    return subprocess.run(['git', '-c', 'user.name=T', '-c', 'user.email=t@x', *args], cwd=root, check=True, capture_output=True, text=True).stdout


def repo(tmp_path, name='work'):
    root = tmp_path/name
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.name', 'T'); git(root, 'config', 'user.email', 't@x')
    (root/'README.md').write_text('# App\n', encoding='utf-8')
    (root/'.gitignore').write_text('.env\n', encoding='utf-8')
    (root/'.env').write_text('API_KEY=secret\nDB_URL=postgres://x\n', encoding='utf-8')
    git(root, 'add', '-A'); git(root, 'commit', '-q', '-m', 'first')
    return root


def wait_done(c, route):
    for _ in range(400):
        detail = c.get(route).json()
        if detail['messages'][-1]['status'] != 'running':
            return detail
        time.sleep(0.05)
    raise AssertionError('turn did not finish')


# ── Team mode ──

def test_plans_are_parsed_normalised_assigned_and_ordered():
    raw = 'Here is the plan.\n```json\n{"summary": "Do both halves.", "tasks": [{"id": "t1", "title": "Backend", "instructions": "Add the route.", "needs": ["coding"], "parallel": false},' \
          '{"id": "t2", "title": "Frontend", "instructions": "Add the button.", "needs": ["coding", "bogus"], "agent": "Codex", "depends_on": ["t1"]},' \
          '{"id": "t3", "title": "Docs", "instructions": "Write docs.", "parallel": true, "depends_on": ["t1"]}, {"title": "", "instructions": ""}]}\n```'
    plan = team.normalise_plan(team.parse_json(raw))
    assert plan['summary'] == 'Do both halves.' and [t['id'] for t in plan['tasks']] == ['t1', 't2', 't3']
    assert plan['tasks'][1]['needs'] == ['coding'] and plan['tasks'][1]['agent'] == 'Codex'
    agents = [{'id': 'claude_cli', 'name': 'Claude', 'provider': 'claude_cli', 'model_name': 'sonnet'}, {'id': 'codex_cli', 'name': 'Codex', 'provider': 'codex_cli', 'model_name': 'gpt'},
              {'id': 'opencode_cli', 'name': 'OpenCode', 'provider': 'opencode_cli', 'model_name': 'x'}]
    assert team.assign(plan['tasks'][1], agents, agents[0])['id'] == 'codex_cli'  # The lead's explicit pick wins.
    picked = team.assign(plan['tasks'][0], agents, agents[0])
    assert picked['id'] in {a['id'] for a in agents}  # Otherwise Adaptive's profiles decide.
    order = team.waves(plan['tasks'])
    assert [[t['id'] for t in wave] for wave in order] == [['t1'], ['t2', 't3']]
    assert team.parse_json('no json here') is None and team.normalise_plan(None)['tasks'] == []


@needs_git
def test_a_lead_plans_workers_do_the_tasks_in_worktrees_and_the_lead_reviews(tmp_path):
    prompts = []
    async def respond(config, prompt, mode, root):
        prompts.append((config['id'], mode, prompt))
        if 'reply with ONLY a JSON object' in prompt and '"tasks"' in prompt and 'REPORTS:' not in prompt:
            assert mode == 'read'
            return AgentResult('```json\n{"summary": "Two files.", "tasks": [{"id": "a", "title": "Write A", "instructions": "Create a.txt containing A.", "needs": ["coding"], "parallel": true},'
                               '{"id": "b", "title": "Write B", "instructions": "Create b.txt containing B.", "agent": "Codex", "parallel": true}]}\n```', 50, 20)
        if 'REPORTS:' in prompt:
            assert mode == 'read' and 'a.txt' in prompt and 'b.txt' in prompt
            return AgentResult('```json\n{"verdict": "done", "fixes": [], "reply": "Both files are in place."}\n```', 30, 10)
        # A worker: write the file its task names, in its own worktree.
        name = 'a.txt' if 'YOUR TASK — Write A' in prompt else 'b.txt'
        (Path(root)/name).write_text(name[0].upper(), encoding='utf-8')
        return AgentResult(f'Wrote {name}.', 10, 5)
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', root)
    async def scenario():
        runner.send('tenant-a', project['id'], session['id'], 'Create a.txt and b.txt.', 'claude_cli', 'edit', team=True)
        await asyncio.gather(runner.turns[('tenant-a', session['id'])], return_exceptions=True)
    asyncio.run(scenario())
    reply = store.get('tenant-a', 'sessions', session['id'])['messages'][-1]
    assert reply['status'] == 'complete', reply.get('error')
    assert reply['content'] == 'Both files are in place.'
    tasks = reply['team']['tasks']
    assert reply['team']['status'] == 'done' and [t['status'] for t in tasks] == ['done', 'done']
    assert tasks[1]['model_id'] == 'codex_cli' and tasks[0]['model_id'] in ('claude_cli', 'codex_cli', 'opencode_cli')
    assert all(t['merge'] == 'applied' for t in tasks)
    # The plan is on the project's board too, done, each task pointing at its worker's session.
    from backend import board
    on_board = board.tasks(store, 'tenant-a', project['id'])
    assert sorted((b['title'], b['status']) for b in on_board) == sorted((t['title'], 'done') for t in tasks)
    assert {b['session_id'] for b in on_board} == {t['session_id'] for t in tasks}
    # The workers wrote in their worktrees; the patches landed in the project folder, uncommitted.
    assert (root/'a.txt').read_text(encoding='utf-8') == 'A' and (root/'b.txt').read_text(encoding='utf-8') == 'B'
    worker_ids = {t['session_id'] for t in tasks}
    workers = [s for s in store.list('tenant-a', 'sessions') if s['id'] in worker_ids]
    assert len(workers) == 2 and all(w['team_parent'] == session['id'] and w.get('worktree') for w in workers)
    assert not (Path(workers[0]['worktree']['path'])/'.env').exists()  # Nothing copied unless configured.
    lead_calls = [p for p in prompts if p[1] == 'read']
    assert len(lead_calls) == 2 and all(p[0] == 'claude_cli' for p in lead_calls)


def test_team_mode_refuses_adaptive_leads_and_read_only(tmp_path):
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, ScriptedAgent())
    project, session = open_project(store, runner, 'tenant-a', folder)
    with pytest.raises(ValueError):
        runner.send('tenant-a', project['id'], session['id'], 'Do it.', 'adaptive', 'edit', team=True)
    with pytest.raises(ValueError):
        runner.send('tenant-a', project['id'], session['id'], 'Do it.', 'claude_cli', 'read', team=True)


# ── Rewind ──

@needs_git
def test_rewind_puts_the_folder_and_the_conversation_back_to_before_a_message(tmp_path):
    store = setup_store(tmp_path/'state')
    root = repo(tmp_path)
    runner = AgentRunner(store, ScriptedAgent(writes={'README.md': '# Changed\n', 'new.txt': 'x'}))
    project, session = open_project(store, runner, 'tenant-a', root)
    session = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Change things.'))
    session = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'And again.'))
    first_user = session['messages'][0]
    result = asyncio.run(runner.rewind('tenant-a', project['id'], session['id'], first_user['id']))
    assert result['content'] == 'Change things.' and result['session']['messages'] == []
    assert (root/'README.md').read_text(encoding='utf-8') == '# App\n' and not (root/'new.txt').exists()
    assert 'README.md' in result['restored']


def test_rewind_without_a_repository_uses_the_recorded_changes(tmp_path):
    store = setup_store(tmp_path/'state'); folder = tmp_path/'plain'; folder.mkdir()
    (folder/'notes.md').write_text('start\n', encoding='utf-8')
    runner = AgentRunner(store, ScriptedAgent(writes={'notes.md': 'changed\n'}))
    project, session = open_project(store, runner, 'tenant-a', folder)
    session = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'First.'))
    session = asyncio.run(turn(runner, store, 'tenant-a', project, session, 'Second.'))
    result = asyncio.run(runner.rewind('tenant-a', project['id'], session['id'], session['messages'][2]['id']))
    assert len(result['session']['messages']) == 2 and result['content'] == 'Second.'
    assert (folder/'notes.md').read_text(encoding='utf-8') == 'changed\n'  # Only the second turn was undone; it rewrote the same text.
    with pytest.raises(ValueError):
        asyncio.run(runner.rewind('tenant-a', project['id'], session['id'], result['session']['messages'][1]['id']))


# ── Context chips, rules, memory, .env protection ──

def test_context_chips_rules_and_memory_reach_the_prompt_and_env_stays_closed(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(reply='ok'))) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        (root/'app.py').write_text('a\nb\nc\nd\n', encoding='utf-8')
        (root/'.env').write_text('TOKEN=1\nexport OTHER=2\n# comment\n', encoding='utf-8')
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        assert c.put(f'/api/t/{t}/projects/{p["id"]}/settings', json={'memory': 'Uses tabs.', 'protect_env': True, 'worktree_copy': ['.env']}).json()['memory'] == 'Uses tabs.'
        c.put(f'/api/tenants/{t}', json={'name': 'Workspace A', 'rules': 'Always write tests.'})
        assert c.get(f'/api/t/{t}/projects/{p["id"]}/credentials').json() == [{'file': '.env', 'names': ['TOKEN', 'OTHER']}]
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={}).json()
        route = f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        r = c.post(route+'/instructions', json={'content': 'Look at this.', 'model_id': a, 'mode': 'read',
                                                 'context': [{'kind': 'file', 'path': 'app.py', 'start': 2, 'end': 3}, {'kind': 'terminal', 'text': 'npm ERR! boom', 'label': 'shell'}]})
        assert r.status_code == 200, r.text
        detail = wait_done(c, route)
        user = detail['messages'][0]
        assert 'lines 2-3' in user['content'] and 'b\nc' in user['content'] and 'npm ERR! boom' in user['content']
        assert [chip['kind'] for chip in user['context']] == ['file', 'terminal']
    agent = c.app.state.runner.broker
    prompt = agent.calls[-1]['prompt']
    assert 'WORKSPACE RULES' in prompt and 'Always write tests.' in prompt and 'PROJECT NOTES' in prompt and 'Uses tabs.' in prompt
    assert agent.calls[-1]['extras']['protect_env'] is True
    argv = agent_argv('claude_cli', ['claude'], 'sonnet', 'edit', root, root/'f', {'protect_env': True})
    assert 'Read(**/.env)' in argv and argv.count('--disallowedTools') == 1
    argv = agent_argv('claude_cli', ['claude'], 'sonnet', 'read', root, root/'f', {'protect_env': True})
    assert 'Bash' in argv and 'Read(./.env)' in argv and argv.count('--disallowedTools') == 1


def test_remember_appends_the_agents_notes_to_the_project(tmp_path):
    async def respond(config, prompt, mode, root):
        assert mode == 'read' and 'worth remembering' in prompt
        return AgentResult('- Tests live in tests/.\n- Run pytest -q.\nnot a bullet', 5, 5)
    store = setup_store(tmp_path/'state'); folder = tmp_path/'work'; folder.mkdir()
    runner = AgentRunner(store, ScriptedAgent(respond=respond))
    project, session = open_project(store, runner, 'tenant-a', folder)
    with pytest.raises(ValueError):
        asyncio.run(runner.remember('tenant-a', project['id'], session['id']))
    session['messages'] = [{'id': 'u', 'role': 'user', 'content': 'Where are the tests?', 'created_at': '2026-01-01T00:00:00Z'},
                           {'id': 'a', 'role': 'assistant', 'content': 'In tests/.', 'created_at': '2026-01-01T00:00:01Z', 'status': 'complete', 'model_id': 'claude_cli', 'model_name': 'Claude'}]
    store.put('tenant-a', 'sessions', session)
    project = asyncio.run(runner.remember('tenant-a', project['id'], session['id']))
    assert 'Tests live in tests/.' in project['memory'] and 'not a bullet' not in project['memory']
    assert 'PROJECT NOTES' in build_prompt(session['messages'], False, memory=project['memory'])


# ── Worktree setup and hydration ──

@needs_git
def test_a_new_worktree_session_copies_ignored_files_and_runs_the_setup_command(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = repo(tmp_path)
        p = c.post(f'/api/t/{t}/projects', json={'name': 'Repo', 'root': str(root)}).json()
        marker = 'echo setup-ran > setup.txt'
        c.put(f'/api/t/{t}/projects/{p["id"]}/settings', json={'worktree_copy': ['.env', '../outside', 'missing.txt'], 'worktree_setup': marker})
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Branch work', 'worktree': True}).json()
        wt = Path(s['worktree']['path'])
        assert s['worktree']['copied'] == ['.env'] and (wt/'.env').read_text(encoding='utf-8').startswith('API_KEY')
        assert s['setup']['exit_code'] == 0 and (wt/'setup.txt').read_text(encoding='utf-8').strip() == 'setup-ran'


# ── Lifecycle: snooze and auto-archive ──

def test_snooze_and_idle_archiving(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={'name': 'Old'}).json()
        route = f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        later = (datetime.now().astimezone()+timedelta(hours=1)).isoformat(timespec='seconds')
        assert c.patch(route, json={'snoozed_until': later}).json()['snoozed_until'] == later
        assert c.patch(route, json={'snoozed_until': 'not a time'}).status_code in (400, 422, 500)
        assert 'snoozed_until' not in c.patch(route, json={'snoozed_until': ''}).json()
        store, runner = c.app.state.store, c.app.state.runner
        session = store.get(t, 'sessions', s['id'])
        session['messages'] = [{'id': 'u', 'role': 'user', 'content': 'x', 'created_at': '2026-01-01T00:00:00Z'}]
        session['snoozed_until'] = (datetime.now().astimezone()-timedelta(minutes=1)).isoformat(timespec='seconds')
        store.put(t, 'sessions', session)
        with store.db() as db:  # put() stamps updated_at itself, so age the row directly.
            db.execute("UPDATE entities SET data=json_set(data,'$.updated_at','2026-01-01T00:00:00+00:00') WHERE kind='sessions' AND id=?", (s['id'],))
        c.put(f'/api/tenants/{t}', json={'name': 'Workspace A', 'auto_archive_days': 30})
        asyncio.run(automations.housekeeping(store, runner, t))
        after = store.get(t, 'sessions', s['id'])
        assert 'snoozed_until' not in after and after['archived'] is True and after['archived_reason'] == 'idle for 30 days'


# ── Pull requests: what can be checked without GitHub ──

def test_pull_request_summaries_and_the_prompt_that_addresses_reviews():
    assert pullrequests.summarise_checks([{'conclusion': 'SUCCESS'}, {'state': 'PENDING'}, {'conclusion': 'FAILURE'}]) == {'success': 1, 'failure': 1, 'pending': 1}
    pr = {'number': 7, 'title': 'Add login', 'url': 'https://github.com/o/r/pull/7'}
    prompt = pullrequests.address_prompt(pr, {'reviews': [{'author': 'ann', 'state': 'CHANGES_REQUESTED', 'body': 'Needs tests.'}],
                                              'comments': [{'author': 'bob', 'path': 'auth.py', 'line': 12, 'body': 'Null check?'}]})
    assert '#7' in prompt and 'auth.py:12' in prompt and 'Needs tests.' in prompt and 'do not push' in prompt


# ── Dev server ──

def test_the_dev_server_starts_reports_its_port_and_stops(tmp_path):
    root = tmp_path/'site'; root.mkdir()
    command = f'"{sys.executable}" -u -m http.server 0 --bind 127.0.0.1'
    async def scenario():
        server = await devserver.start(root, command)
        for _ in range(50):
            if server.port:
                break
            await asyncio.sleep(0.1)
        status = server.status()
        assert status['running'] and status['port'] and 'Serving HTTP' in status['output']
        await devserver.stop(root)
        await asyncio.sleep(0.3)
        assert not devserver.get(root).status()['running']
    asyncio.run(scenario())


# ── History import and tool versions ──

def test_claude_and_codex_histories_become_sessions_once(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    root = tmp_path/'proj'; root.mkdir()
    claude_dir = home/'.claude'/'projects'/maintenance.claude_slug(root); claude_dir.mkdir(parents=True)
    (claude_dir/'abc.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'type': 'custom-title', 'customTitle': 'Fix the widget'},
        {'type': 'user', 'timestamp': '2026-05-01T10:00:00Z', 'message': {'role': 'user', 'content': 'Fix the widget please.'}},
        {'type': 'assistant', 'timestamp': '2026-05-01T10:00:05Z', 'message': {'role': 'assistant', 'content': [{'type': 'thinking', 'thinking': 'hmm'}, {'type': 'text', 'text': 'Fixed it.'}]}},
        {'type': 'user', 'timestamp': '2026-05-01T10:00:06Z', 'message': {'role': 'user', 'content': [{'type': 'tool_result', 'content': 'x'}]}},
        {'type': 'user', 'isSidechain': True, 'message': {'role': 'user', 'content': 'side'}},
    ]), encoding='utf-8')
    codex_dir = home/'.codex'/'sessions'/'2026'/'05'/'02'; codex_dir.mkdir(parents=True)
    (codex_dir/'rollout-x.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'type': 'session_meta', 'payload': {'id': 'sess1', 'cwd': str(root), 'timestamp': '2026-05-02T09:00:00Z'}},
        {'type': 'response_item', 'timestamp': '2026-05-02T09:00:01Z', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<environment_context>x</environment_context>'}]}},
        {'type': 'response_item', 'timestamp': '2026-05-02T09:00:02Z', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Add a test.'}]}},
        {'type': 'response_item', 'timestamp': '2026-05-02T09:00:09Z', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Added tests/test_x.py.'}]}},
    ]), encoding='utf-8')
    store = setup_store(tmp_path/'state')
    project = store.put('tenant-a', 'projects', {'id': 'p1', 'name': 'P', 'root': str(root), 'created_at': '2026-01-01T00:00:00Z'})
    imported = maintenance.import_history(store, 'tenant-a', project)
    assert {(i['source'], i['name'], i['messages']) for i in imported} == {('claude', 'Fix the widget', 2), ('codex', 'Add a test.', 2)}
    sessions = store.list('tenant-a', 'sessions')
    claude = next(s for s in sessions if s['imported_from'] == 'claude')
    assert claude['messages'][1]['content'] == 'Fixed it.' and claude['messages'][1]['model_name'] == 'Claude Code (imported)'
    assert maintenance.import_history(store, 'tenant-a', project) == []  # Already there.
    assert 'gemini_cli' not in maintenance.PACKAGES  # The Antigravity CLI updates itself; it is not an npm package.


# ── Attachments: files on disk, many of them ──

def test_binary_attachments_are_kept_as_files_and_copied_into_the_project(tmp_path):
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c); a = connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        made = c.post(f'/api/t/{t}/attachments', files={'file': ('clip.mp4', b'\x00\x00\x00\x18ftypmp42' + b'\x00'*100, 'video/mp4')})
        assert made.status_code == 200, made.text
        att = made.json()
        assert att['kind'] == 'file' and 'path' not in att and (Path(tmp_path/'state')/'attachments'/(att['id']+'.mp4')).is_file()
        s = c.post(f'/api/t/{t}/projects/{p["id"]}/sessions', json={}).json()
        route = f'/api/t/{t}/projects/{p["id"]}/sessions/{s["id"]}'
        ids = [att['id']]*9
        assert c.post(route+'/instructions', json={'content': 'Look at the clip.', 'model_id': a, 'mode': 'read', 'attachment_ids': ids}).status_code == 200
        detail = wait_done(c, route)
        assert '.frontier/attachments/clip.mp4' in detail['messages'][0]['content'] and (root/'.frontier'/'attachments'/'clip.mp4').stat().st_size == 112


def test_review_tasks_go_to_a_different_model_family_from_the_lead_and_the_authors():
    agents = [{'id': 'claude_cli', 'name': 'Claude', 'provider': 'claude_cli', 'model_name': 'sonnet'},
              {'id': 'codex_cli', 'name': 'Codex', 'provider': 'codex_cli', 'model_name': 'gpt'},
              {'id': 'opencode_cli', 'name': 'OpenCode', 'provider': 'opencode_cli', 'model_name': 'x'}]
    review = {'id': 'r', 'title': 'Review and integrate the fixes', 'needs': ['review'], 'agent': 'Claude'}
    assert team.is_review(review)
    # The lead is Claude and Codex wrote the work: the review goes to neither, even when the plan named Claude.
    assert team.assign(review, agents, agents[0], authors={'codex_cli'})['provider'] == 'opencode_cli'
    # With only the lead's family left besides the authors, it still avoids the lead.
    assert team.assign(review, agents[:2], agents[0], authors={'codex_cli'})['provider'] == 'codex_cli'
    assert not team.is_review({'title': 'Fix the shader warning', 'needs': ['coding']})


def test_review_tasks_are_recognised_by_their_title_alone():
    assert team.is_review({'title': 'Review and integrate Web fixes, final gate', 'needs': ['coding']})
    assert team.is_review({'title': 'Audit the auth changes', 'needs': []})
    assert not team.is_review({'title': 'Previewing the checkout page', 'needs': []})  # A word boundary, not a substring.
