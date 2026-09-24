"""Fuller pull request work through gh: reviews, reviewers, labels, auto-merge, and stacks."""
import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from backend import pullrequests
from backend.app import create_app
from backend.broker import AgentResult
from backend.gitops import GitError
from tests.harness import ScriptedAgent
from tests.test_api import setup, agent as connect_agent

OPEN = [{'number': 10, 'title': 'Base API', 'url': 'u10', 'headRefName': 'api', 'baseRefName': 'main'},
        {'number': 11, 'title': 'Auth on the API', 'url': 'u11', 'headRefName': 'auth', 'baseRefName': 'api'},
        {'number': 12, 'title': 'Login page', 'url': 'u12', 'headRefName': 'login', 'baseRefName': 'auth'}]
VIEW = {'number': 11, 'title': 'Auth on the API', 'url': 'u11', 'state': 'OPEN', 'isDraft': False, 'mergedAt': None, 'reviewDecision': 'REVIEW_REQUIRED',
        'baseRefName': 'api', 'headRefName': 'auth', 'statusCheckRollup': [], 'additions': 3, 'deletions': 1, 'author': {'login': 'me'},
        'labels': [{'name': 'backend'}], 'reviewRequests': [{'login': 'ana'}, {'slug': 'core-team'}], 'latestReviews': [{'author': {'login': 'bo'}, 'state': 'COMMENTED'}],
        'autoMergeRequest': {'mergeMethod': 'SQUASH'}, 'mergeable': 'MERGEABLE', 'mergeStateStatus': 'BLOCKED'}


@pytest.fixture
def gh(monkeypatch):
    calls = []
    async def run(root, *args, timeout=60, ok=(0,)):
        calls.append(list(args))
        if args[:2] == ('pr', 'view'):
            return json.dumps(VIEW), '', 0
        if args[:2] == ('pr', 'list'):
            return json.dumps(OPEN), '', 0
        if args[:2] == ('pr', 'diff'):
            return '+added line\n', '', 0
        if args[:2] == ('repo', 'view'):
            return json.dumps({'nameWithOwner': 'me/app', 'defaultBranchRef': {'name': 'main'}}), '', 0
        if args[:2] == ('label', 'list'):
            return json.dumps([{'name': 'backend', 'color': 'ff0000', 'description': ''}]), '', 0
        if args[0] == 'api':
            return 'ana\nbo\n', '', 0
        return '', '', 0
    monkeypatch.setattr(pullrequests, 'run', run)
    monkeypatch.setattr(pullrequests, 'available', lambda: True)
    return calls


def test_the_pull_request_shows_labels_reviewers_auto_merge_and_where_it_sits_in_a_stack(gh):
    pr = asyncio.run(pullrequests.current('.'))
    assert pr['labels'] == ['backend'] and pr['requested'] == ['ana', 'core-team'] and pr['reviews'] == [{'author': 'bo', 'state': 'commented'}]
    assert pr['auto_merge'] == 'squash' and pr['merge_state'] == 'blocked'
    assert [p['number'] for p in pr['stack']['below']] == [10] and pr['stack']['root_base'] == 'main'
    assert [p['number'] for p in pr['stack']['above']] == [12]
    options = asyncio.run(pullrequests.options('.'))
    assert [b['branch'] for b in options['bases']] == ['main', 'api', 'auth', 'login'] and options['people'] == ['ana', 'bo']


def test_reviews_edits_and_merges_become_the_right_gh_commands(gh):
    asyncio.run(pullrequests.review('.', 11, 'approve', ''))
    asyncio.run(pullrequests.review('.', 11, 'request_changes', 'Handle the empty token.'))
    with pytest.raises(GitError):
        asyncio.run(pullrequests.review('.', 11, 'comment', '  '))
    asyncio.run(pullrequests.edit('.', 11, add_reviewers=['ana', 'bo'], remove_labels=['wip'], add_labels=['backend'], base='main'))
    asyncio.run(pullrequests.merge('.', 11, 'squash', auto=True))
    asyncio.run(pullrequests.merge('.', 11, 'rebase', delete_branch=True))
    asyncio.run(pullrequests.merge('.', 11, disable_auto=True))
    assert gh == [['pr', 'review', '11', '--approve'],
                  ['pr', 'review', '11', '--request-changes', '--body', 'Handle the empty token.'],
                  ['pr', 'edit', '11', '--add-reviewer', 'ana,bo', '--add-label', 'backend', '--remove-label', 'wip', '--base', 'main'],
                  ['pr', 'merge', '11', '--squash', '--auto'],
                  ['pr', 'merge', '11', '--rebase', '--delete-branch'],
                  ['pr', 'merge', '11', '--disable-auto']]


def test_an_agent_drafts_a_review_for_the_user_to_submit(gh, tmp_path):
    prompts = []
    async def respond(config, prompt, mode, root):
        prompts.append((mode, prompt))
        return AgentResult('REQUEST_CHANGES\n\nThe token is never checked.\n\n- auth.py:4 accepts an empty token.', 1, 1)
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent(respond=respond))) as c:
        t = setup(c); connect_agent(c, t)
        root = tmp_path/'project'; root.mkdir()
        p = c.post(f'/api/t/{t}/projects', json={'name': 'P', 'root': str(root)}).json()
        draft = c.post(f'/api/t/{t}/projects/{p["id"]}/pr/review/draft').json()
        assert draft['event'] == 'request_changes' and draft['body'].startswith('The token is never checked.')
        assert prompts[0][0] == 'read' and '+added line' in prompts[0][1]
        c.post(f'/api/t/{t}/projects/{p["id"]}/pr/review', json={'event': 'request_changes', 'body': draft['body']})
        c.post(f'/api/t/{t}/projects/{p["id"]}/pr/merge', json={'method': 'merge', 'auto': True})
        assert c.post(f'/api/t/{t}/projects/{p["id"]}/pr/merge', json={'method': 'octopus'}).status_code == 422
    assert ['pr', 'review', '11', '--request-changes', '--body', draft['body']] in gh and ['pr', 'merge', '11', '--merge', '--auto'] in gh
