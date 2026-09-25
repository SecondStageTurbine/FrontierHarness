"""Public agent benchmarks, as a second opinion on how capable each connected agent is.

DeepSWE (deepswe.datacurve.ai) runs every model through one neutral harness on real repository
work and publishes its pass rate and mean cost per task: it speaks to coding and repository skill,
and sets the cost class. Terminal-Bench (tbench.ai) runs models inside the agents themselves (Claude
Code, Codex) on terminal tasks: builds, environments, debugging. It speaks to tool use and debugging.
Both are refreshed as new runs land, so Frontier re-reads each at most once a day and keeps the last
copy for offline use. Only models a board names are affected; the rest keep their profiles. Nothing
about this computer or its projects is sent; each request is a plain download.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx

REFRESH_SECONDS = 24 * 3600
_memo: dict[str, tuple[float, dict]] = {}
log = logging.getLogger('frontier.benchmark')


def normalise(identifier):
    name = identifier.lower().rsplit('/', 1)[-1]
    name = re.sub(r'(:cloud|-free)$', '', name)
    return re.sub(r'[._\s]+', '-', name).strip('-')


def parse_deepswe(response):
    """The published JSON: one row per harness, model and reasoning effort."""
    return [{'model': normalise(r['model']), 'pass': r['pass_at_1'], 'cost': r.get('mean_cost_usd'), 'effort': r.get('reasoning_effort')}
            for r in response.json().get('rows') or [] if r.get('model') and isinstance(r.get('pass_at_1'), (int, float))]


def parse_tbench(response):
    """Terminal-Bench publishes no data file; its rows are embedded in the page as escaped JSON.

    ponytail: reads the page's embedded data, so a site redesign breaks it. It then logs, keeps the
    last copy, and routing carries on with DeepSWE and track records alone.
    """
    page = response.text.replace('\\"', '"')
    found_rows = re.search(r'"rows"\s*:\s*(?=\[\s*\{\s*"id")', page)
    if not found_rows:
        raise ValueError('the Terminal-Bench page no longer carries its rows where Frontier looks')
    rows, _ = json.JSONDecoder().raw_decode(page[found_rows.end():])
    found = []
    for row in rows:
        meta, metrics = row.get('metadata') or {}, row.get('metrics') or {}
        label = (meta.get('model_display') or {}).get('label') or ''
        org = (meta.get('model_org') or {}).get('label') or ''
        if not label or not isinstance(metrics.get('accuracy'), (int, float)):
            continue
        # Anthropic's rows say "Opus 5"; Frontier and DeepSWE say claude-opus-5.
        model = normalise(('claude ' if org == 'Anthropic' and not label.lower().startswith('claude') else '') + label)
        found.append({'model': model, 'pass': metrics['accuracy'] / 100, 'effort': meta.get('reasoning_effort'),
                      'harness': (meta.get('agent_display') or {}).get('label')})
    return found


SOURCES = {
    'deepswe': ('https://deepswe.datacurve.ai/artifacts/v1.1/leaderboard-live.json', parse_deepswe),
    'tbench': ('https://www.tbench.ai/', parse_tbench),
}


def enabled():
    return os.environ.get('FRONTIER_BENCHMARKS', 'on') != 'off'  # The tests turn downloads off.


async def refresh(directory):
    """Download any board whose saved copy is more than a day old. A failure keeps the old copy."""
    if not enabled():
        return
    async with httpx.AsyncClient(timeout=5, follow_redirects=True, headers={'User-Agent': 'Frontier'}) as client:
        for name, (url, parse) in SOURCES.items():
            path = Path(directory)/f'benchmark-{name}.json'
            if path.is_file() and time.time() - path.stat().st_mtime < REFRESH_SECONDS:
                continue
            try:
                response = await client.get(url)
                response.raise_for_status()
                path.write_text(json.dumps(parse(response)), encoding='utf-8')
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
                log.warning('Could not read the %s leaderboard (%s); keeping the last copy.', name, exc)
                # Try again tomorrow, not before every turn.
                try:
                    path.touch() if path.is_file() else path.write_text('[]', encoding='utf-8')
                except OSError:
                    pass


def table(directory):
    """{board: {model: its best configuration}} from the saved copies."""
    boards = {}
    for name in SOURCES:
        path = Path(directory)/f'benchmark-{name}.json'
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        if _memo.get(str(path), (None,))[0] != stamp:
            try:
                rows = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                rows = []
            best = {}
            for row in rows if isinstance(rows, list) else []:
                # ponytail: the best reasoning effort stands for the model; Frontier does not choose efforts per turn.
                if row['model'] not in best or row['pass'] > best[row['model']]['pass']:
                    best[row['model']] = row
            _memo[str(path)] = (stamp, best)
        boards[name] = _memo[str(path)][1]
    return boards


def find(model, board):
    """One board's entry for a connected agent, or None when that board does not list it."""
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


def match(model, boards):
    """{board: entry} for every board that lists this agent; None when none does."""
    found = {name: entry for name, board in (boards or {}).items() if (entry := find(model, board))}
    return found or None


def skill(rate, low=0.35, high=0.75):
    """A pass rate as a 0-10 skill: `low` and below reads 5, `high` and above reads 10."""
    return max(3, min(10, round(5 + (rate - low) / (high - low) * 5)))


# Each board's own difficulty: Terminal-Bench's best agents pass under 60%, DeepSWE's about 75%.
SCALES = {'deepswe': (0.35, 0.75), 'tbench': (0.15, 0.60)}
# What each board speaks to. Where only one board lists a model, it speaks to all four.
SKILLS = {'deepswe': ('coding', 'repository'), 'tbench': ('tool_use', 'debugging')}


def skills(found):
    result = {}
    for name, entry in found.items():
        covered = SKILLS[name] if len(found) > 1 else ('coding', 'repository', 'tool_use', 'debugging')
        for key in covered:
            result[key] = skill(entry['pass'], *SCALES[name])
    return result


def cost_class(cost):
    if not isinstance(cost, (int, float)):
        return None
    return 'low' if cost < 2 else 'medium' if cost < 6 else 'high'


def describe(found):
    parts = []
    if (entry := (found or {}).get('deepswe')):
        cost = f', about ${entry["cost"]:.2f} per task' if isinstance(entry.get('cost'), (int, float)) else ''
        parts.append(f'DeepSWE {entry["pass"] * 100:.0f}% of tasks passed{cost}')
    if (entry := (found or {}).get('tbench')):
        harness = f' in {entry["harness"]}' if entry.get('harness') else ''
        parts.append(f'Terminal-Bench {entry["pass"] * 100:.0f}%{harness}')
    return '; '.join(parts)
