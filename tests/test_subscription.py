"""Subscription providers run a local agent CLI instead of an HTTP API with a key."""
import json
import sys
import pytest
from backend import broker
from backend.engine import Engine
from backend.schemas import BuildArtifact, ModelConfig
from tests.test_engine import setup_store, ScriptedBroker

def connect(store, provider, model_name='sonnet'):
    config = ModelConfig(name='Subscription', provider=provider, model_name=model_name)
    return store.put('tenant-a', 'models', {'id': provider, **config.model_dump(exclude={'api_key'})})

def test_subscription_config_rejects_credentials_and_rates_usage_at_zero():
    config = ModelConfig(name='Claude', provider='claude_cli', model_name='sonnet')
    # Zero rather than unknown: unknown pricing refuses every run while a budget is set.
    assert config.input_price == 0.0 and config.output_price == 0.0
    with pytest.raises(ValueError):
        ModelConfig(name='Claude', provider='claude_cli', model_name='sonnet', api_key='sk-should-not-be-accepted')
    with pytest.raises(ValueError):
        ModelConfig(name='Claude', provider='claude_cli', model_name='sonnet', base_url='http://127.0.0.1:9100/v1')

def test_strict_schema_closes_objects_without_touching_open_maps():
    strict = broker.strict_schema(BuildArtifact.model_json_schema())
    # Codex rejects a schema whose `required` omits a property, and Pydantic omits defaults.
    assert strict['required'] == ['summary', 'files', 'commands']
    assert strict['additionalProperties'] is False
    assert strict['$defs']['ArtifactFile']['required'] == ['name', 'content']
    assert 'commands' not in BuildArtifact.model_json_schema()['required']
    dependencies = broker.strict_schema({'type': 'object', 'properties': {'m': {'type': 'object', 'additionalProperties': {'type': 'string'}}}})
    assert dependencies['properties']['m']['additionalProperties'] == {'type': 'string'}

def test_resolver_prefers_an_executable_and_explains_a_missing_node(tmp_path, monkeypatch):
    shim = tmp_path/'codex.cmd'
    shim.write_text('@echo off', encoding='utf-8')
    entry = tmp_path/'node_modules/@openai/codex/bin'
    entry.mkdir(parents=True)
    (entry/'codex.js').write_text('', encoding='utf-8')
    monkeypatch.setattr(broker.shutil, 'which', lambda name: str(shim) if name == 'codex' else None)
    # A shim cannot be started directly, and without Node the reason must say so rather than
    # claiming the tool is missing when it is sitting right there.
    with pytest.raises(broker.ProviderError, match='Node.js'):
        broker.resolve_cli('codex_cli')
    monkeypatch.setattr(broker.shutil, 'which', lambda name: str(tmp_path/'node.exe') if name == 'node' else str(shim))
    assert broker.resolve_cli('codex_cli') == [str(tmp_path/'node.exe'), str(entry/'codex.js')]
    monkeypatch.setattr(broker.shutil, 'which', lambda name: str(tmp_path/'claude.exe') if name == 'claude' else None)
    assert broker.resolve_cli('claude_cli') == [str(tmp_path/'claude.exe')]

@pytest.mark.asyncio
async def test_claude_cli_is_denied_ambient_keys_and_reports_usage(tmp_path, monkeypatch):
    store = setup_store(tmp_path)
    connect(store, 'claude_cli')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-never-reach-the-cli')
    stub = ('import json,os,sys;sys.stdin.read();'
            'print(json.dumps({"result":json.dumps({"ambient_key_visible":bool(os.environ.get("ANTHROPIC_API_KEY"))}),'
            '"usage":{"input_tokens":11,"output_tokens":7},"is_error":False}))')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', stub])
    result = await broker.ModelBroker(store).invoke('tenant-a', 'claude_cli', 'System.', 'Prompt.', {'timeout': 60, 'max_tokens': 1000})
    # An inherited key would silently bill per token instead of using the subscription.
    assert json.loads(result.text) == {'ambient_key_visible': False}
    assert (result.input_tokens, result.output_tokens) == (11, 7)

@pytest.mark.asyncio
async def test_codex_cli_enforces_the_contract_through_its_schema_file(tmp_path, monkeypatch):
    store = setup_store(tmp_path)
    connect(store, 'codex_cli', 'gpt-5.6-sol')
    stub = ('import pathlib,sys;a=sys.argv;sys.stdin.read();'
            'pathlib.Path(a[a.index("--output-last-message")+1]).write_text('
            'pathlib.Path(a[a.index("--output-schema")+1]).read_text(encoding="utf-8"),encoding="utf-8")')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', stub])
    result = await broker.ModelBroker(store).invoke('tenant-a', 'codex_cli', 'System.', 'Prompt.', {'timeout': 60, 'max_tokens': 1000}, BuildArtifact.model_json_schema())
    # The final message is read from the file the CLI writes, not from its progress output.
    assert json.loads(result.text)['required'] == ['summary', 'files', 'commands']
    assert result.input_tokens is None  # Codex reports only a combined total.

@pytest.mark.asyncio
async def test_failed_cli_names_the_recovery_without_echoing_stderr(tmp_path, monkeypatch):
    store = setup_store(tmp_path)
    connect(store, 'claude_cli')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', 'import sys;sys.stderr.write("prompt text and secrets");sys.exit(3)'])
    with pytest.raises(broker.ProviderError) as raised:
        await broker.ModelBroker(store).invoke('tenant-a', 'claude_cli', 'System.', 'Prompt.', {'timeout': 60, 'max_tokens': 1000})
    assert 'signed in' in str(raised.value) and 'secrets' not in str(raised.value)

@pytest.mark.asyncio
async def test_zero_rated_model_keeps_a_budget_usable_without_token_counts(tmp_path):
    store = setup_store(tmp_path)
    tenant = store.tenant_internal('tenant-a')
    tenant['monthly_budget'] = 5.0
    with store.db() as db:
        db.execute('UPDATE tenants SET data=? WHERE id=?', (json.dumps(tenant), 'tenant-a'))
    for role, provider in [('discussion', 'anthropic'), ('planning', 'anthropic'), ('building', 'openai'), ('review', 'custom_openai')]:
        model = store.get('tenant-a', 'models', f'{provider}/{role}')
        model.update(input_price=0.0, output_price=0.0)
        store.put('tenant-a', 'models', model)

    class UncountedBroker(ScriptedBroker):
        async def invoke(self, *args, **kwargs):
            result = await super().invoke(*args, **kwargs)
            result.input_tokens = result.output_tokens = None  # What a subscription CLI reports.
            return result

    engine = Engine(store, UncountedBroker())
    run = engine.start('tenant-a', 'workflow-tenant-a')
    await engine.tasks[('tenant-a', run['id'])]
    final = store.get('tenant-a', 'runs', run['id'])
    assert final['status'] == 'complete', final['error']
    # Cost is known to be zero, so the month stays usable; only token counts are partial.
    assert final['cost_complete'] is True and final['cost'] == 0.0 and final['usage_complete'] is False
