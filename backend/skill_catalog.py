"""Skills Frontier can install into a project with one click.

A skill is a SKILL.md an agent finds in the project and follows when its description fits the task.
Each one is written to `.claude/skills/<name>/` (Claude Code, and OpenCode) and `.agents/skills/<name>/`
(Codex), so every connected agent sees it. Frontier only ever removes a copy it wrote and nobody edited.
"""
import shutil
from pathlib import Path

FOLDERS = ('.claude/skills', '.agents/skills')

CATALOG = [
    {'name': 'write-tests', 'title': 'Write tests', 'category': 'Quality',
     'description': 'Add or extend automated tests for new or changed behaviour, then run them and fix what fails. Use after implementing a feature or fixing a bug, or when asked for tests.',
     'body': """# Write tests

1. Find the project's test framework and conventions: look at existing tests, the test command in package.json, pyproject.toml, Makefile or CI config. Follow them; do not add a new framework.
2. List the behaviours to cover: the normal case, edge cases (empty, large, invalid input), and the failure paths. For a bug fix, first write a test that reproduces the bug and fails.
3. Write the smallest tests that would fail if the behaviour broke. Test through public interfaces; avoid asserting on private details or exact log text.
4. Run the tests. If one fails, decide whether the test or the code is wrong, and fix that. Never weaken an assertion just to make it pass.
5. Report which behaviours are now covered, the exact command that runs them, and its result.
"""},
    {'name': 'review-changes', 'title': 'Review changes', 'category': 'Quality',
     'description': 'Review the uncommitted or branch changes for bugs, security problems and missed cases, ranked by severity. Use when asked to review, check or audit changes before committing or merging.',
     'body': """# Review changes

1. Read the diff: `git diff` for uncommitted work, `git diff main...HEAD` (or the default branch) for a branch. Read enough surrounding code to understand each change.
2. Look for, in this order: incorrect behaviour and crashes, data loss, security problems (injection, secrets, missing authorisation), race conditions, unhandled errors, and missing tests for the new behaviour.
3. For each finding, name the file and line, describe the concrete input or state that goes wrong, and suggest the fix. Drop anything you cannot tie to a real failure.
4. Rank findings most severe first. Say plainly when you found nothing serious.
5. Do not change files unless asked to fix what you found.
"""},
    {'name': 'debug-systematically', 'title': 'Debug systematically', 'category': 'Fixing',
     'description': 'Find the root cause of a bug by reproducing it, narrowing it down and proving the fix. Use for errors, crashes, failing tests, or behaviour that differs from what is expected.',
     'body': """# Debug systematically

1. Reproduce the problem first, with the smallest command or input that shows it. Record the exact error.
2. Form one hypothesis at a time and test it: add a targeted log, run a single test, or bisect. Read the code on the failing path end to end rather than guessing.
3. Find the root cause, not the symptom. Check every caller of the function you intend to change; fix it where all of them route through.
4. Fix it, then prove it: the reproduction now passes, and the surrounding tests still pass. Add a regression test when the project has tests.
5. Remove temporary logging. Report the cause, the fix, and how it was verified.
"""},
    {'name': 'check-in-browser', 'title': 'Check in a browser', 'category': 'Frontend',
     'description': 'After changing anything a user sees, open it in a real browser, click through it, read the console and take a screenshot. Use for UI, styling and front-end changes.',
     'body': """# Check in a browser

1. Make sure the app is running: use the project's dev server if one is up, otherwise start it with its usual command and wait until it serves.
2. Open the changed page with the browser tools you have (for example the frontier-browser or Playwright tools). Navigate the way a user would.
3. Exercise the change: click it, fill the form, resize to a phone width if layout changed.
4. Read the console. Any error or warning your change introduced is a bug to fix before you finish.
5. Take a screenshot and describe what you actually saw, not what you expected. If something looks wrong, fix it and look again.
"""},
    {'name': 'security-audit', 'title': 'Security audit', 'category': 'Quality',
     'description': 'Audit the project for security problems: secrets in the repository, injection, authentication and authorisation gaps, unsafe dependencies. Use when asked for a security review or before a release.',
     'body': """# Security audit

1. Secrets: search for API keys, tokens, passwords and private keys committed to the repository or its history. Check that .env files are ignored.
2. Input handling: find every place outside input reaches a query, a shell command, a file path, HTML or a deserialiser, and check it is validated or escaped.
3. Access: check that every endpoint or action that changes or reads private data checks who is asking.
4. Dependencies: run the ecosystem's audit (npm audit, pip-audit, cargo audit) if available and note serious findings.
5. Report each problem with its location, how it could be exploited in one sentence, and the fix, most severe first. Do not print any secret you find; name where it is.
"""},
    {'name': 'refactor-safely', 'title': 'Refactor safely', 'category': 'Fixing',
     'description': 'Restructure code without changing what it does, keeping the tests green at every step. Use when asked to clean up, simplify, rename, split or reorganise code.',
     'body': """# Refactor safely

1. Before touching anything, run the tests and note the result. If the area has no tests, add a few that pin down its current behaviour.
2. Find every caller of what you are changing, so nothing is missed.
3. Change in small steps: one rename, one extraction, one move at a time, running the tests after each.
4. Keep behaviour identical: same inputs, same outputs, same errors. Anything that changes behaviour is a separate change you mention, not part of the refactor.
5. Prefer deleting code to adding it. Report what moved where and confirm the tests pass as they did before.
"""},
    {'name': 'write-docs', 'title': 'Update the docs', 'category': 'Docs',
     'description': 'Update the README and other documentation to match what the code now does. Use after adding features, changing commands or configuration, or when asked for documentation.',
     'body': """# Update the docs

1. Find the documentation that covers what changed: README, docs folder, command help text, configuration examples, comments on public APIs.
2. Check each statement against the code as it is now. Correct what is wrong, add what is missing, remove what no longer exists.
3. Write for someone who has not seen the code: what it does, how to install or run it, one working example. Keep commands exact and copyable.
4. Do not document plans or internals nobody needs.
5. Report which files you changed and what they now say differently.
"""},
    {'name': 'commit-message', 'title': 'Commit message', 'category': 'Git',
     'description': 'Write a clear commit message from the staged changes. Use when asked to commit, or to describe or summarise changes for a commit.',
     'body': """# Commit message

1. Read the staged changes with `git diff --cached` (or all changes if nothing is staged, and say so).
2. First line: at most about 70 characters, saying what the change does for its user, in the imperative ("Add", "Fix", "Stop").
3. Body, after a blank line: why the change was needed and anything a reviewer should know, wrapped at about 72 characters. Skip it for trivial changes.
4. Follow the project's existing style if its history shows one (for example Conventional Commits).
5. Only run `git commit` if you were asked to commit; otherwise give the message.
"""},
    {'name': 'release-notes', 'title': 'Release notes', 'category': 'Docs',
     'description': 'Write release notes from the changes since the last release tag. Use when preparing a release or asked what changed since a version.',
     'body': """# Release notes

1. Find the last release: `git describe --tags --abbrev=0`, or ask if there are no tags.
2. Read `git log <tag>..HEAD` and, where a message is unclear, the change itself.
3. Group what a user would notice: new features, fixes, changes in behaviour, anything they must do to upgrade. Leave out internal refactors unless they affect users.
4. One line per item, in plain language, naming the feature rather than the file.
5. Put breaking changes first and say exactly what to change.
"""},
    {'name': 'explain-architecture', 'title': 'Explain the architecture', 'category': 'Exploring',
     'description': 'Read the project and explain its architecture: entry points, main components, how data flows, and where to make common changes. Use when new to a codebase or asked how it fits together.',
     'body': """# Explain the architecture

1. Start from the entry points: the main executable, server start-up, the front-end root, the CLI. Note how the project is built and run.
2. Identify the main components and what each is responsible for, with the folder or file that holds it.
3. Trace one real request or user action end to end through those components.
4. Note the conventions a newcomer must follow: configuration, tests, error handling, where state lives.
5. Finish with "where to change what": for the three or four most likely kinds of change, which files to open first. Cite paths.
"""},
]

BY_NAME = {s['name']: s for s in CATALOG}


def text_of(skill):
    return f'---\nname: {skill["name"]}\ndescription: {skill["description"]}\n---\n\n{skill["body"]}'


def state(root, skill):
    """installed: every copy is there; edited: a copy differs from what Frontier wrote."""
    copies = [Path(root)/folder/skill['name']/'SKILL.md' for folder in FOLDERS]
    present = [c for c in copies if c.is_file()]
    edited = any(c.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n') != text_of(skill) for c in present)
    return {'installed': len(present) == len(copies), 'partly': 0 < len(present) < len(copies), 'edited': edited}


def listing(root):
    return [{k: s[k] for k in ('name', 'title', 'category', 'description')} | state(root, s) for s in CATALOG]


def install(root, name):
    skill = BY_NAME.get(name)
    if not skill:
        raise KeyError(name)
    if state(root, skill)['edited']:
        raise ValueError(f'{name} is already in this project and has been edited there; Frontier leaves it as it is.')
    for folder in FOLDERS:
        target = Path(root)/folder/name/'SKILL.md'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text_of(skill), encoding='utf-8', newline='\n')
    return state(root, skill)


def remove(root, name):
    skill = BY_NAME.get(name)
    if not skill:
        raise KeyError(name)
    if state(root, skill)['edited']:
        raise ValueError(f'{name} has been edited in this project, so Frontier will not delete it. Remove it by hand if you mean to.')
    for folder in FOLDERS:
        target = Path(root)/folder/name
        if (target/'SKILL.md').is_file() and len(list(target.iterdir())) == 1:
            shutil.rmtree(target)
    return state(root, skill)
