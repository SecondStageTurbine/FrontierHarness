"""Isolated browser-test application. Never imported by the production entrypoint."""
import os
import tempfile
from backend import maintenance
from backend.app import create_app
from tests.harness import ScriptedAgent

import asyncio
import httpx
from backend.broker import AgentResult

# Turns carry Frontier's own tools, as in the desktop app, so a scripted agent can ask a question the way Claude does.
os.environ.setdefault('HARNESS_DESKTOP_PORT', '8001')


class AskingAgent(ScriptedAgent):
    """Answers 'Done.', except a message containing 'ask me' raises a question card and waits for the answer."""
    async def invoke_agent(self, tenant_id, config, prompt, mode, root, extras=None):
        if 'ask me' not in prompt.split('USER:')[-1].lower() or not (extras or {}).get('approval'):
            return await super().invoke_agent(tenant_id, config, prompt, mode, root, extras)
        approval = extras['approval']
        headers = {'X-Frontier-Turn': approval['token']}
        async with httpx.AsyncClient(base_url=approval['url'], timeout=40) as client:
            created = (await client.post('/internal/approvals', headers=headers, json={'tool_name': 'ask_user', 'kind': 'question',
                                                                                     'input': {'question': 'Which colour should the button be?', 'options': ['Blue', 'Green']}})).json()
            for _ in range(120):
                state = (await client.get(f'/internal/approvals/{created["id"]}?wait=1', headers=headers)).json()
                if state.get('decision'):
                    return AgentResult(f'You chose {state.get("message")}.', 1, 1)
                await asyncio.sleep(0.2)
        return AgentResult('Nobody answered.', 1, 1)


app = create_app(os.environ.get('HARNESS_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='frontier-browser-'), AskingAgent(reply='Done.', delay=0.4))

# Subscription usage is fixed here: the suite never calls the providers with the tester's own sign-in.
maintenance.subscription_limits = lambda force=False, logins=None: [
    {'provider': 'claude_cli', 'label': 'Claude', 'plan': None, 'error': None, 'retry_after': None, 'limits': [
        {'label': 'Current session', 'percent': 12.0, 'severity': 'normal', 'resets_at': '2099-01-01T00:00:00Z', 'logins': 2},
        {'label': 'Week, all models', 'percent': 76.0, 'severity': 'normal', 'resets_at': '2099-01-01T00:00:00Z', 'logins': 2}],
     'logins': [{'name': 'Default sign-in', 'plan': None, 'error': None, 'limits': [{'label': 'Current session', 'percent': 4.0}, {'label': 'Week, all models', 'percent': 72.0}]},
                {'name': 'work', 'plan': None, 'error': None, 'limits': [{'label': 'Current session', 'percent': 20.0}, {'label': 'Week, all models', 'percent': 80.0}]}]},
    {'provider': 'codex_cli', 'label': 'Codex', 'plan': 'pro', 'error': None, 'retry_after': None, 'limits': [
        {'label': 'Week', 'percent': 11.0, 'severity': 'normal', 'resets_at': '2099-01-01T00:00:00Z'}]}]
