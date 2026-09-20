"""Isolated browser-test application. Never imported by the production entrypoint."""
import os
import tempfile
from backend.app import create_app
from tests.harness import ScriptedAgent

app = create_app(os.environ.get('HARNESS_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='frontier-browser-'), ScriptedAgent(reply='Done.', delay=0.4))
