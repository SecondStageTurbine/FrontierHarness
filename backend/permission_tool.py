"""The MCP server Claude Code calls when a tool needs permission during a Frontier turn.

Claude starts this as a subprocess named in the turn's `--mcp-config` and asks its one tool,
`approve`, before running anything its posture does not already allow. The tool hands the
request to Frontier over loopback HTTP, where it shows up as a card in the conversation, and
waits for the user's answer. Standard library only: this also runs inside the frozen backend.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROTOCOL = '2024-11-05'
WAIT_SECONDS = 900  # A quarter hour with no answer is a no; the turn continues rather than hangs.
QUESTION_SECONDS = 3600  # A question may take longer to think about than a permission.


def call(method, path, body=None):
    url = os.environ['FRONTIER_URL'].rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Content-Type': 'application/json', 'X-Frontier-Turn': os.environ.get('FRONTIER_TOKEN', '')})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode() or 'null')


def decide(arguments):
    """Ask Frontier, wait for the user, and answer in the shape Claude's permission tool expects."""
    if arguments.get('tool_name') == 'AskUserQuestion':
        return answer_native(arguments.get('input') or {})
    try:
        created = call('POST', '/internal/approvals', {'tool_name': arguments.get('tool_name'), 'input': arguments.get('input') or {},
                                                        'tool_use_id': arguments.get('tool_use_id')})
    except (urllib.error.URLError, OSError, KeyError) as exc:
        return {'behavior': 'deny', 'message': f'Frontier could not be reached to ask for permission: {exc}'}
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            state = call('GET', f'/internal/approvals/{created["id"]}?wait=25')
        except (urllib.error.URLError, OSError):
            time.sleep(2)
            continue
        if state.get('decision') == 'allow':
            return {'behavior': 'allow', 'updatedInput': arguments.get('input') or {}}
        if state.get('decision') == 'deny':
            return {'behavior': 'deny', 'message': state.get('message') or 'The user declined this in Frontier.'}
    return {'behavior': 'deny', 'message': 'Nobody answered the permission request in Frontier within fifteen minutes.'}


UNANSWERED = {'unreachable': 'Frontier could not be reached to ask the user.', 'skipped': 'The user chose not to answer.',
              'timeout': 'Nobody answered within an hour.'}


def wait_for_answer(question, options):
    """Show a question card in the conversation; the user's answer, or why there is none."""
    try:
        created = call('POST', '/internal/approvals', {'tool_name': 'ask_user', 'kind': 'question', 'input': {'question': question[:2000], 'options': options[:6]}})
    except (urllib.error.URLError, OSError, KeyError):
        return None, 'unreachable'
    deadline = time.monotonic() + QUESTION_SECONDS
    while time.monotonic() < deadline:
        try:
            state = call('GET', f'/internal/approvals/{created["id"]}?wait=25')
        except (urllib.error.URLError, OSError):
            time.sleep(2)
            continue
        if state.get('decision') == 'allow':
            return state.get('message') or '(an empty answer)', None
        if state.get('decision') == 'deny':
            return None, 'skipped'
    return None, 'timeout'


def ask(arguments):
    """Frontier's own ask_user tool: the answer as text."""
    question = str(arguments.get('question') or '').strip()
    if not question:
        return 'No question was given.'
    answer, why = wait_for_answer(question, [str(o)[:200] for o in (arguments.get('options') or []) if str(o).strip()])
    if answer is None:
        return UNANSWERED[why] + ' Proceed with your best judgement and say in your reply what you assumed.'
    return 'The user answered: ' + answer


def answer_native(tool_input):
    """Claude's built-in AskUserQuestion reaches the permission tool in print mode. Each of its questions
    becomes a question card, and the answers go back the way Claude reads them: keyed by question text."""
    answers = {}
    for item in (tool_input.get('questions') or [])[:4]:
        question = str(item.get('question') or '').strip()
        if not question:
            continue
        options = [str(o.get('label') if isinstance(o, dict) else o)[:200] for o in item.get('options') or []]
        answer, why = wait_for_answer(question + (' (you may pick more than one)' if item.get('multiSelect') else ''), options)
        if answer is None:
            return {'behavior': 'deny', 'message': UNANSWERED[why] + ' Proceed with your best judgement and say in your reply what you assumed.'}
        answers[question] = answer
    return {'behavior': 'allow', 'updatedInput': {**tool_input, 'answers': answers}}


def board_text(tasks):
    if not tasks:
        return 'The board is empty.'
    return '\n'.join(f'- {t["id"]} [{t["status"]}] {t["title"]}' + (f': {t["notes"][:300]}' if t.get('notes') else '') for t in tasks)


def board_call(name, arguments):
    try:
        if name == 'board_list':
            return board_text(call('GET', '/internal/board'))
        if name == 'board_add':
            task = call('POST', '/internal/board', {k: arguments[k] for k in ('title', 'notes', 'status') if arguments.get(k)})
            return f'Added {task["id"]}: {task["title"]} [{task["status"]}]'
        fields = {k: arguments[k] for k in ('title', 'notes', 'status') if arguments.get(k)}
        task = call('POST', f'/internal/board/{arguments.get("id")}', fields)
        return f'Updated {task["id"]}: {task["title"]} [{task["status"]}]'
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get('detail')
        except ValueError:
            detail = None
        return f'The board refused this: {detail or exc}'
    except (urllib.error.URLError, OSError) as exc:
        return f'Frontier could not be reached: {exc}'


STATUS = {'type': 'string', 'enum': ['todo', 'doing', 'blocked', 'done']}
TOOLS = {
    'approve': {'description': 'Ask the Frontier user whether a tool call may proceed.',
                'inputSchema': {'type': 'object', 'properties': {'tool_name': {'type': 'string'}, 'input': {'type': 'object'}, 'tool_use_id': {'type': 'string'}}}},
    'ask_user': {'description': 'Ask the user a question and wait for their answer. Use it when you need a decision or a fact only '
                                'the user has, and a wrong guess would waste the turn or change something they care about: which of '
                                'two designs, a name, a credential location, whether to delete something. Do not use it for things '
                                'you can find out yourself or reasonably decide. Offer options when the answer is a choice.',
                 'inputSchema': {'type': 'object', 'required': ['question'], 'properties': {
                     'question': {'type': 'string', 'description': 'One clear question, with the context needed to answer it.'},
                     'options': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Up to six suggested answers; the user may also write their own.'}}}},
    'board_list': {'description': "List every task on this project's task board, with ids and status.", 'inputSchema': {'type': 'object', 'properties': {}}},
    'board_add': {'description': "Add a task to this project's task board: real follow-up work the user should see, not a step of this turn.",
                  'inputSchema': {'type': 'object', 'required': ['title'], 'properties': {'title': {'type': 'string'}, 'notes': {'type': 'string'}, 'status': STATUS}}},
    'board_update': {'description': "Change a task on this project's task board: its status when you start, finish or are blocked on it, and notes on what was done.",
                     'inputSchema': {'type': 'object', 'required': ['id'], 'properties': {'id': {'type': 'string'}, 'status': STATUS, 'notes': {'type': 'string'}, 'title': {'type': 'string'}}}},
}


def offered():
    """Codex and OpenCode get the board only; Claude gets every tool."""
    return ['board_list', 'board_add', 'board_update'] if os.environ.get('FRONTIER_TOOLS') == 'board' else list(TOOLS)


def handle(message):
    method, params, ident = message.get('method'), message.get('params') or {}, message.get('id')
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion') or PROTOCOL, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'frontier', 'version': '1'}}
    if method == 'tools/list':
        return {'tools': [{'name': name, **TOOLS[name]} for name in offered()]}
    if method == 'tools/call':
        name, arguments = params.get('name'), params.get('arguments') or {}
        if name not in offered():
            return {'content': [{'type': 'text', 'text': json.dumps({'behavior': 'deny', 'message': 'Unknown tool.'})}], 'isError': True}
        if name == 'approve':
            return {'content': [{'type': 'text', 'text': json.dumps(decide(arguments))}]}
        return {'content': [{'type': 'text', 'text': ask(arguments) if name == 'ask_user' else board_call(name, arguments)}]}
    if method == 'ping':
        return {}
    if ident is None:
        return None  # A notification needs no reply.
    return {'error': {'code': -32601, 'message': f'Unknown method {method}'}}


def serve(stdin=None, stdout=None):
    """Newline-delimited JSON-RPC over stdio, one request at a time, until stdin closes."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = handle(message)
        if message.get('id') is None or result is None:
            continue
        reply = {'jsonrpc': '2.0', 'id': message['id']}
        reply.update({'error': result['error']} if 'error' in result else {'result': result})
        stdout.write(json.dumps(reply) + '\n')
        stdout.flush()


if __name__ == '__main__':
    serve()
