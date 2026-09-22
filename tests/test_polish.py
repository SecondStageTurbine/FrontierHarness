"""Titles from the first reply, the context meter, and compacting a conversation into a summary."""
import pytest
from backend.agent import AgentRunner, build_prompt, context_usage, take_title, TRANSCRIPT_LIMIT
from backend.broker import AgentResult
from tests.harness import ScriptedAgent, open_project, setup_store, turn


def make(tmp_path, agent):
    store = setup_store(tmp_path/'state')
    folder = tmp_path/'work'
    folder.mkdir()
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    return store, runner, project, session


def test_the_first_reply_names_the_conversation_and_the_title_line_is_stripped():
    assert take_title('Title: Fix the login bug\nDone, see auth.py.') == ('Fix the login bug', 'Done, see auth.py.')
    assert take_title('**Title:** "Notes update"\n\nAdded it.') == ('Notes update', 'Added it.')
    assert take_title('Plain reply without a title.') == (None, 'Plain reply without a title.')
    assert take_title('') == (None, '')


@pytest.mark.asyncio
async def test_a_session_is_titled_by_its_first_reply_unless_the_user_named_it(tmp_path):
    async def reply(config, prompt, mode, root):
        assert ('Title: ' in prompt) == ('first question' in prompt.split('USER:')[-1]), 'Only the first turn asks for a title.'
        return AgentResult('Title: Widget inventory\nThere are three widgets.', 10, 5)
    store, runner, project, session = make(tmp_path, ScriptedAgent(respond=reply))
    session = await turn(runner, store, 'tenant-a', project, session, 'This is the first question about widgets.')
    assert session['name'] == 'Widget inventory' and session['messages'][-1]['content'] == 'There are three widgets.'
    assert session['auto_named'] is False
    session['name'] = 'My own name'
    store.put('tenant-a', 'sessions', session)
    session = await turn(runner, store, 'tenant-a', project, session, 'And a follow-up.')
    assert session['name'] == 'My own name'


def test_the_context_meter_counts_what_the_agent_is_sent():
    session = {'messages': [{'id': 'a', 'role': 'user', 'content': 'x'*70_000}, {'id': 'b', 'role': 'assistant', 'content': 'y'*70_000, 'model_name': 'Claude'},
                            {'id': 'c', 'role': 'user', 'content': 'latest'}]}
    usage = context_usage(session)
    assert usage['limit'] == TRANSCRIPT_LIMIT and usage['chars'] > TRANSCRIPT_LIMIT and usage['dropped'] == 1 and usage['compacted'] == 0
    session['summary'] = {'text': 'Earlier: a long exchange.', 'through': 'b', 'count': 2}
    usage = context_usage(session)
    assert usage['dropped'] == 0 and usage['compacted'] == 2 and usage['chars'] < 200
    prompt = build_prompt([session['messages'][2]], switched=False, summary='Earlier: a long exchange.')
    assert 'compacted into this summary' in prompt and 'Earlier: a long exchange.' in prompt and 'x'*100 not in prompt


@pytest.mark.asyncio
async def test_compacting_replaces_what_the_agent_sees_but_keeps_the_conversation(tmp_path):
    prompts = []
    async def reply(config, prompt, mode, root):
        prompts.append(prompt)
        if prompt.startswith('Write a handoff summary'):
            assert mode == 'read'
            return AgentResult('The user asked about widgets; there are three.', 10, 5)
        return AgentResult('Answered.', 10, 5)
    store, runner, project, session = make(tmp_path, ScriptedAgent(respond=reply))
    with pytest.raises(ValueError):
        await runner.compact('tenant-a', project['id'], session['id'])
    session = await turn(runner, store, 'tenant-a', project, session, 'How many widgets?')
    session = await runner.compact('tenant-a', project['id'], session['id'])
    assert session['summary']['text'].startswith('The user asked') and session['summary']['count'] == 2 and len(session['messages']) == 2
    session = await turn(runner, store, 'tenant-a', project, session, 'And gadgets?')
    assert 'compacted into this summary' in prompts[-1] and 'How many widgets?' not in prompts[-1] and 'And gadgets?' in prompts[-1]
    assert context_usage(session)['compacted'] == 2
