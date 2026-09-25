"""Which local agents are actually running.

Several local models can be configured while only one holds the GPU at a time. OpenCode's own
config names the server each local provider talks to, so before Adaptive counts a local agent
as a candidate — and before a manual pick spends a turn on a server that is not there — the
harness asks that server whether it is up and serving the model. Cloud agents are assumed up;
a server that answers with an empty list is taken at its word that it is running.
"""
import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

PROBE_TIMEOUT = 1.5
CACHE_TTL = 10.0
_cache: dict[str, tuple[float, list | None]] = {}


def config_path():
    return Path(os.environ.get('OPENCODE_CONFIG') or Path.home()/'.config'/'opencode'/'opencode.json')


def local_providers():
    """OpenCode provider name -> base URL, for every provider that points at this machine.

    Only the address is read. Whatever else the file holds stays where it is.
    """
    try:
        data = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    found = {}
    for name, spec in (data.get('provider') or {}).items():
        base = ((spec or {}).get('options') or {}).get('baseURL') or ''
        if (urlparse(base).hostname or '') in ('127.0.0.1', 'localhost', '::1'):
            found[name] = base.rstrip('/')
    return found


def server_for(model, providers=None):
    """(provider, base URL, model id on that server) for a local OpenCode model, else None."""
    if model.get('provider') != 'opencode_cli':
        return None
    name, _, ident = (model.get('model_name') or '').partition('/')
    providers = local_providers() if providers is None else providers
    if name not in providers or not ident:
        return None
    return name, providers[name], ident


def context_limit(model):
    """The context window, in tokens, OpenCode's settings give a local model; None when unknown."""
    found = server_for(model)
    if not found:
        return None
    try:
        data = json.loads(config_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    spec = (((data.get('provider') or {}).get(found[0]) or {}).get('models') or {}).get(found[2]) or {}
    limit = (spec.get('limit') or {}).get('context')
    return limit if isinstance(limit, int) and limit > 0 else None


async def served_models(base):
    """The model ids a server lists, or None when nothing answers. Cached briefly, because the
    picker, the router and a turn may all ask within the same few seconds."""
    now = time.monotonic()
    hit = _cache.get(base)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, trust_env=False) as client:
            response = await client.get(base + '/models')
            ids = [m.get('id') for m in (response.json().get('data') or [])] if response.status_code == 200 else None
    except Exception:
        ids = None
    _cache[base] = (now, ids)
    return ids


def serves(ids, ident):
    # Ollama lists what it has pulled with a tag ("gemma4:latest"); llama-server lists its alias as is.
    return any(i == ident or (i or '').startswith(ident + ':') for i in ids)


async def availability(models):
    """model id -> True (up and serving it), False (down, or not serving it), None (not a local server)."""
    providers = local_providers()
    targets = {m['id']: server_for(m, providers) for m in models}
    bases = sorted({t[1] for t in targets.values() if t})
    listed = dict(zip(bases, await asyncio.gather(*(served_models(b) for b in bases)))) if bases else {}
    result = {}
    for model_id, target in targets.items():
        if not target:
            result[model_id] = None
            continue
        ids = listed.get(target[1])
        result[model_id] = ids is not None and (not ids or serves(ids, target[2]))
    return result


def where(model):
    target = server_for(model)
    return f'{target[0]} at {target[1]}' if target else None
