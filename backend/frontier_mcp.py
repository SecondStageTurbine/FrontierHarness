"""Frontier as an MCP server: another agent, an editor or a script drives Frontier through tools.

Started as a stdio subprocess by whatever wants to use Frontier (Claude Code, Codex, Cursor, a
script), it reaches the running Frontier backend over loopback with the local access token that
Frontier keeps in its data folder, so only programs run by this Windows user can use it. Standard
library only: it also runs from the frozen backend (`frontier-backend.exe --mcp-server`).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROTOCOL = '2024-11-05'


def data_dir():
    configured = os.environ.get('HARNESS_DATA_DIR')
    if configured:
        return Path(configured)
    base = os.environ.get('LOCALAPPDATA') or str(Path.home()/'.local'/'share')
    return Path(base)/'dev.frontier.harness'


class Frontier:
    def __init__(self):
        folder = data_dir()
        try:
            self.port = (folder/'backend.port').read_text(encoding='utf-8').strip()
            self.token = (folder/'mcp-token').read_text(encoding='utf-8').strip()
        except OSError:
            raise RuntimeError('Frontier is not running, or has not been started since it was updated. Start Frontier, then try again.') from None
        self.tenant = None

    def call(self, method, path, body=None, timeout=60):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/api{path}', method=method,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Content-Type': 'application/json', 'X-Frontier-Token': self.token})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode() or 'null')
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get('detail')
            except Exception:
                detail = None
            raise RuntimeError(detail or f'Frontier answered {exc.code}.') from None
        except urllib.error.URLError:
            raise RuntimeError('Frontier is not running. Start it, then try again.') from None

    def workspace(self, name=None):
        tenants = self.call('GET', '/tenants')
        if not tenants:
            raise RuntimeError('Frontier has no workspace yet.')
        if name:
            match = [t for t in tenants if t['id'] == name or t['name'].lower() == name.lower()]
            if not match:
                raise RuntimeError(f'No workspace named {name}. Workspaces: ' + ', '.join(t['name'] for t in tenants))
            return match[0]['id']
        return tenants[0]['id']

    def project(self, tenant, name):
        projects = self.call('GET', f'/t/{tenant}/projects')
        match = [p for p in projects if p['id'] == name or p['name'].lower() == str(name).lower()]
        if not match:
            raise RuntimeError(f'No project named {name}. Projects: ' + ', '.join(p['name'] for p in projects))
        return match[0]

    def session(self, tenant, project, name):
        sessions = self.call('GET', f'/t/{tenant}/projects/{project["id"]}/sessions')
        match = [s for s in sessions if s['id'] == name or s['name'].lower() == str(name).lower()]
        if not match:
            raise RuntimeError(f'No session named {name} in {project["name"]}.')
        return match[0]


def text(value):
    return {'content': [{'type': 'text', 'text': value if isinstance(value, str) else json.dumps(value, indent=2)}]}


def brief(message):
    return {'role': message['role'], 'agent': message.get('model_name'), 'status': message.get('status'),
            'content': (message.get('content') or '')[:6000], 'files_changed': [c['path'] for c in message.get('changes') or []][:50]}


TOOLS = [
    {'name': 'list_projects', 'description': 'The projects in Frontier, with their folders.',
     'inputSchema': {'type': 'object', 'properties': {'workspace': {'type': 'string'}}}},
    {'name': 'list_sessions', 'description': 'The conversations in one project, newest first.',
     'inputSchema': {'type': 'object', 'properties': {'project': {'type': 'string'}, 'workspace': {'type': 'string'}}, 'required': ['project']}},
    {'name': 'read_session', 'description': 'The last messages of a conversation, with the files each turn changed.',
     'inputSchema': {'type': 'object', 'properties': {'project': {'type': 'string'}, 'session': {'type': 'string'}, 'last': {'type': 'integer'}, 'workspace': {'type': 'string'}}, 'required': ['project', 'session']}},
    {'name': 'send_message', 'description': 'Send a message to a Frontier conversation and, by default, wait for the agent\'s reply. '
                                            'Without a session a new conversation is started. The agent is Adaptive unless named; mode is read, edit or auto.',
     'inputSchema': {'type': 'object', 'properties': {'project': {'type': 'string'}, 'message': {'type': 'string'}, 'session': {'type': 'string'},
                                                      'agent': {'type': 'string'}, 'mode': {'type': 'string', 'enum': ['read', 'edit', 'auto']},
                                                      'wait': {'type': 'boolean'}, 'workspace': {'type': 'string'}}, 'required': ['project', 'message']}},
    {'name': 'project_changes', 'description': 'The git branch and uncommitted changes of a project.',
     'inputSchema': {'type': 'object', 'properties': {'project': {'type': 'string'}, 'workspace': {'type': 'string'}}, 'required': ['project']}},
    {'name': 'catch_up', 'description': 'What happened in a project since a number of hours ago: turns, files, commits, automation runs.',
     'inputSchema': {'type': 'object', 'properties': {'project': {'type': 'string'}, 'hours': {'type': 'number'}, 'workspace': {'type': 'string'}}, 'required': ['project']}},
]


def run_tool(frontier, name, args):
    tenant = frontier.workspace(args.get('workspace'))
    if name == 'list_projects':
        return [{'name': p['name'], 'id': p['id'], 'folder': p['root']} for p in frontier.call('GET', f'/t/{tenant}/projects')]
    project = frontier.project(tenant, args['project'])
    base = f'/t/{tenant}/projects/{project["id"]}'
    if name == 'list_sessions':
        sessions = sorted(frontier.call('GET', f'{base}/sessions'), key=lambda s: s.get('updated_at', ''), reverse=True)
        return [{'name': s['name'], 'id': s['id'], 'updated': s.get('updated_at'), 'messages': len(s.get('messages') or []), 'archived': bool(s.get('archived'))} for s in sessions[:50]]
    if name == 'read_session':
        session = frontier.session(tenant, project, args['session'])
        detail = frontier.call('GET', f'{base}/sessions/{session["id"]}')
        return {'name': detail['name'], 'messages': [brief(m) for m in detail['messages'][-int(args.get('last') or 10):]]}
    if name == 'project_changes':
        return frontier.call('GET', f'{base}/git')
    if name == 'catch_up':
        since = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - float(args.get('hours') or 24)*3600)) + 'Z'
        return frontier.call('POST', f'{base}/catchup', {'since': since, 'summarize': False})
    if name == 'send_message':
        if args.get('session'):
            session = frontier.session(tenant, project, args['session'])
        else:
            session = frontier.call('POST', f'{base}/sessions', {'name': args['message'].splitlines()[0][:80] or 'From another agent'})
        agent = args.get('agent') or 'adaptive'
        if agent.lower() != 'adaptive':
            models = frontier.call('GET', f'/t/{tenant}/models')
            match = [m for m in models if m['id'] == agent or m['name'].lower() == agent.lower() or m['model_name'].lower() == agent.lower()]
            if not match:
                raise RuntimeError(f'No agent named {agent}. Agents: ' + ', '.join(m['name'] for m in models))
            agent = match[0]['id']
        frontier.call('POST', f'{base}/sessions/{session["id"]}/instructions', {'content': args['message'], 'model_id': agent, 'mode': args.get('mode') or 'edit'})
        if args.get('wait') is False:
            return {'session': session['name'], 'session_id': session['id'], 'status': 'started'}
        deadline = time.monotonic() + 8*3600
        while time.monotonic() < deadline:
            time.sleep(3)
            detail = frontier.call('GET', f'{base}/sessions/{session["id"]}')
            last = detail['messages'][-1]
            if last.get('status') != 'running':
                return {'session': detail['name'], 'session_id': session['id'], **brief(last)}
        return {'session': session['name'], 'session_id': session['id'], 'status': 'still running'}
    raise RuntimeError(f'Unknown tool {name}.')


def handle(message, frontier_factory=Frontier):
    method, params, ident = message.get('method'), message.get('params') or {}, message.get('id')
    if method == 'initialize':
        return {'protocolVersion': params.get('protocolVersion') or PROTOCOL, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'frontier', 'version': '1'}}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        try:
            return text(run_tool(frontier_factory(), params.get('name'), params.get('arguments') or {}))
        except (RuntimeError, KeyError) as exc:
            return {**text(f'Frontier: {exc}'), 'isError': True}
    if method == 'ping':
        return {}
    if ident is None:
        return None
    return {'error': {'code': -32601, 'message': f'Unknown method {method}'}}


def serve(stdin=None, stdout=None, frontier_factory=Frontier):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        result = handle(message, frontier_factory)
        if message.get('id') is None or result is None:
            continue
        reply = {'jsonrpc': '2.0', 'id': message['id']}
        reply.update({'error': result['error']} if 'error' in result else {'result': result})
        stdout.write(json.dumps(reply) + '\n')
        stdout.flush()


if __name__ == '__main__':
    serve()
