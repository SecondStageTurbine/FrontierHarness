"""Git as Frontier uses it: status and staging for the Changes panel, a worktree per session,
and a checkpoint of the working tree before and after every turn so a turn can be reverted.

Nothing here is required of a project. A folder that is not a repository gets `{'repo': False}`
from `status` and no checkpoints; every other feature keeps working without it.
"""
import asyncio
import os
import re
import functools
import shutil
import tempfile
from pathlib import Path

from .localprocess import child_env, child_flags, terminate

CHECKPOINT_IDENTITY = dict(GIT_AUTHOR_NAME='Frontier', GIT_AUTHOR_EMAIL='frontier@localhost',
                           GIT_COMMITTER_NAME='Frontier', GIT_COMMITTER_EMAIL='frontier@localhost')
STATUS_WORDS = {'M': 'modified', 'A': 'added', 'D': 'removed', 'R': 'renamed', 'C': 'copied', 'T': 'modified', 'U': 'conflict', '?': 'untracked'}


class GitError(ValueError):
    pass


@functools.cache
def available():
    return shutil.which('git') is not None


async def run(root, *args, env=None, timeout=60, ok=(0,)):
    """One git command in `root`; stdout on success, GitError with git's own words otherwise."""
    if not available():
        raise GitError('Git is not installed, or not on the PATH Frontier was started with.')
    proc = await asyncio.create_subprocess_exec('git', '-c', 'core.quotepath=false', '-c', 'color.ui=never', *args, cwd=str(root),
                                                env=child_env(**(env or {}), GIT_TERMINAL_PROMPT='0'), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
    except TimeoutError:
        await terminate(proc)
        raise GitError(f'git {args[0]} took longer than {timeout} seconds and was stopped.') from None
    if proc.returncode not in ok:
        raise GitError((err.decode('utf-8', 'replace').strip() or out.decode('utf-8', 'replace').strip() or f'git {args[0]} failed.')[:2000])
    return out.decode('utf-8', 'replace')


async def toplevel(root):
    """The repository this folder belongs to, or None. A worktree answers with its own root."""
    if not available() or not Path(root).is_dir():
        return None
    try:
        return (await run(root, 'rev-parse', '--show-toplevel')).strip()
    except GitError:
        return None


async def has_head(root):
    try:
        await run(root, 'rev-parse', '--verify', '-q', 'HEAD')
        return True
    except GitError:
        return False


async def status(root):
    """Branch, upstream distance, and every changed path with its index and worktree state."""
    top = await toplevel(root)
    if not top:
        return {'repo': False, 'available': available()}
    raw, head = await asyncio.gather(run(root, 'status', '--porcelain=v1', '-z', '--branch', '--untracked-files=all'), has_head(root))
    tokens = raw.split('\0')
    header = tokens.pop(0) if tokens and tokens[0].startswith('## ') else '## HEAD (no branch)'
    # A repository with no commits reports '## No commits yet on main'; the branch is the last word there.
    branch = re.sub(r'\.\.\..*$', '', header[3:].replace('No commits yet on ', '')).split(' ')[0]
    ahead = int(m.group(1)) if (m := re.search(r'ahead (\d+)', header)) else 0
    behind = int(m.group(1)) if (m := re.search(r'behind (\d+)', header)) else 0
    upstream = m.group(1) if (m := re.search(r'\.\.\.(\S+)', header)) else None
    entries = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if len(token) < 4:
            continue
        x, y, path = token[0], token[1], token[3:]
        if x in 'RC':
            i += 1  # The original name follows as its own token; the panel shows where it is now.
        entries.append({'path': path, 'staged': x not in ' ?', 'unstaged': y != ' ' or x == '?',
                        'status': STATUS_WORDS.get(y if y not in ' ' else x, 'modified') if x != '?' else 'untracked'})
    return {'repo': True, 'available': True, 'root': top, 'branch': branch, 'upstream': upstream,
            'ahead': ahead, 'behind': behind, 'entries': entries, 'has_head': head}


async def diff(root, path, staged=False):
    """The unified diff for one path; a new file diffs against nothing."""
    if staged:
        return await run(root, 'diff', '--cached', '--', path)
    tracked = (await run(root, 'ls-files', '--error-unmatch', '--', path, ok=(0, 1))).strip()
    if tracked:
        return await run(root, 'diff', '--', path)
    return await run(root, 'diff', '--no-index', '--', '/dev/null', path, ok=(0, 1))


async def stage(root, paths, staged=True):
    if not paths:
        return
    if staged:
        await run(root, 'add', '-A', '--', *paths)
    elif await has_head(root):
        await run(root, 'restore', '--staged', '--', *paths)
    else:
        await run(root, 'rm', '--cached', '-r', '-q', '--', *paths)


async def commit(root, message):
    if not message.strip():
        raise GitError('Write a commit message first.')
    return (await run(root, 'commit', '-q', '-m', message.strip())).strip()


async def push(root):
    """Push the current branch, setting its upstream the first time."""
    current = await status(root)
    args = ['push'] if current.get('upstream') else ['push', '-u', 'origin', 'HEAD']
    # Git writes progress to stderr even on success, so success is the exit code, not silence.
    proc = await asyncio.create_subprocess_exec('git', *args, cwd=str(root), env=child_env(GIT_TERMINAL_PROMPT='0'),
                                                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **child_flags())
    try:
        async with asyncio.timeout(180):
            out, _ = await proc.communicate()
    except TimeoutError:
        await terminate(proc)
        raise GitError('git push took longer than three minutes and was stopped.') from None
    text = out.decode('utf-8', 'replace').strip()
    if proc.returncode != 0:
        raise GitError(text[:2000] or 'git push failed.')
    return text


async def staged_diff(root, limit=24000):
    text = await run(root, 'diff', '--cached', '--stat')
    body = await run(root, 'diff', '--cached')
    combined = text + '\n' + body
    return combined[:limit] + ('\n… (diff truncated)' if len(combined) > limit else '')


async def checkpoint(root, label):
    """A commit object of the whole working tree (untracked included, ignored excluded), kept
    under refs/frontier/checkpoints so it never touches the user's branch or index.

    ponytail: refs accumulate one per turn boundary; add pruning on session archive if a
    repository's ref list ever grows enough to matter.
    """
    if not await toplevel(root):
        return None
    fd, index = tempfile.mkstemp(prefix='frontier-index-')
    os.close(fd)
    os.unlink(index)  # git wants to create it; an empty existing file is a corrupt index.
    try:
        env = {'GIT_INDEX_FILE': index, **CHECKPOINT_IDENTITY}
        await run(root, 'add', '-A', '--', '.', env=env)
        tree = (await run(root, 'write-tree', env=env)).strip()
        parent = ['-p', 'HEAD'] if await has_head(root) else []
        sha = (await run(root, 'commit-tree', tree, *parent, '-m', label, env=env)).strip()
        await run(root, 'update-ref', f'refs/frontier/checkpoints/{sha[:12]}', sha)
        return sha
    finally:
        if os.path.exists(index):
            os.unlink(index)


async def changed_between(root, before, after):
    """Paths whose content differs between two checkpoints, each with A, M, or D."""
    raw = await run(root, 'diff', '--name-status', '--no-renames', '-z', before, after)
    tokens = [t for t in raw.split('\0') if t]
    return [(tokens[i][0], tokens[i+1]) for i in range(0, len(tokens)-1, 2)]


async def restore(root, before, after):
    """Put every path the turn touched back to how it was at `before`, and say which."""
    changed = await changed_between(root, before, after)
    keep = [path for code, path in changed if code in 'MD']
    drop = [path for code, path in changed if code == 'A']
    if keep:
        await run(root, 'checkout', before, '--', *keep)
        # checkout stages what it restores; the panel should show the file, not a staged revert.
        await stage(root, keep, staged=False)
    for path in drop:
        target = Path(root)/path
        if target.is_file():
            target.unlink()
    return [path for _, path in changed]


def branch_name(text, suffix):
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')[:40] or 'session'
    return f'frontier/{slug}-{suffix}'


async def worktree_add(root, path, branch, copy=()):
    """A new worktree on a new branch from the current HEAD, or on the branch if it already exists.

    `copy` names ignored files, such as .env, that the user chose to carry into every worktree so
    a fresh branch runs at once. Only files inside the project are copied, never followed links.
    """
    if not await has_head(root):
        raise GitError('Make a first commit in this repository before working in a branch.')
    exists = (await run(root, 'branch', '--list', branch)).strip() != ''
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    args = ['worktree', 'add', str(path), branch] if exists else ['worktree', 'add', '-b', branch, str(path)]
    await run(root, *args)
    return hydrate(root, path, copy)


def hydrate(root, path, names):
    """Copy the named ignored files from the project into a worktree; returns what was copied."""
    copied = []
    for name in names or []:
        relative = str(name).replace(chr(92), '/').strip('/')
        if not relative or '..' in relative.split('/'):
            continue
        source, target = Path(root)/relative, Path(path)/relative
        if source.is_symlink() or not source.exists() or Path(root).resolve() not in source.resolve().parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False)
        else:
            shutil.copy2(source, target)
        copied.append(relative)
    return copied


async def apply_between(root, before, after):
    """Bring the changes between two checkpoints into another working tree of the same repository.

    Used by team mode to fold a worker's edits back into the lead's folder without commits or a
    clean tree: the diff is applied three-way, so unrelated local changes stay put.
    """
    patch = await run(root, 'diff', '--binary', before, after)
    if not patch.strip():
        return False
    proc = await asyncio.create_subprocess_exec('git', 'apply', '--3way', '--whitespace=nowarn', cwd=str(root), env=child_env(GIT_TERMINAL_PROMPT='0'),
                                                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **child_flags())
    out, err = await proc.communicate(patch.encode('utf-8', 'surrogateescape'))
    if proc.returncode != 0:
        raise GitError((err.decode('utf-8', 'replace').strip() or out.decode('utf-8', 'replace').strip() or 'git apply failed')[:1500])
    # A three-way apply stages what it lands; the user should see plain working-tree changes and commit deliberately.
    await stage(root, [path for _, path in await changed_between(root, before, after)], staged=False)
    return True


async def rollback(root, before):
    """Put the whole working tree back to a checkpoint: what a turn and everything after it changed."""
    current = await checkpoint(root, 'Frontier: before rewind')
    if not current:
        raise GitError('This folder is not a repository.')
    return await restore(root, before, current)


async def worktree_remove(root, path):
    """Drop the worktree folder; the branch and its commits stay in the repository."""
    await run(root, 'worktree', 'remove', '--force', str(path), ok=(0, 128))
    await run(root, 'worktree', 'prune')
    if Path(path).is_dir():
        shutil.rmtree(path, ignore_errors=True)
