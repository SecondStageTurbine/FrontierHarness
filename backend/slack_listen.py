"""A session that listens to one Slack channel: each new message becomes a turn, and the reply goes back in its thread.

The credentials are the workspace's `slack` MCP server's: a browser session (SLACK_MCP_XOXC_TOKEN with the
`d` cookie in SLACK_MCP_XOXD_TOKEN) or a user token (SLACK_MCP_XOXP_TOKEN). The scheduler calls `tick` every
half minute; a session handles one message at a time, oldest first. It takes top-level messages, and replies in
any thread of the channel from other people when it takes anyone's. The user's own thread replies never count:
they cannot be told apart from what the agent itself posts as the user, and taking them would loop.
"""
import re
import time

import httpx

PREFIX = '🤖 Frontier: '
MODEL_CUE = re.compile(r'\b(?:use|using|with|via|on|ask)\s+|\bmodel\s*[:=]\s*', re.I)
MODEL_IDENTIFIER = re.compile(r'^(?:gpt-\d+(?:\.\d+)?-[a-z0-9]+(?:[.-][a-z0-9]+)*|claude-[a-z0-9]+(?:[.-][a-z0-9]+)*|gemini-[a-z0-9]+(?:[.-][a-z0-9]+)*)', re.I)
GPT_VERSION = re.compile(r'^gpt-(\d+)(?:\.(\d+))?-([a-z0-9]+(?:[.-][a-z0-9]+)*)$', re.I)


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
    """A new message a person wrote: top level from the user, or from anyone else when the session takes anyone's."""
    reply = message.get('thread_ts', message['ts']) != message['ts']
    return (not message.get('bot_id') and not message.get('subtype') and message.get('text', '').strip()
            and not message['text'].startswith((PREFIX.strip(), ':robot_face: Frontier:'))  # Slack stores the emoji by name.
            and float(message['ts']) > float(listen['since'])
            and (message.get('user') != listen['user'] if reply else True)
            and (listen.get('anyone') or message.get('user') == listen['user']))


def slack_markup(text):
    """The agent writes Markdown; Slack's own markup bolds with one asterisk and links as <url|label>."""
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    return re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', r'<\2|\1>', text)


def requested_agent(text, agents):
    """Use an explicit model request in the Slack text; prefer the next connected GPT of the same line."""
    labels = sorted({label for agent in agents for label in (agent.get('model_name'), agent.get('name')) if label},
                    key=len, reverse=True)
    requested = None
    for cue in MODEL_CUE.finditer(text):
        if re.search(r"(?:don't|do not|never)\s*$", text[max(0, cue.start()-12):cue.start()], re.I):
            continue
        tail = re.sub(r'^(?:the\s+)?(?:model\s+)?', '', text[cue.end():], count=1, flags=re.I)
        requested = next((label for label in labels if tail.casefold().startswith(label.casefold())
                          and (len(tail) == len(label) or not re.match(r'[\w./-]', tail[len(label)]))), None)
        if requested is None:
            identifier = MODEL_IDENTIFIER.match(tail)
            requested = identifier.group() if identifier else None
        if requested:
            break
    if not requested:
        return None
    exact = next((agent for agent in agents if requested.casefold() in
                  (agent.get('model_name', '').casefold(), agent.get('name', '').casefold())), None)
    if exact:
        return {**exact, 'requested_model': requested}
    target = GPT_VERSION.fullmatch(requested)
    if target:
        wanted = (int(target[1]), int(target[2] or 0))
        same_line = []
        for agent in agents:
            candidate = GPT_VERSION.fullmatch(agent.get('model_name', '')) if agent.get('provider') == 'codex_cli' else None
            if candidate and candidate[3].casefold() == target[3].casefold():
                same_line.append(((int(candidate[1]), int(candidate[2] or 0)), agent))
        if same_line:
            newer = [(version, agent) for version, agent in same_line if version >= wanted]
            chosen = min(newer, key=lambda item: item[0])[1] if newer else max(same_line, key=lambda item: item[0])[1]
            return {**chosen, 'requested_model': requested}
    return None


def prompt(listen, message):
    where = f'a reply in the thread {message["thread_ts"]} of' if message.get('thread_ts', message['ts']) != message['ts'] else 'a message in'
    return (f'A Slack message, {where} #{listen["name"]} ({listen["channel"]}), from <@{message.get("user")}>:\n\n{message["text"]}\n\n'
            'Act on it. Your final reply in this turn is posted back to the message\'s Slack thread for you, already '
            'mentioning the sender. To mention anyone else write <@USERID>; a plain @name notifies no one. '
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
        requested = pending.get('requested_model')
        if requested:
            used = pending.get('selected_model')
            if reply and reply.get('model_id') and reply['model_id'] != pending.get('selected_model_id'):
                used = reply.get('model_name') or used
            if used and requested.casefold() != used.casefold():
                text = f'Requested {requested}; used {used}, the available model selected for this turn.\n\n{text}'
        sender = pending.get('user')
        mention = f'<@{sender}> ' if sender and sender != listen['user'] else ''
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=pending['ts'], text=(PREFIX + mention + slack_markup(text))[:39000])
        session = store.get(tenant_id, 'sessions', session['id'])
        session['slack'].pop('pending', None)
        session['slack']['error'] = None
        store.put(tenant_id, 'sessions', session)
        return
    if runner.busy(tenant_id, session['id']):
        return
    # ponytail: only threads under the latest 50 top-level messages are followed; page further if older threads matter.
    history = (await call(client, headers, 'conversations.history', channel=listen['channel'], limit='50')).get('messages', [])
    candidates = list(history)
    if listen.get('anyone'):
        for parent in history:
            if float(parent.get('latest_reply') or 0) > float(listen['since']):
                thread = await call(client, headers, 'conversations.replies', channel=listen['channel'], ts=parent['ts'], oldest=listen['since'], limit='200')
                candidates += [m for m in thread.get('messages', []) if m['ts'] != parent['ts']]
    fresh = sorted((m for m in candidates if wanted(m, listen)), key=lambda m: float(m['ts']))
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
    selected = requested_agent(message['text'], runner.agents(tenant_id))
    try:
        session = runner.send(tenant_id, session['project_id'], session['id'], prompt(listen, message),
                              selected['id'] if selected else listen['model_id'], listen['mode'])
    except Exception as exc:
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=message.get('thread_ts', message['ts']), text=f'{PREFIX}could not start: {exc}')
        return
    session['slack']['pending'] = {'ts': message.get('thread_ts', message['ts']), 'user': message.get('user'), 'message_id': session['messages'][-1]['id']}
    if selected:
        session['slack']['pending'].update(requested_model=selected['requested_model'],
                                           selected_model=selected['model_name'], selected_model_id=selected['id'])
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
