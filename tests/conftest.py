"""The suite must not depend on what happens to be running on the machine that runs it."""
import pytest


@pytest.fixture(autouse=True)
def no_local_servers(monkeypatch, tmp_path):
    # Without an OpenCode config, no model is a local server and nothing is probed.
    monkeypatch.setenv('OPENCODE_CONFIG', str(tmp_path/'no-such-opencode.json'))
    monkeypatch.setenv('FRONTIER_BENCHMARKS', 'off')  # No leaderboard download either.
