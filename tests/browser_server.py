"""Isolated browser-test application. Never imported by the production entrypoint."""
import os
import tempfile
from backend.app import create_app
from tests.test_engine import ScriptedBroker, REJECTED, APPROVED

app = create_app(os.environ.get('HARNESS_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='frontier-browser-'),ScriptedBroker([REJECTED,APPROVED],delay=0.6))
