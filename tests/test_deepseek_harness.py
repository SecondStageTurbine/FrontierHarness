import json
import os

import pytest

from backend import adaptive, broker
from backend.broker import ProviderError, agent_argv, read_output
from backend.agent import AgentRunner
from backend.schemas import ModelConfig
from tests.harness import ScriptedAgent, open_project, setup_store


@pytest.mark.skipif(os.name != 'nt', reason='DeepSeek Harness desktop fallback is Windows-only')
def test_installed_dsh_cli_wins_over_desktop_internal_host(tmp_path, monkeypatch):
    appdata = tmp_path/'AppData'; localappdata = tmp_path/'LocalAppData'
    npm = appdata/'npm'; package = npm/'node_modules/@deepseek-ai/dsh'; program = package/'lib/bin.js'
    program.parent.mkdir(parents=True)
    program.write_text('', encoding='utf-8')
    (package/'package.json').write_text(json.dumps({'bin': {'dsh': 'lib/bin.js'}}), encoding='utf-8')
    shim = npm/'dsh.ps1'; shim.write_text('', encoding='utf-8')
    node = tmp_path/'node.exe'; node.write_text('', encoding='utf-8')
    desktop = localappdata/'Programs/DeepSeek Harness'
    desktop.mkdir(parents=True)
    (desktop/'DeepSeek Harness.exe').write_text('', encoding='utf-8')
    (desktop/'resources').mkdir()
    (desktop/'resources/app.asar').write_text('', encoding='utf-8')
    monkeypatch.setenv('APPDATA', str(appdata)); monkeypatch.setenv('LOCALAPPDATA', str(localappdata))
    monkeypatch.setattr(broker, 'locate', lambda provider: str(shim))
    monkeypatch.setattr(broker.shutil, 'which', lambda name: str(node) if name == 'node' else None)

    assert broker.resolve_cli('deepseek_harness') == [str(node), str(program.resolve())]


def test_headless_turn_uses_profile_and_reads_terminal_answer(tmp_path):
    command = agent_argv('deepseek_harness', ['dsh'], 'headless', 'auto', tmp_path, tmp_path/'final')
    assert command == ['dsh', 'headless', '--json', '-']
    output = '\n'.join([
        json.dumps({'type': 'session', 'sessionId': 'session-1'}),
        json.dumps({'type': 'text', 'text': 'intermediate'}),
        json.dumps({'type': 'final', 'text': 'done'}),
    ])
    result = read_output('deepseek_harness', 0, output, '', 'prompt', tmp_path/'final')
    assert result.text == 'done'
    assert result.session_id == 'session-1'


def test_headless_cannot_claim_read_or_edit_posture(tmp_path):
    for mode in ('read', 'edit'):
        with pytest.raises(ProviderError, match='Full auto'):
            agent_argv('deepseek_harness', ['dsh'], 'headless', mode, tmp_path, tmp_path/'final')


def test_headless_is_keyless_but_not_priced_as_subscription():
    row = ModelConfig(name='DeepSeek Harness', provider='deepseek_harness', model_name='headless')
    assert row.input_price is None and row.output_price is None
    assert adaptive.profile({'id': 'dsh', 'name': row.name, 'provider': row.provider, 'model_name': row.model_name})


def test_headless_reports_missing_terminal_event(tmp_path):
    with pytest.raises(ProviderError, match='final'):
        read_output('deepseek_harness', 0, '{"type":"text","text":"partial"}', '', '', tmp_path/'final')


def test_headless_missing_credential_names_its_own_configuration(tmp_path):
    event = {'type': 'status', 'phase': 'turn_end', 'reason': {'kind': 'error', 'error': {'code': 'MISSING_CREDENTIAL', 'message': 'private detail'}}}
    with pytest.raises(ProviderError, match='DeepSeek API key') as stopped:
        read_output('deepseek_harness', 1, json.dumps(event), '', 'prompt', tmp_path/'final')
    assert 'private detail' not in str(stopped.value)


@pytest.mark.asyncio
async def test_headless_is_only_a_full_auto_single_agent_candidate(tmp_path):
    store = setup_store(tmp_path/'state')
    row = ModelConfig(name='DeepSeek Harness', provider='deepseek_harness', model_name='headless')
    store.put('tenant-a', 'models', {'id': 'dsh', **row.model_dump(exclude={'api_key'}), 'status': 'connected'})
    runner = AgentRunner(store, ScriptedAgent())
    folder = tmp_path/'project'
    folder.mkdir()
    project, session = open_project(store, runner, 'tenant-a', folder)
    with pytest.raises(ValueError, match='Full auto'):
        runner.send('tenant-a', project['id'], session['id'], 'read this', 'dsh', 'read')
    with pytest.raises(ValueError, match='single-agent'):
        runner.send('tenant-a', project['id'], session['id'], 'build this', 'dsh', 'auto', team=True)
    other_ids = {m['id'] for m in runner.agents('tenant-a') if m['id'] != 'dsh'}
    assert await runner.fallback('tenant-a', other_ids, mode='edit') is None
    assert (await runner.fallback('tenant-a', other_ids, mode='auto'))['id'] == 'dsh'


def test_routing_judges_headless_by_the_model_its_profile_names(tmp_path, monkeypatch):
    monkeypatch.setattr(adaptive.Path, 'home', lambda: tmp_path)
    row = {'id': 'dsh', 'name': 'DeepSeek Harness', 'provider': 'deepseek_harness', 'model_name': 'headless'}
    assert adaptive.profile(row)['cost_class'] == 'high'  # No profile patch: the conservative default.
    patch = tmp_path/'.dsh'/'profiles'/'headless'/'cordis.patch.yml'
    patch.parent.mkdir(parents=True)
    patch.write_text('- id: agent-default-model\n  config:\n    provider: deepseek-account\n    model: deepseek-flash\n', encoding='utf-8')
    assert adaptive.profile(row)['cost_class'] == 'low'
    luna = {'id': 'luna', 'name': 'Luna', 'provider': 'codex_cli', 'model_name': 'gpt-6-luna'}
    haiku = {'id': 'haiku', 'name': 'Haiku', 'provider': 'claude_cli', 'model_name': 'haiku'}
    sol = {'id': 'sol', 'name': 'Sol', 'provider': 'codex_cli', 'model_name': 'gpt-6-sol'}
    needs = adaptive.TaskRequirements(task_type='implementation', complexity='medium', risk='low', requirements={'coding': 8, 'debugging': 7}, reason='x')
    assert adaptive.choose(needs, [sol, luna, haiku, row])[0]['id'] == 'dsh'  # A cheap turn it covers is its, not Sol's.
