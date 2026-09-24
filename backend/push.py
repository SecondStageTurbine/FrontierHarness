"""Push notifications to a phone through ntfy (https://ntfy.sh, or a server you run).

Frontier's remote access is plain HTTP on your network, where browsers refuse web push, so the
phone subscribes to a private topic in the ntfy app instead and Frontier publishes to it. Off by
default. The topic name is the secret: anyone who knows it can read the notifications, so it is long
and random, and the reply's text is only included when the user asks for it.
"""
import json
import secrets
import threading
import urllib.request
from pathlib import Path

from .store import read_json, write_json

EVENTS = ('done', 'failed', 'waiting')
DEFAULTS = {'enabled': False, 'server': 'https://ntfy.sh', 'topic': None, 'events': list(EVENTS), 'details': False}


def config(directory):
    stored = read_json(Path(directory)/'push.json', {}) or {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(directory, new_topic=False, **fields):
    """Change the settings. A new topic is a fresh random name: anyone subscribed to the old one stops receiving."""
    current = config(directory)
    current.update({k: v for k, v in fields.items() if k in DEFAULTS and v is not None})
    if new_topic or not current['topic']:
        current['topic'] = 'frontier-' + secrets.token_urlsafe(18).replace('_', 'x').replace('-', 'y')
    current['server'] = (current['server'] or DEFAULTS['server']).rstrip('/')
    write_json(Path(directory)/'push.json', current)
    return current


def post(server, payload):
    request = urllib.request.Request(server, data=json.dumps(payload).encode('utf-8'), method='POST', headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status


def send(directory, event, title, message, click=None, wait=False, force=False):
    """Publish one notification if the user wants this kind. In the background unless `wait`, so a turn
    never waits on the network; a failure is dropped, as a missed notification should not stop anything."""
    settings = config(directory)
    if not settings['enabled'] or not settings['topic'] or (event not in settings['events'] and not force):
        return False
    payload = {'topic': settings['topic'], 'title': title[:200], 'message': (message or title)[:1000],
               'tags': [{'done': 'white_check_mark', 'failed': 'x', 'waiting': 'raising_hand'}[event]],
               'priority': 4 if event == 'waiting' else 3}
    if click:
        payload['click'] = click
    if wait:
        return post(settings['server'], payload)
    def deliver():
        try:
            post(settings['server'], payload)
        except OSError:
            pass
    threading.Thread(target=deliver, daemon=True).start()
    return True


def link(directory, tenant_id, project_id, session_id):
    """Where tapping the notification goes: this conversation, at the first address remote access answers on."""
    import os
    from . import remote
    port = os.environ.get('HARNESS_DESKTOP_PORT')
    addresses = remote.addresses() if remote.enabled(directory) else []
    if not port or not addresses:
        return None
    return f'http://{addresses[0]}:{port}/?t={tenant_id}&p={project_id}&s={session_id}'
