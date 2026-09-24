"""Pull requests through the GitHub CLI the user already signed in to. Frontier never holds a token."""
import asyncio
import json
import shutil

from .gitops import GitError, push, status as git_status
from .localprocess import child_env, child_flags, terminate

FIELDS = ('number,title,url,state,isDraft,mergedAt,reviewDecision,baseRefName,headRefName,statusCheckRollup,additions,deletions,author,'
          'labels,reviewRequests,latestReviews,autoMergeRequest,mergeable,mergeStateStatus')
MERGE_METHODS = {'squash': '--squash', 'merge': '--merge', 'rebase': '--rebase'}
REVIEW_EVENTS = {'approve': '--approve', 'comment': '--comment', 'request_changes': '--request-changes'}


def available():
    return shutil.which('gh') is not None


async def run(root, *args, timeout=60, ok=(0,)):
    if not available():
        raise GitError('The GitHub CLI (gh) is not installed. Install it and run `gh auth login` once.')
    proc = await asyncio.create_subprocess_exec('gh', *args, cwd=str(root), env=child_env(GIT_TERMINAL_PROMPT='0', GH_PROMPT_DISABLED='1', GH_NO_UPDATE_NOTIFIER='1', NO_COLOR='1'),
                                                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except TimeoutError:
        await terminate(proc)
        raise GitError(f'gh {args[0]} took longer than {timeout} seconds and was stopped.') from None
    text_out, text_err = out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace')
    if proc.returncode not in ok:
        raise GitError((text_err.strip() or text_out.strip() or f'gh {args[0]} failed.')[:1500])
    return text_out, text_err, proc.returncode


def summarise_checks(rollup):
    counts = {'success': 0, 'failure': 0, 'pending': 0}
    for check in rollup or []:
        state = (check.get('conclusion') or check.get('state') or check.get('status') or '').upper()
        if state in ('SUCCESS', 'NEUTRAL', 'SKIPPED'):
            counts['success'] += 1
        elif state in ('FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT', 'ACTION_REQUIRED'):
            counts['failure'] += 1
        else:
            counts['pending'] += 1
    return counts


async def current(root):
    """The pull request for the checked-out branch, or None."""
    out, err, code = await run(root, 'pr', 'view', '--json', FIELDS, ok=(0, 1))
    if code != 0:
        if 'no pull requests found' in (err + out).lower() or 'no default remote' in (err + out).lower():
            return None
        raise GitError((err.strip() or out.strip())[:800])
    pr = json.loads(out)
    auto = pr.get('autoMergeRequest')
    found = {'number': pr['number'], 'title': pr['title'], 'url': pr['url'], 'state': 'merged' if pr.get('mergedAt') else pr['state'].lower(),
             'draft': pr.get('isDraft', False), 'review': (pr.get('reviewDecision') or '').lower() or None, 'base': pr.get('baseRefName'), 'head': pr.get('headRefName'),
             'checks': summarise_checks(pr.get('statusCheckRollup')), 'additions': pr.get('additions'), 'deletions': pr.get('deletions'), 'author': (pr.get('author') or {}).get('login'),
             'labels': [label['name'] for label in pr.get('labels') or []],
             'requested': [r.get('login') or r.get('slug') or r.get('name') for r in pr.get('reviewRequests') or [] if r.get('login') or r.get('slug') or r.get('name')],
             'reviews': [{'author': (r.get('author') or {}).get('login'), 'state': (r.get('state') or '').lower()} for r in pr.get('latestReviews') or []],
             'auto_merge': (auto.get('mergeMethod') or 'merge').lower() if auto else None,
             'mergeable': (pr.get('mergeable') or '').lower() or None, 'merge_state': (pr.get('mergeStateStatus') or '').lower() or None}
    found['stack'] = await stack(root, found)
    return found


async def open_prs(root):
    out, _, _ = await run(root, 'pr', 'list', '--state', 'open', '--limit', '100', '--json', 'number,title,url,headRefName,baseRefName')
    return json.loads(out or '[]')


async def stack(root, pr):
    """Where this pull request sits in a stack: the open pull requests beneath it (whose branch it builds on),
    nearest first, and the ones built on it."""
    try:
        prs = await open_prs(root)
    except GitError:
        return {'below': [], 'above': []}
    by_head = {p['headRefName']: p for p in prs}
    below, base, seen = [], pr.get('base'), {pr.get('head')}
    while base in by_head and base not in seen:
        seen.add(base)
        below.append({k: by_head[base][k] for k in ('number', 'title', 'url', 'headRefName')})
        base = by_head[base]['baseRefName']
    above = [{k: p[k] for k in ('number', 'title', 'url', 'headRefName')} for p in prs if p['baseRefName'] == pr.get('head')]
    return {'below': below, 'above': above, 'root_base': base}


async def options(root):
    """What the pull request form can offer: the repository's labels, people who can review, and branches to
    base on, the default branch first and then every open pull request's branch, for stacking."""
    out, _, _ = await run(root, 'repo', 'view', '--json', 'nameWithOwner,defaultBranchRef')
    repo = json.loads(out)
    labels_out, _, _ = await run(root, 'label', 'list', '--limit', '200', '--json', 'name,color,description', ok=(0, 1))
    try:
        people, _, _ = await run(root, 'api', f'repos/{repo["nameWithOwner"]}/assignees', '--paginate', '--jq', '.[].login')
    except GitError:
        people = ''  # Listing people needs push access; reviewers can still be typed by name.
    default = (repo.get('defaultBranchRef') or {}).get('name') or 'main'
    return {'repo': repo['nameWithOwner'], 'default_branch': default,
            'labels': json.loads(labels_out or '[]'), 'people': [p for p in people.split() if p],
            'bases': [{'branch': default, 'title': 'The default branch', 'number': None}] +
                     [{'branch': p['headRefName'], 'title': p['title'], 'number': p['number']} for p in await open_prs(root)]}


async def review(root, number, event, body):
    """Approve, comment on, or request changes to a pull request, as the signed-in GitHub user."""
    if event not in REVIEW_EVENTS:
        raise GitError('A review approves, comments, or requests changes.')
    if event != 'approve' and not (body or '').strip():
        raise GitError('A comment or a request for changes needs some text.')
    await run(root, 'pr', 'review', str(number), REVIEW_EVENTS[event], *(['--body', body] if (body or '').strip() else []), timeout=90)


async def edit(root, number, add_reviewers=(), remove_reviewers=(), add_labels=(), remove_labels=(), base=None):
    args = []
    for flag, values in (('--add-reviewer', add_reviewers), ('--remove-reviewer', remove_reviewers), ('--add-label', add_labels), ('--remove-label', remove_labels)):
        if values:
            args += [flag, ','.join(values)]
    if base:
        args += ['--base', base]
    if not args:
        raise GitError('Nothing to change.')
    await run(root, 'pr', 'edit', str(number), *args, timeout=90)


async def merge(root, number, method='squash', auto=False, delete_branch=False, disable_auto=False):
    """Merge now, or turn on auto-merge so GitHub merges once checks and reviews pass, or turn it off."""
    if disable_auto:
        await run(root, 'pr', 'merge', str(number), '--disable-auto', timeout=90)
        return
    if method not in MERGE_METHODS:
        raise GitError('Merge by squash, merge commit, or rebase.')
    await run(root, 'pr', 'merge', str(number), MERGE_METHODS[method], *(['--auto'] if auto else []), *(['--delete-branch'] if delete_branch else []), timeout=120)


async def diff(root, number, limit=40000):
    out, _, _ = await run(root, 'pr', 'diff', str(number), timeout=90)
    return out[:limit] + ('\n… (diff truncated)' if len(out) > limit else '')


async def create(root, title, body, base=None, draft=False, reviewers=(), labels=()):
    """Push the branch, then open the pull request. Returns its status."""
    branch = (await git_status(root)).get('branch')
    if not branch or branch == 'HEAD':
        raise GitError('Check out a branch before opening a pull request.')
    await push(root)
    args = ['pr', 'create', '--title', title, '--body', body or '', '--head', branch]
    if base:
        args += ['--base', base]
    if draft:
        args.append('--draft')
    if reviewers:
        args += ['--reviewer', ','.join(reviewers)]
    if labels:
        args += ['--label', ','.join(labels)]
    await run(root, *args, timeout=120)
    return await current(root)


async def review_comments(root, number):
    """Review comments on the diff and top-level review bodies, oldest first, for the agent to address."""
    repo, _, _ = await run(root, 'repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner')
    repo = repo.strip()
    out, _, _ = await run(root, 'api', f'repos/{repo}/pulls/{number}/comments', '--paginate', '--jq',
                          '.[] | {id: .id, path: .path, line: (.line // .original_line), body: .body, author: .user.login, created_at: .created_at, in_reply_to: .in_reply_to_id}')
    comments = [json.loads(line) for line in out.splitlines() if line.strip()]
    out, _, _ = await run(root, 'api', f'repos/{repo}/pulls/{number}/reviews', '--paginate', '--jq',
                          '.[] | select(.body != "") | {id: .id, body: .body, author: .user.login, state: .state, created_at: .submitted_at}')
    reviews = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {'repo': repo, 'comments': comments, 'reviews': reviews}


def address_prompt(pr, feedback):
    lines = [f'Address the review feedback on pull request #{pr["number"]} "{pr["title"]}" ({pr["url"]}). Make the changes in the project; do not push.',
             'Reply with what you changed for each comment, and say plainly which comments you disagree with and why.']
    for review in feedback.get('reviews') or []:
        lines.append(f'\nReview by {review["author"]} ({review["state"]}):\n{review["body"]}')
    for comment in feedback.get('comments') or []:
        where = f'{comment["path"]}:{comment["line"]}' if comment.get('path') else 'general'
        lines.append(f'\nComment by {comment["author"]} on {where}:\n{comment["body"]}')
    return '\n'.join(lines)
