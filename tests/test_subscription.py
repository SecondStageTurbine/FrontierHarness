"""Subscription logins run a local agent tool; several can be connected to one provider."""
import json
import os
import sys
import pytest
from backend import broker
from backend.schemas import ModelConfig
from tests.harness import setup_store


def connect(store, provider, model_name='sonnet', account=None):
    config = ModelConfig(name=account or 'Subscription', provider=provider, model_name=model_name, account=account)
    return store.put('tenant-a', 'models', {'id': account or provider, **config.model_dump(exclude={'api_key'})})


def project(tmp_path):
    folder = tmp_path/'work'
    folder.mkdir(exist_ok=True)
    return str(folder)


# Answers with the credential directory it was pointed at, so a test can see which login ran.
# The named account 'spent' refuses the way a subscription with nothing left refuses.
CLAUDE_STUB = '''
import json, os, sys
sys.stdin.read()
home = os.environ.get('CLAUDE_CONFIG_DIR', '')
if home.endswith('spent'):
    print(json.dumps({'result': 'Claude usage limit reached. The limit will reset later.', 'is_error': True}))
    sys.stderr.write('prompt text and secrets')
    sys.exit(1)
print(json.dumps({'result': home, 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'is_error': False}))
'''

# What the Claude tool actually answers for an account that was never signed in: the error in
# `result`, and a reason that has nothing to do with running out of usage.
UNSIGNED_STUB = '''
import json, sys
sys.stdin.read()
print(json.dumps({'result': 'Not logged in \\u00b7 Please run /login', 'is_error': True}))
'''

OPENCODE_STUB = '''
import json, os, sys
sys.stdin.read()
home = os.environ.get('XDG_DATA_HOME', '')
for event in [{'type': 'step_start', 'part': {'type': 'step-start'}},
              {'type': 'text', 'part': {'type': 'text', 'text': 'answered from ' + home}},
              {'type': 'step_finish', 'part': {'type': 'step-finish', 'tokens': {'input': 28645, 'output': 3}}}]:
    print(json.dumps(event))
'''


def test_subscription_config_rejects_credentials_and_rates_usage_at_zero():
    config = ModelConfig(name='Claude', provider='claude_cli', model_name='sonnet')
    # Zero rather than unknown: the subscription already covers the usage.
    assert config.input_price == 0.0 and config.output_price == 0.0
    with pytest.raises(ValueError):
        ModelConfig(name='Claude', provider='claude_cli', model_name='sonnet', api_key='sk-should-not-be-accepted')
    with pytest.raises(ValueError):
        ModelConfig(name='Claude', provider='claude_cli', model_name='sonnet', base_url='http://127.0.0.1:9100/v1')


def test_a_named_account_belongs_only_to_a_subscription_login():
    assert ModelConfig(name='Second seat', provider='claude_cli', model_name='opus', account='seat-2').account == 'seat-2'
    with pytest.raises(ValueError):
        ModelConfig(name='Keyed', provider='openai', model_name='gpt-5.6-terra', api_key='sk-x', account='seat-2')
    with pytest.raises(ValueError):
        ModelConfig(name='Traversal', provider='claude_cli', model_name='opus', account='../../secret.key')


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


def test_each_mode_becomes_the_matching_power_in_every_tool(tmp_path):
    root, final = project(tmp_path), tmp_path/'final.txt'
    powers = {}
    for provider in ('claude_cli', 'codex_cli', 'opencode_cli'):
        powers[provider] = {mode: broker.agent_argv(provider, ['tool'], 'model', mode, root, final) for mode in ('read', 'edit', 'auto')}
    # Read only must never be able to write, and full auto must never wait for an approval no
    # one is there to give; both are the difference between the postures and a hung turn.
    read = powers['claude_cli']['read']
    # Read only is the ordinary agent minus its writing and shell tools, not plan mode, which
    # writes a plan file into the tool's own home and answers about it.
    assert read[read.index('--permission-mode')+1] == 'dontAsk' and 'plan' not in read
    assert {'Bash', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'} <= set(read[read.index('--disallowedTools')+1:])
    assert powers['claude_cli']['edit'][-1] == 'acceptEdits'
    assert powers['claude_cli']['auto'][-1] == 'bypassPermissions'
    assert '--sandbox' in powers['codex_cli']['read'] and powers['codex_cli']['read'][powers['codex_cli']['read'].index('--sandbox')+1] == 'read-only'
    assert powers['codex_cli']['edit'][powers['codex_cli']['edit'].index('--sandbox')+1] == 'workspace-write'
    assert '--dangerously-bypass-approvals-and-sandbox' in powers['codex_cli']['auto']
    assert powers['codex_cli']['edit'][:3] == ['tool', '-a', 'never']  # A global flag; after `exec` it is not read.
    assert powers['opencode_cli']['read'][-1] == 'plan' and powers['opencode_cli']['edit'][-1] == 'build'
    assert powers['opencode_cli']['auto'][-1] == '--auto'
    # Every tool is told which model to use, whatever the posture.
    for provider, modes in powers.items():
        assert all('model' in argv for argv in modes.values()), provider


@pytest.mark.asyncio
async def test_an_agent_is_denied_ambient_keys_and_runs_in_the_project_folder(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'claude_cli')
    root = project(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-never-reach-the-agent')
    stub = ('import json,os,sys;sys.stdin.read();'
            'print(json.dumps({"result":json.dumps({"key":bool(os.environ.get("ANTHROPIC_API_KEY")),"cwd":os.getcwd()}),'
            '"usage":{"input_tokens":11,"output_tokens":7},"is_error":False}))')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', stub])
    config = store.get('tenant-a', 'models', 'claude_cli')
    result = await broker.ModelBroker(store).invoke_agent('tenant-a', config, 'Prompt.', 'edit', root)
    answered = json.loads(result.text)
    # An inherited key would bill per token instead of using the subscription, and the folder is
    # the working directory because that is how the agent's own tools reach the project at all.
    assert answered['key'] is False and os.path.samefile(answered['cwd'], root)
    assert (result.input_tokens, result.output_tokens) == (11, 7)


@pytest.mark.asyncio
async def test_a_spent_subscription_hands_the_turn_to_the_next_connected_one(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'claude_cli', 'opus', account='spent')
    connect(store, 'claude_cli', 'opus', account='spare')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', CLAUDE_STUB])
    model_broker = broker.ModelBroker(store)
    config = store.get('tenant-a', 'models', 'spent')
    result = await model_broker.invoke_agent('tenant-a', config, 'Prompt.', 'edit', project(tmp_path))
    # Two directories rather than one shared sign-in is the whole reason both can be connected.
    assert result.served_by == 'spare' and result.text.endswith(os.path.join('claude_cli', 'spare'))
    assert ('tenant-a', 'spent') in model_broker.cooldowns
    order = model_broker.accounts_for('tenant-a', config)
    assert [m['id'] for m in order] == ['spare', 'spent']  # The spent login goes last, not missing.


@pytest.mark.asyncio
async def test_the_last_subscription_running_out_is_reported_rather_than_skipped(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'claude_cli', 'opus', account='spent')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', CLAUDE_STUB])
    with pytest.raises(broker.ProviderError) as raised:
        await broker.ModelBroker(store).invoke_agent('tenant-a', store.get('tenant-a', 'models', 'spent'), 'Prompt.', 'edit', project(tmp_path))
    # Classified out of the tool's own reason, which still never reaches the message or the store.
    assert raised.value.exhausted and 'no usage left' in str(raised.value) and 'secrets' not in str(raised.value)


@pytest.mark.asyncio
async def test_an_unsigned_account_is_reported_instead_of_quietly_using_another(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'claude_cli', 'opus', account='never-signed-in')
    connect(store, 'claude_cli', 'opus', account='spare')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', UNSIGNED_STUB])
    model_broker = broker.ModelBroker(store)
    with pytest.raises(broker.ProviderError) as raised:
        await model_broker.invoke_agent('tenant-a', store.get('tenant-a', 'models', 'never-signed-in'), 'Prompt.', 'edit', project(tmp_path))
    # Switching here would hide a sign-in the workspace still has to do.
    assert not raised.value.exhausted and 'signed in' in str(raised.value) and not model_broker.cooldowns


@pytest.mark.asyncio
async def test_opencode_reads_its_reply_and_usage_out_of_the_event_stream(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'opencode_cli', 'opencode/free', account='work')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', OPENCODE_STUB])
    result = await broker.ModelBroker(store).invoke_agent('tenant-a', store.get('tenant-a', 'models', 'work'), 'Prompt.', 'edit', project(tmp_path))
    # The reply is every text part; progress events carry no text and must not become one.
    assert result.text == 'answered from ' + os.path.join(str(store.directory), 'subscriptions', 'tenant-a', 'opencode_cli', 'work')
    assert (result.input_tokens, result.output_tokens) == (28645, 3)


@pytest.mark.asyncio
async def test_a_spent_subscription_is_recognised_in_an_opencode_error_event(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'opencode_cli', 'opencode/free', account='spent')
    connect(store, 'opencode_cli', 'opencode/free', account='spare')
    # OpenCode reports a refusal as an event on stdout and still exits 0.
    stub = ('import json,os,sys;sys.stdin.read();'
            'print(json.dumps({"type":"error","error":{"data":{"message":"usage limit reached"}}})'
            ' if os.environ.get("XDG_DATA_HOME","").endswith("spent") else'
            ' json.dumps({"type":"text","part":{"type":"text","text":"answered"}}))')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', stub])
    result = await broker.ModelBroker(store).invoke_agent('tenant-a', store.get('tenant-a', 'models', 'spent'), 'Prompt.', 'edit', project(tmp_path))
    assert result.text == 'answered' and result.served_by == 'spare'


@pytest.mark.asyncio
async def test_codex_answers_from_the_file_it_writes_rather_than_its_progress_output(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'codex_cli', 'gpt-5.6-sol', account='work')
    stub = ('import pathlib,sys;a=sys.argv;sys.stdin.read();print("progress noise");'
            'pathlib.Path(a[a.index("--output-last-message")+1]).write_text("the final message",encoding="utf-8")')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', stub])
    result = await broker.ModelBroker(store).invoke_agent('tenant-a', store.get('tenant-a', 'models', 'work'), 'Prompt.', 'edit', project(tmp_path))
    assert result.text == 'the final message'
    assert result.input_tokens is None  # Codex reports only a combined total.


@pytest.mark.asyncio
async def test_a_failed_tool_names_the_recovery_without_echoing_stderr(tmp_path, monkeypatch):
    store = setup_store(tmp_path/'state')
    connect(store, 'codex_cli', 'gpt-5.6-sol')
    monkeypatch.setattr(broker, 'resolve_cli', lambda provider: [sys.executable, '-c', 'import sys;sys.stderr.write("prompt text and secrets");sys.exit(3)'])
    with pytest.raises(broker.ProviderError) as raised:
        await broker.ModelBroker(store).invoke_agent('tenant-a', store.get('tenant-a', 'models', 'codex_cli'), 'Prompt.', 'edit', project(tmp_path))
    assert 'signed in' in str(raised.value) and 'secrets' not in str(raised.value)


def test_a_claude_error_reaches_the_user_in_claudes_own_words():
    from backend.broker import read_claude, ProviderError
    import json, pytest
    with pytest.raises(ProviderError) as failed:
        read_claude(json.dumps({'is_error': True, 'result': 'The model fable is not available to this account.', 'subtype': 'error'}), '', 1, 'claude_cli')
    assert 'not available to this account' in str(failed.value) and 'signed in' not in str(failed.value)
    with pytest.raises(ProviderError) as spent:
        read_claude(json.dumps({'is_error': True, 'result': 'You have hit your usage limit.'}), '', 1, 'claude_cli')
    assert spent.value.exhausted
    with pytest.raises(ProviderError) as silent:
        read_claude(json.dumps({'is_error': True}), '', 2, 'claude_cli')
    assert 'exit code 2' in str(silent.value)


def test_an_unknown_model_identifier_is_named_as_such():
    from backend.broker import read_claude, ProviderError
    import json, pytest
    with pytest.raises(ProviderError) as failed:
        read_claude(json.dumps({'is_error': True, 'result': "There's an issue with the selected model (Fable 5.1). It may not exist or you may not have access to it."}), '[claude-code:unrecognized_model] {"model":"Fable 5.1"}', 1, 'claude_cli')
    assert 'does not recognise the model identifier' in str(failed.value) and 'fable' in str(failed.value) and 'signed in' not in str(failed.value)


def test_a_tool_outside_the_path_is_found_in_its_installers_folder(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from backend import broker
    home = tmp_path/'home'; (home/'.local'/'bin').mkdir(parents=True)
    exe = home/'.local'/'bin'/('claude.exe' if os.name == 'nt' else 'claude')
    exe.write_bytes(b'')
    monkeypatch.setenv('PATH', str(tmp_path/'empty'))
    monkeypatch.setattr(broker, 'refresh_path', lambda: None)
    monkeypatch.setitem(broker.KNOWN_DIRS, 'claude_cli', [str(home/'.local'/'bin')])
    assert broker.locate('claude_cli') == str(exe)
    exe.unlink()
    import pytest
    with pytest.raises(broker.ProviderError) as missing:
        broker.resolve_cli('claude_cli')
    assert 'not on the PATH' in str(missing.value) and 'install.ps1' in str(missing.value)
