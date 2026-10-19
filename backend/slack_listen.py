"""A session that listens to one Slack channel: each new message becomes a turn, and the reply goes back in its thread.

The credentials are the workspace's `slack` MCP server's: a browser session (SLACK_MCP_XOXC_TOKEN with the
`d` cookie in SLACK_MCP_XOXD_TOKEN) or a user token (SLACK_MCP_XOXP_TOKEN). The scheduler calls `tick` every
half minute; a session handles one message at a time, oldest first. It takes top-level messages, and replies in
any thread of the channel from other people when it takes anyone's. The user's own thread replies never count:
they cannot be told apart from what the agent itself posts as the user, and taking them would loop.
"""
import re
import time
from datetime import datetime, timezone

import httpx

PREFIX = '🤖 Frontier: '
NOTE_AFTER = 90  # Seconds a handed-in message may go unanswered before the sender is told it waits on a long step.
MODEL_CUE = re.compile(r'\b(?:use|using|with|via|on|ask)\s+|\bmodel\s*[:=]\s*', re.I)
HANDOFF_CUE = re.compile(
    r'\b(?P<need>need|want)\s+(?:(?:a|an|the)\s+)?|'
    r'\b(?P<switch>switch|route)(?:\s+(?:me|us|this|the|session|conversation|review))*\s+to\s+', re.I)
MODEL_IDENTIFIER = re.compile(r'^(?:gpt-\d+(?:\.\d+)?-[a-z0-9]+(?:[.-][a-z0-9]+)*|claude-[a-z0-9]+(?:[.-][a-z0-9]+)*|gemini-[a-z0-9]+(?:[.-][a-z0-9]+)*)', re.I)
GPT_VERSION = re.compile(r'^gpt-(\d+)(?:\.(\d+))?-([a-z0-9]+(?:[.-][a-z0-9]+)*)$', re.I)
PROVIDER_REQUESTS = {'codex cli': 'codex_cli', 'claude code': 'claude_cli', 'open code': 'opencode_cli',
                     'codex': 'codex_cli', 'claude': 'claude_cli', 'opencode': 'opencode_cli', 'gemini': 'gemini_cli'}


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


def _agent_from_tail(tail, agents):
    """A leading configured name, model identifier, or agent family in some text."""
    labels = sorted({label for agent in agents for label in (agent.get('model_name'), agent.get('name')) if label},
                    key=len, reverse=True)
    tail = re.sub(r'^(?:the\s+)?(?:model\s+)?', '', tail, count=1, flags=re.I)
    requested = next((label for label in labels if tail.casefold().startswith(label.casefold())
                      and (len(tail) == len(label) or not re.match(r'[\w/-]', tail[len(label)]))), None)
    if requested is None:
        requested = next((label for label in PROVIDER_REQUESTS if tail.casefold().startswith(label)
                          and (len(tail) == len(label) or not re.match(r'[\w/-]', tail[len(label)]))), None)
    if requested is None:
        identifier = MODEL_IDENTIFIER.match(tail)
        requested = identifier.group() if identifier else None
    if not requested:
        return None
    exact = next((agent for agent in agents if requested.casefold() in
                  (agent.get('model_name', '').casefold(), agent.get('name', '').casefold())), None)
    if exact:
        return {**exact, 'requested_model': requested}
    provider = PROVIDER_REQUESTS.get(requested.casefold())
    if provider:
        matches = [agent for agent in agents if agent.get('provider') == provider]
        if matches:
            chosen = next((agent for agent in matches if agent.get('online') is not False), matches[0])
            return {**chosen, 'requested_model': requested}
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


def requested_agent(text, agents):
    """Use an explicit model request in the Slack text; prefer the next connected GPT of the same line."""
    for cue in MODEL_CUE.finditer(text):
        if re.search(r"(?:don't|do not|never)\s*$", text[max(0, cue.start()-12):cue.start()], re.I):
            continue
        if selected := _agent_from_tail(text[cue.end():], agents):
            return selected
    return None


def requested_handoff(text, agents):
    """A request for a reviewer/agent switch, which pins later Slack messages to that agent."""
    for cue in HANDOFF_CUE.finditer(text):
        tail = text[cue.end():]
        selected = _agent_from_tail(tail, agents)
        if not selected:
            continue
        if cue.group('need'):
            requested_at = tail.casefold().find(selected['requested_model'].casefold())
            rest = tail[requested_at + len(selected['requested_model']):]
            if not re.match(r'\s+(?:reviewer|agent)\b', rest, re.I):
                continue
        return selected
    return None


def prompt(listen, message, midturn=False):
    thread = message.get('thread_ts', message['ts'])
    where = f'a reply in the thread {thread} of' if thread != message['ts'] else 'a message in'
    head = f'A Slack message, {where} #{listen["name"]} ({listen["channel"]}), from <@{message.get("user")}>:\n\n{message["text"]}\n\n'
    if midturn:
        return (head + 'It arrived while you were working. Answer it now with the slack_reply tool, thread_ts '
                f'"{thread}": what you are on and when it will be done, or whatever it asks. Then carry on, changing course if it '
                f'asks you to. To mention anyone write <@USERID>. Never post a new top-level message in #{listen["name"]}.')
    return (head +
            'Act on it. Your final reply in this turn is posted back to the message\'s Slack thread for you, already '
            'mentioning the sender. To mention anyone else write <@USERID>; a plain @name notifies no one. '
            f'so do not post it yourself, and never post a new top-level message in #{listen["name"]}.')


def mention_of(listen, user):
    return f'<@{user}> ' if user and user != listen['user'] else ''


async def fresh_messages(client, headers, listen):
    """The messages not taken yet, oldest first."""
    # ponytail: only threads under the latest 50 top-level messages are followed; page further if older threads matter.
    history = (await call(client, headers, 'conversations.history', channel=listen['channel'], limit='50')).get('messages', [])
    candidates = list(history)
    if listen.get('anyone'):
        for parent in history:
            if float(parent.get('latest_reply') or 0) > float(listen['since']):
                thread = await call(client, headers, 'conversations.replies', channel=listen['channel'], ts=parent['ts'], oldest=listen['since'], limit='200')
                candidates += [m for m in thread.get('messages', []) if m['ts'] != parent['ts']]
    return sorted((m for m in candidates if wanted(m, listen)), key=lambda m: float(m['ts']))


async def react(client, headers, listen, message):
    try:
        await call(client, headers, 'reactions.add', channel=listen['channel'], timestamp=message['ts'], name='eyes')
    except ValueError:
        pass  # Already reacted, or reactions are off: the acknowledgement is a nicety.


def minutes_since(stamp):
    try:
        started = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
        return max(0, int((datetime.now(timezone.utc) - started).total_seconds() // 60))
    except ValueError:
        return None


async def still_unanswered(store, tenant_id, session, client, headers):
    """A handed-in message the agent has not answered after a while: it is inside one long step (a build), and reads
    the message only when that step ends. The sender hears so once, rather than nothing."""
    listen = session['slack']
    late = [e for e in listen.get('midturn') or [] if not e.get('answered') and not e.get('noted') and time.time() - e.get('at', 0) > NOTE_AFTER]
    if not late:
        return
    running = next((m for m in reversed(session['messages']) if m['role'] == 'assistant' and m['status'] == 'running'), {})
    for entry in late:
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=entry['ts'],
                   text=PREFIX + f'{mention_of(listen, entry.get("user"))}{running.get("model_name") or "The agent"} is mid-step '
                   f'(working {minutes_since(running.get("created_at"))} min) and has your message; it answers when this step finishes.')
    session = store.get(tenant_id, 'sessions', session['id'])
    for entry in session['slack'].get('midturn') or []:
        if entry['ts'] in {e['ts'] for e in late}:
            entry['noted'] = True
    store.put(tenant_id, 'sessions', session)


async def during(store, runner, tenant_id, session, client, headers):
    """A turn is running. A Claude turn is handed each new message, to answer in its thread at once and carry on;
    otherwise each gets 👀 and one line saying it waits, and is taken, in order, when the turn ends."""
    await still_unanswered(store, tenant_id, session, client, headers)
    session = store.get(tenant_id, 'sessions', session['id'])
    handing = True
    for message in await fresh_messages(client, headers, session['slack']):
        if message['ts'] in (session['slack'].get('acked') or []):
            handing = False  # Already waiting; anything after it waits behind it, so the order holds.
            continue
        listen = session['slack']
        thread = message.get('thread_ts', message['ts'])
        await react(client, headers, listen, message)
        if handing and runner.inject(tenant_id, session['id'], prompt(listen, message, midturn=True)):
            session = store.get(tenant_id, 'sessions', session['id'])
            session['slack']['since'] = message['ts']
            session['slack'].setdefault('midturn', []).append({'ts': thread, 'user': message.get('user'), 'at': time.time()})
        else:
            handing = False
            running = next((m for m in reversed(session['messages']) if m['role'] == 'assistant' and m['status'] == 'running'), {})
            minutes = minutes_since(running.get('created_at'))
            note = (f'{mention_of(listen, message.get("user"))}mid-turn ({running.get("model_name") or "an agent"} has been working '
                    f'{minutes} min); your message is queued and will be answered when it finishes.' if minutes is not None else
                    f'{mention_of(listen, message.get("user"))}mid-turn; your message is queued and will be answered when it finishes.')
            await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=thread, text=PREFIX + note)
            session = store.get(tenant_id, 'sessions', session['id'])
            session['slack']['acked'] = (session['slack'].get('acked') or [])[-49:] + [message['ts']]
        store.put(tenant_id, 'sessions', session)


async def reply(store, tenant_id, session, thread_ts, text):
    """The agent answers a Slack thread from inside its turn: only a thread this conversation was handed."""
    listen = session.get('slack') or {}
    threads = {e['ts']: e for e in listen.get('midturn') or []}
    if listen.get('pending'):
        threads.setdefault(listen['pending']['ts'], listen['pending'])
    entry = threads.get(thread_ts)
    if not listen.get('channel') or entry is None:
        raise ValueError(f'This conversation was not handed a Slack thread {thread_ts}.')
    async with httpx.AsyncClient(timeout=20) as client:
        await call(client, credentials(store, tenant_id), 'chat.postMessage', channel=listen['channel'], thread_ts=thread_ts,
                   text=(PREFIX + mention_of(listen, entry.get('user')) + slack_markup(text))[:39000])
    session = store.get(tenant_id, 'sessions', session['id'])
    for e in session['slack'].get('midturn') or []:
        if e['ts'] == thread_ts:
            e['answered'] = True
    store.put(tenant_id, 'sessions', session)
    return {'ok': True}


async def step(store, runner, tenant_id, session, client, headers):
    listen = session['slack']
    pending = listen.get('pending')
    if pending:
        reply_message = next((m for m in session['messages'] if m['id'] == pending['message_id']), None)
        if reply_message and reply_message.get('status') == 'running':
            return await during(store, runner, tenant_id, session, client, headers)
        text = (reply_message or {}).get('content') or ''
        if not reply_message or reply_message.get('status') != 'complete':
            text = f'the turn {(reply_message or {}).get("status", "was lost")}' + (f': {reply_message["error"]}' if reply_message and reply_message.get('error') else '') + (f'\n\n{text}' if text else '')
        requested = pending.get('requested_model')
        if requested:
            used = pending.get('selected_model')
            if reply_message and reply_message.get('model_id') and reply_message['model_id'] != pending.get('selected_model_id'):
                used = reply_message.get('model_name') or used
            if used and requested.casefold() != used.casefold():
                text = f'Requested {requested}; used {used}, the available model selected for this turn.\n\n{text}'
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=pending['ts'], text=(PREFIX + mention_of(listen, pending.get('user')) + slack_markup(text))[:39000])
        for extra in listen.get('midturn') or []:
            # Handed in mid-turn but never answered with slack_reply: the turn's reply answers it too.
            if not extra.get('answered') and extra['ts'] != pending['ts']:
                await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=extra['ts'], text=(PREFIX + mention_of(listen, extra.get('user')) + slack_markup(text))[:39000])
        session = store.get(tenant_id, 'sessions', session['id'])
        for key in ('pending', 'midturn'):
            session['slack'].pop(key, None)
        session['slack']['error'] = None
        store.put(tenant_id, 'sessions', session)
        return
    if runner.busy(tenant_id, session['id']):
        return await during(store, runner, tenant_id, session, client, headers)
    fresh = await fresh_messages(client, headers, listen)
    if not fresh:
        if listen.get('error'):
            listen['error'] = None
            store.put(tenant_id, 'sessions', session)
        return
    message = fresh[0]
    listen['since'] = message['ts']
    listen['acked'] = [t for t in listen.get('acked') or [] if float(t) > float(message['ts'])]
    listen.pop('midturn', None)
    store.put(tenant_id, 'sessions', session)  # Moved past it first, so a message that cannot start is never retried every tick.
    await react(client, headers, listen, message)
    agents = runner.agents(tenant_id)
    handoff = requested_handoff(message['text'], agents)
    if handoff:
        listen['model_id'] = handoff['id']
        listen['pinned_agent'] = {key: handoff.get(key) for key in ('id', 'name', 'model_name', 'provider')}
        listen['error'] = None
        store.put(tenant_id, 'sessions', session)
        identity = handoff.get('name') or handoff.get('model_name') or handoff['requested_model']
        model = handoff.get('model_name')
        if model and model.casefold() != identity.casefold():
            identity += f' ({model})'
        thread = message.get('thread_ts', message['ts'])
        await call(client, headers, 'chat.postMessage', channel=listen['channel'], thread_ts=thread,
                   text=(PREFIX + mention_of(listen, message.get('user')) +
                         f'Switched this Slack conversation to {identity}. It is ready; send the review.')[:39000])
        store.event(tenant_id, session['id'], 'slack.handoff', f'Slack switched this conversation to {identity}.')
        return
    selected = requested_agent(message['text'], agents)
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
