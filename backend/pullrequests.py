"""Pull requests through the GitHub CLI the user already signed in to. Frontier never holds a token."""
import asyncio
import json
import shutil

from .gitops import GitError, push, status as git_status
from .localprocess import child_env, child_flags, terminate

FIELDS = 'number,title,url,state,isDraft,mergedAt,reviewDecision,baseRefName,headRefName,statusCheckRollup,additions,deletions,author'


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
    return {'number': pr['number'], 'title': pr['title'], 'url': pr['url'], 'state': 'merged' if pr.get('mergedAt') else pr['state'].lower(),
            'draft': pr.get('isDraft', False), 'review': (pr.get('reviewDecision') or '').lower() or None, 'base': pr.get('baseRefName'), 'head': pr.get('headRefName'),
            'checks': summarise_checks(pr.get('statusCheckRollup')), 'additions': pr.get('additions'), 'deletions': pr.get('deletions'), 'author': (pr.get('author') or {}).get('login')}


async def create(root, title, body, base=None, draft=False):
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
