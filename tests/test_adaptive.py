"""Adaptive picks the least expensive agent that covers a message, and escalates when it fails."""
import asyncio
import json
from pathlib import Path
import pytest
from backend import adaptive
from backend.adaptive import ADAPTIVE, TaskRequirements
from backend.agent import AgentRunner
from backend.broker import AgentResult, ProviderError
from backend.schemas import ModelConfig
from tests.harness import ScriptedAgent, open_project, setup_store, turn

# The three agents the specification names, as model rows. Nothing below refers to them by
# provider: the router sees only what each row advertises.
QWEN = {'id': 'qwen', 'name': 'Qwen local', 'provider': 'opencode_cli', 'model_name': 'ollama/qwen3.5:27b'}
CODEX = {'id': 'codex', 'name': 'Codex', 'provider': 'codex_cli', 'model_name': 'gpt-5.6-sol'}
CLAUDE = {'id': 'claude', 'name': 'Claude', 'provider': 'claude_cli', 'model_name': 'opus'}
KEYED = {'id': 'keyed', 'name': 'Keyed', 'provider': 'openai', 'model_name': 'gpt-5.6-terra'}
FLEET = [QWEN, CODEX, CLAUDE, KEYED]


def pick(text, models=FLEET, repo=None):
    requirements = adaptive.heuristic_requirements(text, repo or {'file_count': 40, 'languages': ['.py'], 'flagged': []})
    chosen, ranked = adaptive.choose(requirements, models)
    return requirements, chosen, ranked


@pytest.mark.parametrize('text,expected', [
    ('Change this button from blue to green.', 'qwen'),
    ('Explain what this function does.', 'qwen'),
    ('Rename this component everywhere it is referenced.', 'codex'),
    ('Add pagination to the customer API.', 'codex'),
    ('Find and fix why this API intermittently returns 500.', 'codex'),
    ('Analyze our system and determine why these services are so tightly coupled.', 'claude'),
    ('Design a zero-downtime migration strategy for this database.', 'claude'),
    ('Review this implementation for architectural and security problems.', 'claude'),
])
def test_the_specified_examples_route_where_the_specification_expects(text, expected):
    requirements, chosen, _ = pick(text)
    assert chosen['id'] == expected, (text, requirements.reason, chosen)


def test_length_is_not_the_signal():
    # Short and hard: security plus ambiguity raise reasoning and review past the cheap agents.
    short, short_pick, _ = pick('Fix authentication.')
    assert short.risk != 'low' and short_pick['id'] != 'qwen'
    # Long and easy: thirty deterministic replacements still read as a simple edit.
    long_text = 'Change the label text ' + ' and '.join(f'from "{i}" to "{i+1}"' for i in range(30)) + '.'
    long_req, long_pick, _ = pick(long_text)
    assert long_req.task_type == 'edit_simple' and long_pick['id'] == 'qwen'


def test_cheapest_sufficient_wins_and_a_keyed_model_is_never_a_candidate():
    _, chosen, ranked = pick('Explain what this function does.')
    assert chosen['cost_class'] == 'free' and chosen['location'] == 'local'
    assert 'keyed' not in {r['id'] for r in ranked}  # Hard requirement: a turn needs an agent loop.
    # Sufficient candidates come first, cheapest to dearest; insufficient ones after.
    assert [r['id'] for r in ranked] == ['qwen', 'codex', 'claude']


def test_a_future_agent_announces_itself_through_its_row_not_the_router():
    coder = {'id': 'future-coder', 'name': 'Future local coder', 'provider': 'opencode_cli', 'model_name': 'ollama/future-coder',
             'capabilities': {'coding': 9, 'debugging': 8, 'repository': 8, 'tool_use': 8, 'instruction_following': 8}}
    _, chosen, _ = pick('Add pagination to the customer API.', FLEET + [coder])
    # Local and free, and now sufficient for implementation work: it beats Codex on cost alone.
    assert chosen['id'] == 'future-coder'
    # The same numbers are refused when they are not capabilities or not 0-10.
    with pytest.raises(ValueError):
        ModelConfig(name='x', provider='opencode_cli', model_name='ollama/x', capabilities={'charisma': 5})
    with pytest.raises(ValueError):
        ModelConfig(name='x', provider='opencode_cli', model_name='ollama/x', capabilities={'coding': 11})


def test_the_repository_raises_the_stakes_before_any_agent_looks():
    calm = {'file_count': 40, 'languages': ['.py'], 'flagged': []}
    flagged = {'file_count': 40, 'languages': ['.py'], 'flagged': ['security', 'data']}
    easy, easy_pick, _ = pick('Fix the login timeout.', repo=calm)
    hard, hard_pick, _ = pick('Fix the login timeout.', repo=flagged)
    assert hard.risk == 'high' and easy.risk != 'high'
    assert hard.need('reasoning') >= easy.need('reasoning') and hard.need('architecture') > easy.need('architecture')


def test_stronger_stops_when_nothing_stronger_is_left():
    _, _, ranked = pick('Add pagination to the customer API.')
    nxt = adaptive.stronger(ranked, {'qwen'})
    assert nxt and nxt['id'] == 'codex'
    nxt = adaptive.stronger(ranked, {'qwen', 'codex'})
    assert nxt and nxt['id'] == 'claude'
    assert adaptive.stronger(ranked, {'qwen', 'codex', 'claude'}) is None  # The loop is finite.


def test_struggle_is_a_failure_or_a_stated_inability_with_nothing_done():
    assert adaptive.struggled('', 'The tool exited with code 1.', 'edit', [])
    assert adaptive.struggled('I was unable to find the handler for this route.', None, 'edit', [])
    # An ordinary mistake in a productive attempt is not a reason to switch.
    assert adaptive.struggled('I could not find a test for it, so I added one.', None, 'edit', [{'path': 'a.py'}]) is None
    assert adaptive.struggled('Done.', None, 'edit', []) is None


class FakeTypesafeResponse:
    def __init__(self, answers):
        self._answers = answers
    def raise_for_status(self):
        pass
    def json(self):
        return {'answers': self._answers}


class FakeTypesafeClient:
    """Stands in for httpx.AsyncClient so a test controls TypeSafe's answers directly,
    the same way ScriptedAgent stands in for a real agent tool."""
    def __init__(self, answers=None, sent=None, **_):
        self.answers = answers or {}
        self._sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        if self._sent is not None:
            self._sent.append(json)
        return FakeTypesafeResponse(self.answers)


def choice(value, confidence=0.95):
    return {'type': 'choice', 'choice': value, 'confidence': confidence, 'probabilities': {value: confidence}}


def noul(value):
    return {'type': 'noul', 'noul': value}


@pytest.mark.asyncio
async def test_typesafe_requirements_reads_answers_through_the_same_derivation_as_heuristics(monkeypatch):
    sent = []
    answers = {'task_type': choice('planning', 0.9), 'broad_scope': noul(0.85), 'ambiguous': noul(0.9),
              'security_risk': noul(0.1), 'data_risk': noul(0.98), 'money_risk': noul(0.1), 'external_risk': noul(0.1)}
    monkeypatch.setattr(adaptive.httpx, 'AsyncClient', lambda **kw: FakeTypesafeClient(answers, sent))
    repo = {'file_count': 40, 'languages': ['.py'], 'flagged': []}
    result = await adaptive.typesafe_requirements('Design a zero-downtime migration strategy.', repo, 'sk-test', 'jev-latest')
    # planning is not in AMBIGUITY_APPLIES_TO, so a high `ambiguous` noul is ignored for it -
    # the same restriction the regex heuristic places on its own ambiguous signal.
    heuristic_planning = adaptive._derive('planning', True, False, ['data'], repo)
    assert result.requirements == heuristic_planning.requirements
    assert result.complexity == 'high' and result.risk == 'high'
    assert '0.90 confidence' in result.reason
    # The request actually sent to TypeSafe carries the request text and the repo signals, and
    # nothing else - no project file contents.
    assert sent[0]['state'] == {'request': 'Design a zero-downtime migration strategy.', 'repository': repo}
    assert set(sent[0]['questions']) == {'task_type', 'broad_scope', 'ambiguous', 'security_risk', 'data_risk', 'money_risk', 'external_risk'}


@pytest.mark.asyncio
async def test_ambiguous_noul_only_applies_to_tasks_with_a_determinate_target(monkeypatch):
    # "Fix authentication." - debugging IS in AMBIGUITY_APPLIES_TO, so a high ambiguous noul
    # should raise reasoning/repository the same way the regex heuristic's own ambiguous case does.
    answers = {'task_type': choice('debugging', 0.95), 'broad_scope': noul(0.1), 'ambiguous': noul(0.95),
              'security_risk': noul(0.9), 'data_risk': noul(0.1), 'money_risk': noul(0.1), 'external_risk': noul(0.1)}
    monkeypatch.setattr(adaptive.httpx, 'AsyncClient', lambda **kw: FakeTypesafeClient(answers))
    repo = {'file_count': 10, 'languages': [], 'flagged': []}
    result = await adaptive.typesafe_requirements('Fix authentication.', repo, 'sk-test')
    expected = adaptive._derive('debugging', False, True, ['security'], repo)
    assert result.requirements == expected.requirements
    assert 'ambiguous' in result.reason


@pytest.mark.asyncio
async def test_typesafe_requirements_returns_none_on_failure_rather_than_raising(monkeypatch):
    class BoomClient(FakeTypesafeClient):
        async def post(self, *a, **kw):
            raise RuntimeError('network is down')
    monkeypatch.setattr(adaptive.httpx, 'AsyncClient', lambda **kw: BoomClient())
    result = await adaptive.typesafe_requirements('Explain this.', {'file_count': 0, 'languages': [], 'flagged': []}, 'sk-test')
    assert result is None


@pytest.mark.asyncio
async def test_classify_dispatches_typesafe_models_and_falls_back_without_a_key(monkeypatch):
    # No stored key at all -> heuristics immediately, never calls out.
    unkeyed = {'id': 'ts', 'name': 'TypeSafe', 'provider': 'typesafe', 'model_name': 'jev-latest', 'encrypted_key': None}
    requirements, by = await adaptive.classify('Explain this.', {'file_count': 0, 'languages': [], 'flagged': []}, unkeyed, lambda v: v)
    assert by == 'heuristics (classifier unavailable)' and requirements.task_type == 'explain'

    # A working key dispatches to typesafe_requirements and is credited by the model's own name.
    keyed = {**unkeyed, 'encrypted_key': 'enc'}
    async def fake_typesafe(content, repo, api_key, model):
        assert api_key == 'sk-decrypted' and model == 'jev-latest'
        return adaptive._derive('explain', False, False, [], repo)
    monkeypatch.setattr(adaptive, 'typesafe_requirements', fake_typesafe)
    requirements, by = await adaptive.classify('Explain this.', {'file_count': 0, 'languages': [], 'flagged': []}, keyed, lambda v: 'sk-decrypted')
    assert by == 'TypeSafe' and requirements.task_type == 'explain'


@pytest.mark.asyncio
async def test_a_failed_classifier_falls_back_to_heuristics():
    bogus = {'id': 'x', 'name': 'Bogus', 'provider': 'custom_openai', 'model_name': 'x', 'base_url': 'http://127.0.0.1:9/v1', 'encrypted_key': None}
    requirements, by = await adaptive.classify('Explain what this function does.', {'file_count': 1, 'languages': [], 'flagged': []}, bogus, lambda v: v)
    assert requirements.task_type == 'explain' and 'heuristics' in by


def make(tmp_path, respond):
    store = setup_store(tmp_path/'state')
    for row in (QWEN, CODEX, CLAUDE):
        config = ModelConfig(name=row['name'], provider=row['provider'], model_name=row['model_name'])
        store.put('tenant-a', 'models', {'id': row['id'], **config.model_dump(exclude={'api_key'}), 'encrypted_key': None, 'key_hint': 'Subscription login', 'status': 'connected'})
    for row in ('claude_cli', 'codex_cli', 'opencode_cli'):
        store.delete('tenant-a', 'models', row)  # Only the three named agents take part.
    folder = tmp_path/'work'
    folder.mkdir()
    (folder/'app.py').write_text('print(1)\n', encoding='utf-8')
    agent = ScriptedAgent(respond=respond)
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    return store, runner, agent, project, session, folder


@pytest.mark.asyncio
async def test_an_adaptive_turn_chooses_an_agent_and_records_why(tmp_path):
    async def respond(config, prompt, mode, root):
        return AgentResult(f'answered by {config["name"]}', 10, 5)
    store, runner, agent, project, session, _ = make(tmp_path, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Explain what app.py does.', ADAPTIVE, 'read')
    reply = session['messages'][-1]
    assert reply['status'] == 'complete' and reply['model_id'] == 'qwen' and reply['content'] == 'answered by Qwen local'
    routing = reply['routing']
    assert routing['mode'] == 'adaptive' and routing['chosen']['id'] == 'qwen' and routing['escalations'] == 0
    assert routing['requirements']['task_type'] == 'explain' and 'cheapest' in routing['chosen']['because']
    assert routing['candidates'][0]['id'] == 'qwen'


@pytest.mark.asyncio
async def test_a_failing_agent_hands_the_same_turn_to_a_stronger_one_with_a_handoff(tmp_path):
    async def respond(config, prompt, mode, root):
        if config['id'] == 'qwen':
            (Path(root)/'partial.txt').write_text('half', encoding='utf-8')
            raise ProviderError('The OpenCode command line tool exited with code 1.')
        return AgentResult(f'finished by {config["name"]}', 10, 5)
    store, runner, agent, project, session, folder = make(tmp_path, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Change the printed number to 2.', ADAPTIVE, 'edit')
    reply = session['messages'][-1]
    assert reply['status'] == 'complete' and reply['model_id'] == 'codex' and reply['content'] == 'finished by Codex'
    assert reply['routing']['escalations'] == 1
    assert [a['name'] for a in reply['routing']['attempts']] == ['Qwen local', 'Codex']
    assert 'exited with code 1' in reply['routing']['attempts'][0]['outcome']
    # The stronger agent was told what happened, and what is already on disk, rather than restarted cold.
    handed = agent.calls[-1]['prompt']
    assert 'HANDOFF FROM A PREVIOUS AGENT' in handed and 'Qwen local' in handed and 'partial.txt' in handed
    assert 'Change the printed number to 2.' in handed
    assert any(c['path'] == 'partial.txt' for c in reply['changes'])  # The turn's record spans both attempts.


@pytest.mark.asyncio
async def test_an_agent_that_says_it_cannot_and_changed_nothing_is_escalated(tmp_path):
    async def respond(config, prompt, mode, root):
        if config['id'] == 'qwen':
            return AgentResult('I am unable to complete this without more context.', 5, 5)
        return AgentResult('Implemented.', 10, 5)
    store, runner, agent, project, session, _ = make(tmp_path, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Update the greeting text.', ADAPTIVE, 'edit')
    reply = session['messages'][-1]
    assert reply['model_id'] == 'codex' and reply['routing']['escalations'] == 1
    assert 'could not complete' in reply['routing']['attempts'][0]['outcome']


@pytest.mark.asyncio
async def test_escalation_is_capped_and_ends_in_a_reported_failure(tmp_path):
    async def respond(config, prompt, mode, root):
        raise ProviderError(f'{config["name"]} exited with code 1.')
    store, runner, agent, project, session, _ = make(tmp_path, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Update the greeting text.', ADAPTIVE, 'edit')
    reply = session['messages'][-1]
    assert reply['status'] == 'failed' and reply['routing']['escalations'] == 2
    assert [c['model_id'] for c in agent.calls] == ['qwen', 'codex', 'claude']  # Once each, then stop.
    assert not runner.busy('tenant-a', session['id'])


@pytest.mark.asyncio
async def test_a_manual_choice_is_never_second_guessed(tmp_path):
    async def respond(config, prompt, mode, root):
        raise ProviderError(f'{config["name"]} exited with code 1.')
    store, runner, agent, project, session, _ = make(tmp_path, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Explain app.py.', 'qwen', 'read')
    reply = session['messages'][-1]
    assert reply['status'] == 'failed' and reply['model_id'] == 'qwen' and reply['routing'] == {'mode': 'manual'}
    assert [c['model_id'] for c in agent.calls] == ['qwen']


@pytest.mark.asyncio
async def test_adaptive_needs_an_agent_and_a_disabled_row_is_not_one(tmp_path):
    async def respond(config, prompt, mode, root):
        return AgentResult('ok', 1, 1)
    store, runner, agent, project, session, _ = make(tmp_path, respond)
    for row in ('qwen', 'codex', 'claude'):
        model = store.get('tenant-a', 'models', row)
        model['enabled'] = False
        store.put('tenant-a', 'models', model)
    with pytest.raises(ValueError, match='at least one'):
        runner.send('tenant-a', project['id'], session['id'], 'hello', ADAPTIVE, 'read')


def test_requirements_are_validated_not_trusted():
    with pytest.raises(ValueError):
        TaskRequirements(task_type='wizardry', complexity='low', risk='low', requirements={})
    with pytest.raises(ValueError):
        TaskRequirements(task_type='explain', complexity='enormous', risk='low', requirements={})


def test_codex_families_have_their_own_default_profiles():
    from backend.adaptive import profile
    row = lambda name: {'id': name, 'name': name, 'provider': 'codex_cli', 'model_name': name}
    astra, sol, luna = profile(row('gpt-6-astra')), profile(row('gpt-6-sol')), profile(row('gpt-6-luna'))
    assert (astra['cost_class'], sol['cost_class'], luna['cost_class']) == ('high', 'medium', 'low')
    assert astra['reasoning'] > sol['reasoning'] > luna['reasoning'] and luna['speed'] > astra['speed']
    assert profile(row('gpt-5.6-sol')) == {**profile(row('gpt-5.5')), 'id': 'gpt-5.6-sol', 'name': 'gpt-5.6-sol'}  # Older rows are unchanged.
    # The row's own numbers still win over the family default.
    custom = profile({**row('gpt-6-luna'), 'capabilities': {'coding': 9}, 'cost_class': 'free'})
    assert custom['coding'] == 9 and custom['cost_class'] == 'free'
