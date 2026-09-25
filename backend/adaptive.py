"""Adaptive routing: pick the least expensive agent that can reliably take this turn.

Nothing here knows a provider by name. An agent advertises what it is good at through a
capability profile on its model row — defaults by provider and model family, overridable per
row — and a task is described by the capabilities it needs. The router compares the two. Adding
an agent is a model row plus, at most, a profile; the algorithm does not change.

The router runs before a turn, on cheap signals: the request's wording and what a walk of the
project folder reveals, refined by an optional small classifier model when the workspace names
one. A turn is one opaque subprocess, so there is no "after the agent inspected the repository"
boundary inside it to reassess at; the harness does that inspection itself, up front, and it
reassesses at the one boundary it does own — the end of an attempt — escalating to a stronger
agent with a structured handoff when the attempt failed or the agent said it could not.
"""
import asyncio
import json
import re
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field
from . import benchmark

ADAPTIVE = 'adaptive'
CAPABILITIES = ('coding', 'reasoning', 'planning', 'debugging', 'architecture', 'review',
                'tool_use', 'repository', 'instruction_following', 'speed')
COST_RANK = {'free': 0, 'low': 1, 'medium': 2, 'high': 3}
# A candidate may fall short of a requirement by this much and still count as sufficient;
# profiles are estimates, and a point of slack keeps a cheap agent from losing to noise.
TOLERANCE = 1
MAX_ESCALATIONS = 2

# ── Capability registry ──────────────────────────────────────────────────────
# Routing intentions, not permanent truths. Every number can be overridden on the model row,
# which is what a future local coder or reasoner does to announce itself.
PROVIDER_PROFILES = {
    'claude_cli':   dict(location='cloud', cost_class='high',   coding=8,  reasoning=10, planning=10, debugging=8, architecture=10, review=10, tool_use=9, repository=9, instruction_following=9, speed=5),
    'codex_cli':    dict(location='cloud', cost_class='medium', coding=10, reasoning=8,  planning=7,  debugging=9, architecture=7,  review=7,  tool_use=9, repository=9, instruction_following=9, speed=6),
    'opencode_cli': dict(location='cloud', cost_class='low',    coding=7,  reasoning=7,  planning=6,  debugging=6, architecture=5,  review=6,  tool_use=8, repository=7, instruction_following=7, speed=7),
    'gemini_cli':   dict(location='cloud', cost_class='low',    coding=7,  reasoning=8,  planning=7,  debugging=7, architecture=7,  review=7,  tool_use=7, repository=8, instruction_following=8, speed=8),
}
# Refinements keyed on the model identifier. Matched by substring, first match wins per key.
FAMILY_PROFILES = [
    ('ollama/',    dict(location='local', cost_class='free', speed=8, coding=6, reasoning=5, planning=4, debugging=5, architecture=4, review=5, tool_use=6, repository=6, instruction_following=6)),
    ('lmstudio/',  dict(location='local', cost_class='free', speed=8, coding=6, reasoning=5, planning=4, debugging=5, architecture=4, review=5, tool_use=6, repository=6, instruction_following=6)),
    ('haiku',      dict(cost_class='low', speed=9, coding=6, reasoning=6, planning=5, debugging=6, architecture=5, review=6)),
    ('flash',      dict(cost_class='low', speed=9, coding=6, reasoning=6, planning=5, debugging=6, architecture=5, review=6)),
    ('mini',       dict(cost_class='low', speed=9, coding=6, reasoning=6, planning=5, debugging=6, architecture=5, review=6)),
    ('sonnet',     dict(reasoning=9, planning=9, architecture=9, speed=6)),
    # Codex's families, as `codex debug models` describes them. Astra is the frontier tier and costs
    # the most; Sol is the coding workhorse; Luna is the fast, cheap tier. Applied after 'mini' so a Codex
    # identifier is judged by its own family. Older identifiers keep the Codex provider default.
    ('gpt-6-astra', dict(cost_class='high',   coding=10, reasoning=10, planning=9, debugging=10, architecture=9, review=9, tool_use=10, repository=10, instruction_following=9, speed=5)),
    ('gpt-6-sol',   dict(cost_class='medium', coding=9,  reasoning=8,  planning=8, debugging=9,  architecture=8, review=8, tool_use=9,  repository=9,  instruction_following=9, speed=7)),
    ('gpt-6-luna',  dict(cost_class='low',    coding=7,  reasoning=6,  planning=6, debugging=6,  architecture=5, review=6, tool_use=8,  repository=7,  instruction_following=8, speed=9)),
    # Last, so a free model stays free whatever family its name also matches (a free "flash" is still free).
    ('-free',      dict(cost_class='free')),
]


# ── Track record ─────────────────────────────────────────────────────────────
# What each agent actually did with the work it was given: team tasks finished or failed, fixes the
# lead asked for, context overflows, and Adaptive turns that completed or had to escalate. It is kept
# per model row and model identifier, so changing a row's model starts its record again.
TRACK_KIND = 'track_records'
TRACK_MINIMUM = 3  # Tasks before the record moves a profile; fewer say too little.


def track_for(model, records):
    record = records.get(model['id'])
    return record if record and record.get('model_name') == model.get('model_name') else None


def record_outcome(store, tenant_id, model, **counts):
    """Add to one agent's record: done, failed, fixes, overflows."""
    try:
        row = track_for(model, {model['id']: store.get(tenant_id, TRACK_KIND, model['id'])}) or {}
    except Exception:
        row = {}
    row = {'done': 0, 'failed': 0, 'fixes': 0, 'overflows': 0, **row, 'id': model['id'], 'model_name': model.get('model_name')}
    for key, value in counts.items():
        row[key] = row.get(key, 0) + value
    store.put(tenant_id, TRACK_KIND, row)


def first_pass(track):
    """(tasks accepted without a fix, tasks attempted), from a record."""
    tried = (track or {}).get('done', 0) + (track or {}).get('failed', 0)
    return max(0, (track or {}).get('done', 0) - (track or {}).get('fixes', 0)), tried


def track_shift(track):
    """How far the record moves every skill: +1 for a clean record, down to -2 for a poor one."""
    clean, tried = first_pass(track)
    if tried < TRACK_MINIMUM:
        return 0
    # ponytail: one shift for every skill; per-skill records (debugging vs review) if routing needs finer grain.
    return max(-2, min(1, round((clean / tried - 0.7) * 5)))


def describe_track(track):
    clean, tried = first_pass(track)
    if not tried:
        return 'no track record yet'
    parts = [f'{clean} of {tried} tasks accepted first time']
    if track.get('failed'):
        parts.append(f'{track["failed"]} failed')
    if track.get('overflows'):
        parts.append(f'{track["overflows"]} ran out of context')
    return 'track record: ' + ', '.join(parts)


def profile(model):
    """What one agent is good at: provider default, family refinement, the public benchmark, its track
    record on this computer, then the row's own word."""
    base = dict(PROVIDER_PROFILES.get(model['provider'], {}))
    if not base:
        return None  # An API-key model has no agent loop; it cannot take a turn at all.
    name = (model.get('model_name') or '').lower()
    for needle, refinement in FAMILY_PROFILES:
        if needle in name:
            base.update(refinement)
    bench = model.get('benchmark')
    if bench:
        base.update(benchmark.skills(bench))
        cost = benchmark.cost_class((bench.get('deepswe') or {}).get('cost'))  # Only DeepSWE prices a task.
        if base.get('cost_class') != 'free' and cost:
            base['cost_class'] = cost
    shift = track_shift(model.get('track'))
    for key in CAPABILITIES:
        if shift and key != 'speed':
            base[key] = max(0, min(10, base[key] + shift))
    for key, value in (model.get('capabilities') or {}).items():
        if key in CAPABILITIES and isinstance(value, (int, float)):
            base[key] = max(0, min(10, int(value)))
    if model.get('cost_class') in COST_RANK:
        base['cost_class'] = model['cost_class']
    base['id'] = model['id']
    base['name'] = model['name']
    return base


# ── Task requirements ────────────────────────────────────────────────────────
class TaskRequirements(BaseModel):
    """What a turn needs. Machine-readable and validated: routing never reads free text."""
    model_config = ConfigDict(extra='forbid')
    task_type: str = Field(pattern=r'^(explain|edit_simple|implementation|debugging|refactor|analysis|review|planning)$')
    complexity: str = Field(pattern=r'^(low|medium|high)$')
    risk: str = Field(pattern=r'^(low|medium|high)$')
    requirements: dict[str, int]
    needs_repository_inspection: bool = True
    reason: str = Field(default='', max_length=300)

    def need(self, capability):
        return self.requirements.get(capability, 0)


# Base requirement vectors by task type; complexity and risk then push them around. Speed is a
# preference rather than a requirement, so it is never demanded here.
TASK_BASE = {
    'explain':        dict(coding=3, reasoning=4, planning=1, debugging=1, architecture=2, review=2, tool_use=4, repository=5, instruction_following=5),
    'edit_simple':    dict(coding=4, reasoning=2, planning=1, debugging=1, architecture=1, review=1, tool_use=6, repository=4, instruction_following=7),
    'implementation': dict(coding=8, reasoning=5, planning=4, debugging=5, architecture=3, review=3, tool_use=8, repository=7, instruction_following=7),
    'debugging':      dict(coding=7, reasoning=7, planning=3, debugging=9, architecture=4, review=4, tool_use=8, repository=8, instruction_following=6),
    'refactor':       dict(coding=8, reasoning=6, planning=5, debugging=5, architecture=6, review=5, tool_use=8, repository=9, instruction_following=7),
    'analysis':       dict(coding=4, reasoning=9, planning=6, debugging=5, architecture=9, review=6, tool_use=6, repository=9, instruction_following=6),
    'review':         dict(coding=6, reasoning=8, planning=3, debugging=6, architecture=8, review=10, tool_use=6, repository=8, instruction_following=6),
    'planning':       dict(coding=3, reasoning=9, planning=10, debugging=3, architecture=9, review=5, tool_use=4, repository=8, instruction_following=6),
}
TYPE_SIGNALS = [
    ('review',         r'\b(review|audit|critique|assess (this|the) (implementation|code|pr))\b'),
    ('planning',       r'\b(design|strategy|plan (a|the|for)|roadmap|propose (an? )?(approach|architecture)|migration strategy)\b'),
    ('analysis',       r'\b(analy[sz]e|why (is|are|does|do)|explain why|root cause|tightly coupled|trade-?offs?|investigate|understand (how|why))\b'),
    ('debugging',      r'\b(fix|bug|broken|crash|fail(s|ing|ure)?|error|exception|intermittent|500|timeout|not working|regression)\b'),
    ('refactor',       r'\b(refactor|rename|everywhere|across (the|all)|move .* to|extract|consolidate|deduplicate)\b'),
    ('implementation', r'\b(implement|add|build|create|write|integrate|support|endpoint|feature|pagination|wire up|migrate)\b'),
    ('edit_simple',    r'\b(change|replace|update|set|tweak|adjust|typo|spelling|format|reword|color|colour|label|text)\b'),
    ('explain',        r'\b(explain|what (does|is)|how (does|do)|describe|summari[sz]e|walk me through|tell me)\b'),
]
RISK_SIGNALS = {
    'security': r'\b(auth(entication|orization)?|oauth|jwt|session|password|token|secret|credential|permission|csrf|xss|inject|encrypt|security)\b',
    'data':     r'\b(database|schema|migration|drop|delete|truncate|wipe|backup|restore|data loss|prod(uction)?)\b',
    'money':    r'\b(payment|billing|invoice|stripe|checkout|refund|ledger)\b',
    'external': r'\b(api key|webhook|third[- ]party|integration|external service)\b',
}
SCOPE_SIGNALS = r'\b(everywhere|across|entire|whole (codebase|project|repo)|all (files|modules|services)|multiple (files|services|modules)|end[- ]to[- ]end)\b'
# Repository paths that raise the stakes of a turn regardless of how the request is worded.
FLAGGED_PATHS = {
    'security': r'(^|/)(auth|oauth|sessions?|identity|login|permissions?|acl)(/|\.|$)',
    'data':     r'(^|/)(migrations?|schema|models?|db|database)(/|\.|$)',
    'money':    r'(^|/)(billing|payments?|checkout|invoic)',
}


def repository_signals(files, tenant_id, project_id):
    """What a walk of the folder says before any agent looks: size, languages, flagged areas.

    Deliberately shallow — names and counts, never contents — so it costs nothing worth naming.
    """
    try:
        entries, _ = files.walk(tenant_id, project_id)
    except (ValueError, OSError):
        return {'file_count': 0, 'languages': [], 'flagged': []}
    suffixes = {}
    flagged = set()
    for entry in entries:
        path = entry['path'].lower()
        suffix = Path(path).suffix
        if suffix:
            suffixes[suffix] = suffixes.get(suffix, 0) + 1
        for flag, pattern in FLAGGED_PATHS.items():
            if re.search(pattern, path):
                flagged.add(flag)
    languages = [s for s, _ in sorted(suffixes.items(), key=lambda kv: -kv[1])[:4]]
    return {'file_count': len(entries), 'languages': languages, 'flagged': sorted(flagged)}


def _derive(task_type, broad_scope, ambiguous, risks, repo, note=None):
    """A task type plus a handful of yes/no signals, turned into a requirements vector.

    Shared by the regex heuristics and the TypeSafe classifier below so the two ever differ
    only in how they detect `broad_scope` / `ambiguous` / `risks`, never in what one of those
    signals is worth — a routing rule lives in exactly one place regardless of which detector
    found the signal.
    """
    needs = dict(TASK_BASE[task_type])
    bumps = 0
    if broad_scope:
        needs['repository'] = min(10, needs['repository'] + 2); needs['planning'] = min(10, needs['planning'] + 1); bumps += 1
    if risks:
        needs['reasoning'] = min(10, needs['reasoning'] + 2); needs['review'] = min(10, needs['review'] + 2); bumps += 1
        if 'security' in risks or 'data' in risks:
            needs['architecture'] = min(10, needs['architecture'] + 2)
    if ambiguous:
        needs['reasoning'] = min(10, needs['reasoning'] + 2); needs['repository'] = min(10, needs['repository'] + 1); bumps += 1
    if repo.get('file_count', 0) > 400:
        needs['repository'] = min(10, needs['repository'] + 1)
    complexity = 'high' if bumps >= 2 or task_type in ('analysis', 'planning') else 'medium' if bumps == 1 or task_type in ('implementation', 'debugging', 'refactor', 'review') else 'low'
    risk = 'high' if len(risks) >= 2 or 'data' in risks else 'medium' if risks else 'low'
    reason = f'{task_type}, {complexity} complexity' + (f', {"/".join(risks)} risk' if risks else '') + (', broad scope' if broad_scope else '') + (', ambiguous' if ambiguous else '')
    if note:
        reason = f'{reason} ({note})'
    return TaskRequirements(task_type=task_type, complexity=complexity, risk=risk, requirements=needs,
                            needs_repository_inspection=task_type != 'explain' or broad_scope, reason=reason)


def heuristic_requirements(content, repo):
    """Deterministic first pass. Length is not a signal: a short request can be the hardest."""
    text = content.lower()
    task_type = next((kind for kind, pattern in TYPE_SIGNALS if re.search(pattern, text)), 'implementation')
    risks = [name for name, pattern in RISK_SIGNALS.items() if re.search(pattern, text)]
    risks += [flag for flag in repo.get('flagged', []) if flag not in risks and task_type != 'explain']
    files_named = len(re.findall(r'[\w/.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|cs|rb|php|sql|md|yaml|yml|json|toml)\b', text))
    broad_scope = bool(re.search(SCOPE_SIGNALS, text)) or files_named >= 3
    # Ambiguity: a broad noun with no anchor is harder than a precise one. "Fix authentication."
    # names a whole subsystem in two words; "Add pagination to the customer API." names its target.
    ambiguous = task_type in ('debugging', 'implementation', 'analysis') and files_named == 0 and len(text.split()) <= 4
    return _derive(task_type, broad_scope, ambiguous, risks, repo)


ROUTER_PROMPT = (
    'You are the Adaptive Model Router for an AI software-engineering harness. Your job is NOT to '
    'solve the request. Determine the capabilities required to complete it successfully. Evaluate '
    'coding, reasoning, planning, debugging, architecture, review, tool_use, repository understanding '
    'and instruction_following, each 0-10, plus task_type, complexity and risk. Do not judge '
    'complexity by prompt length: a short prompt can be a hard task and a long one simple work. '
    'Prefer the minimum capabilities reasonably likely to succeed; do not request the strongest '
    'model by default. Return ONLY a JSON object with keys task_type (one of explain, edit_simple, '
    'implementation, debugging, refactor, analysis, review, planning), complexity (low|medium|high), '
    'risk (low|medium|high), requirements (object of the nine capabilities to integers 0-10), '
    'needs_repository_inspection (boolean), reason (one short sentence). No other text.'
)


async def classify(content, repo, classifier, decrypt):
    """Heuristics, refined by a small classifier when the workspace names one.

    The classifier is either a TypeSafe row (see `typesafe_requirements` below) or any keyed
    OpenAI-compatible or Anthropic model — a local Ollama model is the intended case for the
    latter. It is replaceable either way: the workspace setting points at a model row, not at a
    provider. Any failure falls back to the heuristic answer, so routing never blocks a turn.
    """
    baseline = heuristic_requirements(content, repo)
    if not classifier:
        return baseline, 'heuristics'
    key = decrypt(classifier['encrypted_key']) if classifier.get('encrypted_key') else None
    if classifier['provider'] == 'typesafe':
        if not key:
            return baseline, 'heuristics (classifier unavailable)'
        refined = await typesafe_requirements(content, repo, key, classifier.get('model_name') or 'jev-latest')
        return (refined, classifier['name']) if refined else (baseline, 'heuristics (classifier unavailable)')
    user = json.dumps({'request': content[:4000], 'heuristic_estimate': baseline.model_dump(),
                       'repository': repo}, ensure_ascii=False)
    try:
        async with asyncio.timeout(20):
            raw = await _complete(classifier, key or 'local-no-key', user)
        payload = json.loads(_json_object(raw))
        payload['requirements'] = {k: max(0, min(10, int(v))) for k, v in (payload.get('requirements') or {}).items() if k in CAPABILITIES}
        for cap_key in CAPABILITIES[:-1]:
            payload['requirements'].setdefault(cap_key, baseline.need(cap_key))
        refined = TaskRequirements.model_validate({k: payload.get(k, getattr(baseline, k)) for k in ('task_type', 'complexity', 'risk', 'requirements', 'needs_repository_inspection', 'reason')})
        return refined, classifier['name']
    except Exception:
        return baseline, 'heuristics (classifier unavailable)'


async def _complete(model, key, user):
    if model['provider'] == 'anthropic':
        from anthropic import AsyncAnthropic
        async with AsyncAnthropic(api_key=key, max_retries=0) as client:
            r = await client.messages.create(model=model['model_name'], max_tokens=400, system=ROUTER_PROMPT,
                                             messages=[{'role': 'user', 'content': user}])
            return ''.join(c.text for c in r.content if c.type == 'text')
    from openai import AsyncOpenAI
    async with AsyncOpenAI(api_key=key, base_url=model.get('base_url') or 'https://api.openai.com/v1', max_retries=0) as client:
        r = await client.chat.completions.create(model=model['model_name'], max_tokens=400, temperature=0,
                                                 messages=[{'role': 'system', 'content': ROUTER_PROMPT}, {'role': 'user', 'content': user}])
        return r.choices[0].message.content or ''


def _json_object(text):
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < 0:
        raise ValueError('no JSON object in classifier output')
    return text[start:end+1]


# ── TypeSafe classifier ──────────────────────────────────────────────────────
# TypeSafe (https://docs.typesafe.ai) returns typed, calibrated judgments instead of a chat
# model's free-form text, so classification is a handful of Choice/Noul questions rather than
# "ask a model to write JSON and hope it parses." Every answer's own confidence is used, rather
# than trusted blindly: `_derive` above is the one place a signal becomes a requirement, so
# swapping this in for the regex path changes nothing about what a signal is worth.
TYPESAFE_ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
TYPESAFE_TASK_TYPES = {
    'explain': 'Wants an explanation of existing code or behavior. No changes requested.',
    'edit_simple': 'A small, well-specified change: rename, replace, tweak, formatting, wording, a single clear value change.',
    'implementation': 'Add or build new functionality: a feature, endpoint, integration, capability that does not exist yet.',
    'debugging': 'Fix a bug, crash, failure, or behavior that is wrong compared to what it should be.',
    'refactor': 'Reorganize, rename, or restructure existing code without changing its behavior.',
    'analysis': 'Understand or explain WHY the system behaves a certain way; investigate a root cause; no change requested yet.',
    'review': 'Review or audit existing code or a proposed change for correctness, security, or quality.',
    'planning': 'Design an approach, architecture, or strategy before any implementation happens.',
}
TYPESAFE_RISK_NOULS = {
    'security_risk': {'true': 'Touches authentication, authorization, sessions, credentials, permissions, or user identity.', 'false': 'Does not touch any of those.'},
    'data_risk': {'true': 'Touches a database schema, migration, or could delete, overwrite, or lose stored data.', 'false': 'Does not.'},
    'money_risk': {'true': 'Touches payments, billing, invoicing, or financial transactions.', 'false': 'Does not.'},
    'external_risk': {'true': 'Touches a third-party API, webhook, or external integration.', 'false': 'Does not.'},
}
# Verified against jev-latest on 12 representative requests: task_type matched all 12 (0.55-1.0
# confidence); risk nouls matched every seeded case at >0.6 / <0.4. The ambiguous wording below
# is the second of two tried — the first read almost every short request as ambiguous, since
# "without inspecting the code" is technically true of any one-line instruction. This phrasing
# isolates a request that names no concrete target ("fix authentication", "improve performance")
# from one that does ("change this button green"), and separately, it reads high by nature for
# investigative task types (explain/analysis/review/planning are open-ended on purpose) — which
# is why `typesafe_requirements` only applies it to the four task types where a concrete target
# is expected, the same restriction the regex heuristic already places on its own `ambiguous`.
TYPESAFE_AMBIGUOUS = {
    'instructions': 'Does this request name a broad area or symptom without saying what specifically should change, so many different concrete changes could satisfy it? Or does it name a specific, concrete target and outcome, even if minor implementation details are left out?',
    'criteria': {
        'true': "Names a broad area, goal, or symptom, not a concrete target (e.g. 'fix authentication', 'improve performance', 'clean up the code', 'make it better'). Many unrelated changes could all reasonably satisfy it.",
        'false': "Names a specific, concrete target and a clear intended outcome, even without file paths or line numbers (e.g. 'change this button from blue to green', 'add pagination to the customer API').",
    },
}
# Only task types with a determinate target; see the note above.
AMBIGUITY_APPLIES_TO = ('debugging', 'implementation', 'refactor', 'edit_simple')


async def typesafe_requirements(content, repo, api_key, model='jev-latest'):
    """The same requirements vector as `heuristic_requirements`, from TypeSafe's judgments
    instead of regexes. Returns `None` on any failure so the caller falls back to heuristics."""
    questions = {'task_type': {'type': 'choice', 'instructions': 'What kind of software-engineering request is this?', 'criteria': TYPESAFE_TASK_TYPES},
                'broad_scope': {'type': 'noul', 'instructions': 'Is completing this request likely to require touching many files, multiple modules, or the whole project, rather than one narrow, well-contained change?'},
                'ambiguous': {'type': 'noul', **TYPESAFE_AMBIGUOUS}}
    questions.update({name: {'type': 'noul', 'instructions': f'Does this request involve {name.removesuffix("_risk")}-sensitive work?', 'criteria': criteria}
                      for name, criteria in TYPESAFE_RISK_NOULS.items()})
    body = {'state': {'request': content[:4000], 'repository': repo}, 'model': model, 'questions': questions}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(TYPESAFE_ENDPOINT, headers={'Authorization': f'Bearer {api_key}'}, json=body)
            response.raise_for_status()
            answers = response.json()['answers']
        task_type = answers['task_type']['choice']
        if task_type not in TASK_BASE:
            return None
        broad = answers['broad_scope']['noul'] > 0.6
        ambiguous = task_type in AMBIGUITY_APPLIES_TO and answers['ambiguous']['noul'] > 0.6
        risks = [name.removesuffix('_risk') for name in TYPESAFE_RISK_NOULS if answers[name]['noul'] > 0.6]
        note = f"{answers['task_type']['confidence']:.2f} confidence"
        return _derive(task_type, broad, ambiguous, risks, repo, note=note)
    except Exception:
        return None


# ── Candidate scoring ────────────────────────────────────────────────────────
def score(requirements, models, in_cooldown=lambda model_id: False):
    """Every enabled agent, ranked. Cheapest sufficient first; if none is sufficient, closest."""
    ranked = []
    for model in models:
        if model.get('enabled') is False:
            continue
        cap = profile(model)
        if cap is None:
            continue  # Hard requirement: a turn needs an agent loop, and a keyed model has none.
        deficits = {c: max(0, requirements.need(c) - cap.get(c, 0)) for c in CAPABILITIES if c != 'speed'}
        total = sum(deficits.values())
        sufficient = all(d <= TOLERANCE for d in deficits.values())
        strength = sum(cap.get(c, 0) for c in CAPABILITIES if c != 'speed')
        ranked.append({'id': model['id'], 'name': model['name'], 'provider': model['provider'], 'location': cap['location'],
                       'cost_class': cap['cost_class'], 'sufficient': sufficient, 'deficit': total, 'strength': strength,
                       'speed': cap.get('speed', 5), 'cooling': bool(in_cooldown(model['id']))})
    # A login in cooldown is not unavailable — the account pool will move past it — but it is a
    # worse bet than one that is not, so it sorts behind an equal alternative.
    ranked.sort(key=lambda r: (not r['sufficient'], r['cooling'], r['deficit'] if not r['sufficient'] else 0,
                               COST_RANK[r['cost_class']], -r['speed'], -r['strength']))
    return ranked


def choose(requirements, models, in_cooldown=lambda model_id: False):
    ranked = score(requirements, models, in_cooldown)
    if not ranked:
        return None, ranked
    best = ranked[0]
    if best['sufficient']:
        best['because'] = f'cheapest agent that covers {requirements.reason}'
    else:
        best['because'] = f'no agent fully covers {requirements.reason}; closest available'
    return best, ranked


def stronger(ranked, tried):
    """The next candidate up from everything tried so far, for an escalation. None if there is
    nothing stronger, which is how the loop stays finite."""
    tried_strength = max((r['strength'] for r in ranked if r['id'] in tried), default=-1)
    candidates = [r for r in ranked if r['id'] not in tried and r['strength'] > tried_strength]
    # One rung up, not the top of the ladder: a sufficient candidate first, then the weakest of
    # those, so a cheap agent's failure goes to the next agent rather than straight to the dearest.
    candidates.sort(key=lambda r: (-r['sufficient'], r['deficit'], r['strength']))
    return candidates[0] if candidates else None


INABILITY = re.compile(r"\b(i (cannot|can't|am unable to|was unable to|couldn't|could not)|not able to|beyond (my|the scope)|insufficient (context|information|permissions?)|cannot determine|unable to (complete|proceed|find))\b", re.I)


def struggled(reply, error, mode, changes):
    """Whether an attempt should hand over: it failed outright, or the agent said it could not
    and, when it was allowed to change the folder, changed nothing. One ordinary mistake in an
    otherwise productive attempt is not a reason to switch."""
    if error:
        return f'the attempt failed: {error}'
    if INABILITY.search(reply or '') and (mode == 'read' or not changes):
        return 'the agent reported it could not complete the task'
    return None


# ── Handoff ──────────────────────────────────────────────────────────────────
def handoff(session, message, attempts):
    """What the next agent needs to continue rather than start over. Built from what the harness
    itself knows — an agent's own tool calls happen inside its process and are not available."""
    users = [m for m in session['messages'] if m['role'] == 'user']
    return {
        'objective': users[0]['content'] if users else '',
        'latest_request': users[-1]['content'] if users else '',
        'attempts': [{'agent': a['name'], 'outcome': a['outcome'], 'reply_excerpt': (a.get('reply') or '')[:600]} for a in attempts],
        'files_changed_so_far': sorted({c['path'] for c in message.get('changes', [])}),
        'earlier_turns_changed': sorted({c['path'] for m in session['messages'] for c in (m.get('changes') or []) if m['id'] != message['id']}),
        'next_action': 'Continue the task from the current state of the project folder; do not redo work that is already there.',
    }


def render_handoff(data):
    lines = ['HANDOFF FROM A PREVIOUS AGENT ON THIS SAME TASK', f'Objective: {data["objective"]}']
    for attempt in data['attempts']:
        lines.append(f'- {attempt["agent"]}: {attempt["outcome"]}')
        if attempt['reply_excerpt']:
            lines.append(f'  It said: {attempt["reply_excerpt"]}')
    if data['files_changed_so_far']:
        lines.append('Files already changed in this turn: ' + ', '.join(data['files_changed_so_far']))
    lines.append(data['next_action'])
    return '\n'.join(lines)
