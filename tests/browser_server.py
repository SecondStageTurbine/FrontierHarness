"""Isolated browser-test application. Never imported by the production entrypoint."""
import os
import tempfile
from backend import maintenance
from backend.app import create_app
from tests.harness import ScriptedAgent

app = create_app(os.environ.get('HARNESS_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='frontier-browser-'), ScriptedAgent(reply='Done.', delay=0.4))

# Subscription usage is fixed here: the suite never calls the providers with the tester's own sign-in.
maintenance.subscription_limits = lambda force=False: [
    {'provider': 'claude_cli', 'label': 'Claude', 'plan': None, 'error': None, 'retry_after': None, 'limits': [
        {'label': 'Current session', 'percent': 12.0, 'severity': 'normal', 'resets_at': '2099-01-01T00:00:00Z'},
        {'label': 'Week, all models', 'percent': 76.0, 'severity': 'normal', 'resets_at': '2099-01-01T00:00:00Z'}]},
    {'provider': 'codex_cli', 'label': 'Codex', 'plan': 'pro', 'error': None, 'retry_after': None, 'limits': [
        {'label': 'Week', 'percent': 11.0, 'severity': 'normal', 'resets_at': '2099-01-01T00:00:00Z'}]}]
