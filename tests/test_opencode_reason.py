import json

from backend.broker import agent_argv, opencode_reason

UNEXPECTED = json.dumps({'type': 'error', 'error': {'name': 'UnknownError', 'data': {
    'message': 'Unexpected server error. Check server logs for details.', 'ref': 'err_f961c716'}}})

# What OpenCode 1.18 printed to stderr (--print-logs) when the goal plugin's state file was unreadable.
PLUGIN_STDERR = (
    'timestamp=2026-10-03T02:31:27.901Z level=ERROR run=5b72e74c message=failed ref=err_3ea0b6bc '
    'error="(FiberFailure) StateDecodeError: An error has occurred\\n at catch (C:\\\\Users\\\\me\\\\.cache\\\\opencode'
    '\\\\packages\\\\@prevalentware\\\\opencode-goal-plugin@latest\\\\node_modules\\\\dist\\\\server.js:103:43) {\\n '
    '[cause]: SyntaxError: JSON Parse error\\n}" cause="ignored"')


def test_plugin_failure_is_named():
    reason = opencode_reason(UNEXPECTED, PLUGIN_STDERR)
    assert 'Unexpected server error' in reason
    assert '@prevalentware/opencode-goal-plugin' in reason
    assert 'StateDecodeError' in reason
    assert 'opencode.json' in reason


def test_other_internal_error_keeps_its_first_line():
    err = 'timestamp=x level=ERROR run=1 message=failed error="Database is locked\\n at foo" cause="x"'
    reason = opencode_reason(UNEXPECTED, err)
    assert reason.endswith('(Database is locked)')
    assert 'plugin' not in reason


def test_without_stderr_detail_the_event_stands():
    assert opencode_reason(UNEXPECTED, '') == 'Unexpected server error. Check server logs for details.'


def test_ordinary_errors_are_unchanged():
    out = json.dumps({'type': 'error', 'error': {'data': {'message': 'Model not found: x/y'}}})
    assert opencode_reason(out, PLUGIN_STDERR) == 'Model not found: x/y'


def test_opencode_off_switches_reach_the_tool(monkeypatch):
    from backend.localprocess import child_env
    monkeypatch.setenv('OPENCODE_DISABLE_CLAUDE_CODE_SKILLS', '1')
    monkeypatch.setenv('OPENCODE_API_KEY', 'secret')
    env = child_env()
    assert env.get('OPENCODE_DISABLE_CLAUDE_CODE_SKILLS') == '1'
    assert 'OPENCODE_API_KEY' not in env


def test_opencode_prints_its_errors():
    argv = agent_argv('opencode_cli', ['opencode'], 'strata/m', 'edit', '.', 'final.txt')
    assert argv[argv.index('--log-level') + 1] == 'ERROR' and '--print-logs' in argv
