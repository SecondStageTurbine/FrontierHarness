"""A session that listens to one Slack channel: each new message becomes a turn, and the reply goes back in its thread.

The credentials are the workspace's `slack` MCP server's: a browser session (SLACK_MCP_XOXC_TOKEN with the
`d` cookie in SLACK_MCP_XOXD_TOKEN) or a user token (SLACK_MCP_XOXP_TOKEN). The scheduler calls `tick` every
half minute; a session handles one message at a time, oldest first, and only top-level messages, so its
own replies, which are thread replies, never come back to it.
"""
import time

import httpx

PREFIX = '🤖 Frontier: '


def credentials(store, tenant_id):
    server = next((s for s in store.list(tenant_id, 'mcp_servers') if s.get('name') == 'slack' and s.get('enabled') is not False), None)
    env = (server or {}).get('env') or {}
    token = env.get('SLACK_MCP_XOXC_TOKEN') or env.get('SLACK_MCP_XOXP_TOKEN')
    if not token:
        raise ValueError('Add the Slack MCP server under Settings → MCP servers, with its tokens, first.')
    return {'Authorization': f'Bearer {token}', **({'Cookie': f'd={env["SLACK_MCP_XOXD_TOKEN"]}'} if env.get('SLACK_MCP_XOXD_TOKEN') and env.get('SLACK_MCP_XOXC_TOKEN') else {})}


async def call(client, headers, method, **params):
    response = await client.post(f'https://slack.com/api/{method}', headers=headers, data=params)
    body = response.json()
    if not body.get('ok'):
        raise ValueError(f'Slack {method}: {body.get("error", response.status_code)}')
    return body


async def resolve(store, tenant_id, channel):
    """A channel ID or #name → (id, name, the token's own user ID)."""
    headers = credentials(store, tenant_id)
    wanted = channel.strip().lstrip('#')
    async with httpx.AsyncClient(timeout=20) as client:
        me = (await call(client, headers, 'auth.test'))['user_id']
        cursor = ''
        while True:
            page = await call(client, headers, 'conversations.list', types='public_channel,private_channel', exclude_archived='true', limit='1000', cursor=cursor)
            for c in page['channels']:
                if wanted in (c['id'], c['name']):
                    return c['id'], c['name'], me
            cursor = page.get('response_metadata', {}).get('next_cursor')
            if not cursor:
                raise ValueError(f'No channel {channel} that you are in.')


def wanted(message, listen):
    """A new top-level message a person wrote, from the user unless the session takes anyone's."""
    return (not message.get('bot_id') and not message.get('subtype') and message.get('text', '').strip()
            and not message['text'].startswith(PREFIX.strip())
            and float(message['ts']) > float(listen['since']) and (listen.get('anyone') or message.get('user') == listen['user']))


def prompt(listen, message):
    return (f'A Slack message in #{listen["name"]} from <@{message.get("user")}>:\n\n{message["text"]}\n\n'
            'Act on it. Your final reply in this turn is posted back to the message\'s Slack thread for you, '
            f'so do not post it yourself, and never post a new top-level message in #{listen["name"]}.')


async def step(store, runner, tenant_id, session, client, headers):
    listen = session['slack']
    pending = listen.get('pending')
    if pending:
        reply = next((m for m in session['messages'] if m['id'] == pending['message_id']), None)
        if reply and reply.get('status') == 'running':
            return
        text = (reply or {}).get('content') or ''
        if not reply or reply.get('status') != 'complete':
            text = f'the turn {(reply or {}).get("status", "was lost")}' + (f': {reply["error"]}' if reply and reply.get('error') else '') + (f'\n\n{text}' if text else '')
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=pending['ts'], text=(PREFIX + text)[:39000])
        session = store.get(tenant_id, 'sessions', session['id'])
        session['slack'].pop('pending', None)
        session['slack']['error'] = None
        store.put(tenant_id, 'sessions', session)
        return
    if runner.busy(tenant_id, session['id']):
        return
    history = await call(client, headers, 'conversations.history', channel=listen['channel'], oldest=listen['since'], limit='50')
    fresh = sorted((m for m in history.get('messages', []) if wanted(m, listen)), key=lambda m: float(m['ts']))
    if not fresh:
        if listen.get('error'):
            listen['error'] = None
            store.put(tenant_id, 'sessions', session)
        return
    message = fresh[0]
    listen['since'] = message['ts']
    store.put(tenant_id, 'sessions', session)  # Moved past it first, so a message that cannot start is never retried every tick.
    try:
        await call(client, headers, 'reactions.add', channel=listen['channel'], timestamp=message['ts'], name='eyes')
    except ValueError:
        pass  # Already reacted, or reactions are off: the acknowledgement is a nicety.
    try:
        session = runner.send(tenant_id, session['project_id'], session['id'], prompt(listen, message), listen['model_id'], listen['mode'])
    except Exception as exc:
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=message['ts'], text=f'{PREFIX}could not start: {exc}')
        return
    session['slack']['pending'] = {'ts': message['ts'], 'message_id': session['messages'][-1]['id']}
    session['slack']['error'] = None
    store.put(tenant_id, 'sessions', session)


async def tick(store, runner, tenant_id):
    listening = [s for s in store.list(tenant_id, 'sessions') if (s.get('slack') or {}).get('channel') and not s.get('archived')]
    if not listening:
        return
    try:
        headers = credentials(store, tenant_id)
    except ValueError as exc:
        headers, failure = None, str(exc)
    async with httpx.AsyncClient(timeout=20) as client:
        for session in listening:
            try:
                if not headers:
                    raise ValueError(failure)
                await step(store, runner, tenant_id, session, client, headers)
            except Exception as exc:
                # Shown on the session; the next tick tries again, so a dead token or a network blip recovers on its own.
                session = store.get(tenant_id, 'sessions', session['id'])
                if session.get('slack') and session['slack'].get('error') != str(exc):
                    session['slack']['error'] = str(exc)
                    store.put(tenant_id, 'sessions', session)


def since_now():
    return f'{time.time():.6f}'
