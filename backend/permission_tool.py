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


def call(method, path, body=None):
    url = os.environ['FRONTIER_URL'].rstrip('/') + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Content-Type': 'application/json', 'X-Frontier-Turn': os.environ.get('FRONTIER_TOKEN', '')})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode() or 'null')


def decide(arguments):
    """Ask Frontier, wait for the user, and answer in the shape Claude's permission tool expects."""
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


def handle(message):
    method, params, ident = message.get('method'), message.get('params') or {}, message.get('id')
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion') or PROTOCOL, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'frontier', 'version': '1'}}
    if method == 'tools/list':
        return {'tools': [{'name': 'approve', 'description': 'Ask the Frontier user whether a tool call may proceed.',
                           'inputSchema': {'type': 'object', 'properties': {'tool_name': {'type': 'string'}, 'input': {'type': 'object'},
                                                                            'tool_use_id': {'type': 'string'}}}}]}
    if method == 'tools/call':
        if params.get('name') != 'approve':
            return {'content': [{'type': 'text', 'text': json.dumps({'behavior': 'deny', 'message': 'Unknown tool.'})}], 'isError': True}
        return {'content': [{'type': 'text', 'text': json.dumps(decide(params.get('arguments') or {}))}]}
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
