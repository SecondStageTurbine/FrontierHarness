"""Only one local model can hold the GPU at a time; a turn must never be sent to the others."""
import json
import pytest
from backend import localhealth
from backend.adaptive import ADAPTIVE
from backend.agent import AgentRunner
from backend.broker import AgentResult
from backend.schemas import ModelConfig
from tests.harness import ScriptedAgent, open_project, setup_store, turn

CONFIG = {'provider': {
    'big':    {'options': {'baseURL': 'http://127.0.0.1:9093/v1'}, 'models': {'Prometheus': {}}},
    'coder':  {'options': {'baseURL': 'http://localhost:9095/v1'}, 'models': {'qwen3-coder': {}}},
    'ollama': {'options': {'baseURL': 'http://127.0.0.1:11434/v1'}, 'models': {'gemma4': {}}},
    'cloud':  {'options': {'baseURL': 'https://api.example.com/v1'}, 'models': {'x': {}}},
}}


@pytest.fixture
def opencode_config(monkeypatch, tmp_path):
    path = tmp_path/'opencode.json'
    path.write_text(json.dumps(CONFIG), encoding='utf-8')
    monkeypatch.setenv('OPENCODE_CONFIG', str(path))
    localhealth._cache.clear()
    return path


def test_only_loopback_providers_are_local_servers(opencode_config):
    providers = localhealth.local_providers()
    assert set(providers) == {'big', 'coder', 'ollama'}  # The cloud provider is not ours to probe.
    assert providers['big'] == 'http://127.0.0.1:9093/v1'
    assert localhealth.server_for({'provider': 'opencode_cli', 'model_name': 'big/Prometheus'}, providers) == ('big', 'http://127.0.0.1:9093/v1', 'Prometheus')
    assert localhealth.server_for({'provider': 'opencode_cli', 'model_name': 'cloud/x'}, providers) is None
    assert localhealth.server_for({'provider': 'claude_cli', 'model_name': 'sonnet'}, providers) is None


def test_ollama_tags_and_llama_server_aliases_both_match():
    assert localhealth.serves(['gemma4:latest'], 'gemma4') and localhealth.serves(['Prometheus'], 'Prometheus')
    assert not localhealth.serves(['nomic-embed-text:latest'], 'gemma4')


@pytest.mark.asyncio
async def test_availability_reports_up_down_and_not_applicable(opencode_config, monkeypatch):
    async def fake(base):
        return {'http://127.0.0.1:9093/v1': ['Prometheus'], 'http://localhost:9095/v1': None, 'http://127.0.0.1:11434/v1': ['nomic-embed-text:latest']}[base]
    monkeypatch.setattr(localhealth, 'served_models', fake)
    models = [{'id': 'a', 'provider': 'opencode_cli', 'model_name': 'big/Prometheus'},
              {'id': 'b', 'provider': 'opencode_cli', 'model_name': 'coder/qwen3-coder'},
              {'id': 'c', 'provider': 'opencode_cli', 'model_name': 'ollama/gemma4'},
              {'id': 'd', 'provider': 'codex_cli', 'model_name': 'gpt-5.6-sol'}]
    assert await localhealth.availability(models) == {'a': True, 'b': False, 'c': False, 'd': None}


def make(tmp_path, respond):
    store = setup_store(tmp_path/'state')
    for row in ('claude_cli', 'codex_cli', 'opencode_cli'):
        store.delete('tenant-a', 'models', row)
    for mid, name, provider, model_name in [('big', 'Big local', 'opencode_cli', 'big/Prometheus'),
                                            ('coder', 'Coder local', 'opencode_cli', 'coder/qwen3-coder'),
                                            ('codex', 'Codex', 'codex_cli', 'gpt-5.6-sol')]:
        config = ModelConfig(name=name, provider=provider, model_name=model_name)
        store.put('tenant-a', 'models', {'id': mid, **config.model_dump(exclude={'api_key'}), 'encrypted_key': None, 'key_hint': 'Subscription login', 'status': 'connected'})
    folder = tmp_path/'work'
    folder.mkdir()
    (folder/'app.py').write_text('print(1)\n', encoding='utf-8')
    agent = ScriptedAgent(respond=respond)
    runner = AgentRunner(store, agent)
    project, session = open_project(store, runner, 'tenant-a', folder)
    return store, runner, agent, project, session


@pytest.mark.asyncio
async def test_adaptive_skips_the_local_agent_whose_server_is_down(opencode_config, monkeypatch):
    async def fake(base):
        return ['Prometheus'] if base.endswith('9093/v1') else None  # Only the big model holds the GPU right now.
    monkeypatch.setattr(localhealth, 'served_models', fake)
    async def respond(config, prompt, mode, root):
        return AgentResult(f'answered by {config["name"]}', 1, 1)
    store, runner, agent, project, session = make(tmp_path := opencode_config.parent, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Explain app.py.', ADAPTIVE, 'read')
    reply = session['messages'][-1]
    # Both locals are free and both cover an explanation; only the running one is a candidate.
    assert reply['model_id'] == 'big' and reply['routing']['offline'] == ['Coder local']
    assert [c['model_id'] for c in agent.calls] == ['big']


@pytest.mark.asyncio
async def test_a_manual_pick_of_an_offline_local_agent_fails_fast_with_the_address(opencode_config, monkeypatch):
    async def fake(base):
        return None
    monkeypatch.setattr(localhealth, 'served_models', fake)
    async def respond(config, prompt, mode, root):
        return AgentResult('should not run', 1, 1)
    store, runner, agent, project, session = make(opencode_config.parent, respond)
    session = await turn(runner, store, 'tenant-a', project, session, 'Explain app.py.', 'coder', 'read')
    reply = session['messages'][-1]
    assert reply['status'] == 'failed' and 'coder at http://localhost:9095/v1' in reply['error']
    assert agent.calls == []  # No turn was spent on a server that is not there.
