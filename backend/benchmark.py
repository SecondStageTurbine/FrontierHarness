"""The public DeepSWE leaderboard, as a second opinion on how capable each connected agent is.

DeepSWE (deepswe.datacurve.ai) runs models through the same agent harness on real software
engineering tasks and publishes each one's pass rate and mean cost per task. It is refreshed as
new runs are imported, so Frontier re-reads it at most once a day and keeps the last copy for
offline use. Only models the leaderboard names are affected; local and unlisted models keep their
profiles. Nothing about this computer or its projects is sent; the request is a plain download.
"""
import json
import os
import re
import time
from pathlib import Path

import httpx

URL = 'https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json'
REFRESH_SECONDS = 24 * 3600
CACHE_NAME = 'deepswe-leaderboard.json'
_memo: dict[str, tuple[float, dict]] = {}


def source():
    # An empty FRONTIER_BENCHMARK_URL turns the download off (the tests do this).
    return os.environ.get('FRONTIER_BENCHMARK_URL', URL)


async def refresh(directory):
    """Download the leaderboard when the saved copy is more than a day old. Failure keeps the old copy."""
    url, path = source(), Path(directory)/CACHE_NAME
    if not url or (path.is_file() and time.time() - path.stat().st_mtime < REFRESH_SECONDS):
        return
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        if isinstance(data.get('rows'), list):
            path.write_text(json.dumps(data), encoding='utf-8')
    except (httpx.HTTPError, ValueError, OSError, AttributeError):
        # Offline: keep the last copy and try again tomorrow, not before every turn.
        try:
            path.touch() if path.is_file() else path.write_text('{"rows": []}', encoding='utf-8')
        except OSError:
            pass


def table(directory):
    """Leaderboard model -> its best configuration: pass@1, mean cost per task in USD, reasoning effort."""
    path = Path(directory)/CACHE_NAME
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    if _memo.get(str(path), (None,))[0] != stamp:
        try:
            rows = json.loads(path.read_text(encoding='utf-8')).get('rows') or []
        except (OSError, ValueError):
            rows = []
        best = {}
        for row in rows:
            name, rate = row.get('model'), row.get('pass_at_1')
            if not name or not isinstance(rate, (int, float)):
                continue
            # ponytail: the best reasoning effort stands for the model; Frontier does not choose efforts per turn.
            if name not in best or rate > best[name]['pass']:
                best[name] = {'model': name, 'pass': rate, 'cost': row.get('mean_cost_usd'), 'effort': row.get('reasoning_effort')}
        _memo[str(path)] = (stamp, best)
    return _memo[str(path)][1]


def normalise(identifier):
    name = identifier.lower().rsplit('/', 1)[-1]
    name = re.sub(r'(:cloud|-free)$', '', name)
    return re.sub(r'[._\s]+', '-', name)


def match(model, board):
    """The leaderboard entry for one connected agent, or None when the leaderboard does not list it."""
    if not board or not model.get('model_name'):
        return None
    name = normalise(model['model_name'])
    if name in board:
        return board[name]
    if model.get('provider') == 'claude_cli' and re.fullmatch(r'[a-z]+', name):
        # Claude Code's aliases (opus, sonnet, fable) mean the newest model of that family.
        family = [entry for key, entry in board.items() if re.fullmatch(rf'claude-{name}-[\d-]+', key)]
        if family:
            return max(family, key=lambda e: [int(n) for n in e['model'].split('-')[2:] if n.isdigit()])
    return None


def skill(rate):
    """A pass rate as a 0-10 skill: 35% and below reads 5, 75% and above reads 10."""
    return max(3, min(10, round(5 + (rate - 0.35) / 0.40 * 5)))


def cost_class(cost):
    if not isinstance(cost, (int, float)):
        return None
    return 'low' if cost < 2 else 'medium' if cost < 6 else 'high'


def describe(entry):
    if not entry:
        return ''
    cost = f', about ${entry["cost"]:.2f} per task' if isinstance(entry.get('cost'), (int, float)) else ''
    return f'DeepSWE benchmark {entry["pass"] * 100:.0f}% of tasks passed{cost}'
