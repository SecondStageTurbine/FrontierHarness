"""Other machines running Frontier, used from this window.

Another computer with remote access on shows a pairing link (Settings → Remote access). Pasting it
here signs this Frontier in there once; the session it gets is kept encrypted in this data folder.
From then on this window can switch to that machine: its API calls go through this backend, which
adds the stored session and passes them on, so projects, sessions, agents, files and git all live and
run on the other machine. Nothing about the other machine is stored here except its address and that session.
"""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .store import uid


def path_of(directory):
    return Path(directory)/'environments.json'


def load(directory):
    try:
        return json.loads(path_of(directory).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def save(directory, items):
    path_of(directory).write_text(json.dumps(items), encoding='utf-8')


def client(timeout=15.0):
    """The HTTP client for another machine; tests replace it to talk to an app in memory."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


async def pair(store, name, link):
    """Use another machine's pairing link: take the session it grants, and check it works."""
    parts = urlsplit(link.strip())
    if parts.scheme not in ('http', 'https') or not parts.netloc or parts.path.rstrip('/') != '/pair' or 'code=' not in parts.query:
        raise ValueError('Paste the pairing link from the other machine: Settings, Remote access, Show pairing code, Copy link.')
    base = f'{parts.scheme}://{parts.netloc}'
    try:
        async with client() as http:
            answer = await http.get(link.strip())
            token = answer.cookies.get('harness_session')
            if answer.status_code == 410 or not token:
                raise ValueError('That pairing link has expired or was already used. Show a new one on the other machine.')
            check = await http.get(f'{base}/api/tenants', cookies={'harness_session': token})
    except httpx.HTTPError as exc:
        raise ValueError(f'{base} could not be reached ({exc}). Check that Frontier is running there with remote access on, and that both machines share a network or tailnet.') from None
    if check.status_code != 200:
        raise ValueError(f'{base} refused the new session (HTTP {check.status_code}).')
    items = load(store.directory)
    item = {'id': uid(), 'name': name.strip() or parts.hostname, 'url': base, 'token': store.encrypt(token), 'added_at': time.time()}
    save(store.directory, [i for i in items if i['url'] != base] + [item])
    return public(item)


def public(item):
    return {k: item[k] for k in ('id', 'name', 'url', 'added_at')}


def find(directory, env_id):
    return next((i for i in load(directory) if i['id'] == env_id), None)


def remove(directory, env_id):
    save(directory, [i for i in load(directory) if i['id'] != env_id])


async def status(store, item):
    try:
        async with client(timeout=4.0) as http:
            answer = await http.get(f'{item["url"]}/api/tenants', cookies={'harness_session': store.decrypt(item['token'])})
        return {**public(item), 'reachable': True, 'signed_in': answer.status_code == 200}
    except (httpx.HTTPError, ValueError):
        return {**public(item), 'reachable': False, 'signed_in': False}
