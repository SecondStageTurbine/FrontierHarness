"""Frontier's own sandbox around an agent turn, whatever the agent tool's posture allows.

Two independent parts, set per project:

Files (Windows). The agent tool runs at low integrity, a Windows mandatory-integrity level: a
low-integrity process cannot write to anything labelled higher, which is almost everything the user
owns. Frontier labels the project folder low, plus the tool's own settings folder so it can keep its
session and sign-in, and a temporary folder and package caches under LocalLow. The agent can then
change the project and nothing else, even under Full auto. Reading is not restricted.

Network. Every tool runs with HTTP(S)_PROXY pointing at a filter in Frontier that lets through only the
agent's own service and the hosts the user allowed, and records what it refused. Tools and the
package managers they run honour those variables; a program written to ignore them is not stopped by
this part, which is stated wherever it is offered.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from .store import read_json, write_json

LOW = 'S-1-16-4096'
# The services each tool must reach to work at all.
PROVIDER_HOSTS = {
    'claude_cli': ['anthropic.com', '*.anthropic.com', 'claude.ai', '*.claude.ai', 'claude.com', '*.claude.com'],
    'codex_cli': ['openai.com', '*.openai.com', 'chatgpt.com', '*.chatgpt.com', 'oaistatic.com', '*.oaistatic.com'],
    'opencode_cli': ['opencode.ai', '*.opencode.ai', 'models.dev', '*.models.dev'],
    'gemini_cli': ['*.googleapis.com', 'accounts.google.com', 'oauth2.googleapis.com', 'play.googleapis.com'],
}
# Telemetry the tools send on their own: refused like anything else, but not listed as something the turn needed.
QUIET = ['*.datadoghq.com', 'sentry.io', '*.sentry.io']


def supported():
    return os.name == 'nt'


# ── Files: low integrity ──

def lower_integrity():
    """Drop this process, and so everything it starts, to low integrity. There is no way back up."""
    import ctypes
    import ctypes.wintypes as w
    advapi, kernel = ctypes.WinDLL('advapi32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.SetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    advapi.GetLengthSid.argtypes = [ctypes.c_void_p]
    class Label(ctypes.Structure):
        _fields_ = [('Sid', ctypes.c_void_p), ('Attributes', w.DWORD)]
    token, sid = w.HANDLE(), ctypes.c_void_p()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0080 | 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not advapi.ConvertStringSidToSidW(LOW, ctypes.byref(sid)):
        raise ctypes.WinError(ctypes.get_last_error())
    label = Label(sid, 0x20)  # SE_GROUP_INTEGRITY
    if not advapi.SetTokenInformation(token, 25, ctypes.byref(label), ctypes.sizeof(label) + advapi.GetLengthSid(sid)):  # TokenIntegrityLevel
        raise ctypes.WinError(ctypes.get_last_error())


def peer_integrity(client_port, server_port):
    """The integrity level (as a RID: low 0x1000, medium 0x2000) of the local process on the other end of
    a loopback TCP connection, found through the TCP table and that process's token. None if unknown."""
    import ctypes
    import ctypes.wintypes as w
    import socket
    iphlp, kernel, advapi = (ctypes.WinDLL(n, use_last_error=True) for n in ('iphlpapi', 'kernel32', 'advapi32'))
    size = w.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 5, 0)  # AF_INET, TCP_TABLE_OWNER_PID_ALL
    buffer = ctypes.create_string_buffer(size.value)
    if iphlp.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 5, 0) != 0:
        return None
    count = ctypes.cast(buffer, ctypes.POINTER(w.DWORD))[0]
    class Row(ctypes.Structure):
        _fields_ = [('state', w.DWORD), ('local_addr', w.DWORD), ('local_port', w.DWORD), ('remote_addr', w.DWORD), ('remote_port', w.DWORD), ('pid', w.DWORD)]
    rows = ctypes.cast(ctypes.addressof(buffer) + ctypes.sizeof(w.DWORD), ctypes.POINTER(Row * count)).contents
    pid = next((r.pid for r in rows if socket.ntohs(r.local_port & 0xFFFF) == client_port and socket.ntohs(r.remote_port & 0xFFFF) == server_port), None)
    if not pid:
        return None
    kernel.OpenProcess.restype = w.HANDLE
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)
    process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    token = w.HANDLE()
    try:
        if not advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return None
        needed = w.DWORD(0)
        advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
        label = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 25, label, needed, ctypes.byref(needed)):
            return None
        sid = ctypes.cast(label, ctypes.POINTER(ctypes.c_void_p))[0]
        return advapi.GetSidSubAuthority(ctypes.c_void_p(sid), advapi.GetSidSubAuthorityCount(ctypes.c_void_p(sid))[0] - 1)[0]
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def run_confined(argv):
    """`frontier-backend --sandboxed -- <command…>`: lower integrity, then run the command with this
    process's own standard input and output, and exit with its code."""
    lower_integrity()
    try:
        code = subprocess.run(argv).returncode
    except OSError as exc:
        sys.stderr.write(f'The sandbox could not start {argv[0]}: {exc}\n')
        code = 127
    raise SystemExit(code)


def launcher():
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--sandboxed', '--']
    # From source: the backend package is found by path, without a PYTHONPATH the agent's own commands would inherit.
    code = f'import sys;sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r});from backend.sandbox import run_confined;run_confined(sys.argv[1:])'
    return [sys.executable, '-c', code]


def low_home():
    """Frontier's own folder under LocalLow, where a low-integrity process may always write."""
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData'/'Local')
    return base.parent/'LocalLow'/'Frontier'


def tool_state(provider, login=None):
    """Where each tool keeps its sessions and sign-in: it must still write there inside the sandbox.
    A named login keeps all of it in its own folder."""
    if login:
        return [Path(login)]
    home = Path.home()
    return {'claude_cli': [home/'.claude', home/'.claude.json'], 'codex_cli': [home/'.codex'],
            'opencode_cli': [home/'.local'/'share'/'opencode', home/'.config'/'opencode', home/'.cache'/'opencode'],
            'gemini_cli': [home/'.gemini']}.get(provider, [])


def label(path):
    """Mark a folder (and everything in it) or a file writable from low integrity. Reads nothing, changes no permission."""
    path = Path(path)
    if not path.exists():
        return False
    args = ['icacls', str(path), '/setintegritylevel', '(OI)(CI)low' if path.is_dir() else 'low', '/C', '/Q'] + (['/T'] if path.is_dir() else [])
    done = subprocess.run(args, capture_output=True, text=True, creationflags=0x08000000)
    return done.returncode == 0


def labelled_record(directory):
    return Path(directory)/'sandbox-labelled.json'


def prepare(directory, root, provider, login=None):
    """Label what the confined tool must be able to write, once per path. Returns the environment it needs."""
    record = labelled_record(directory)
    done = set(read_json(record, []) or [])
    scratch = low_home()
    for sub in ('tmp', 'npm-cache', 'pip-cache', 'uv-cache', 'yarn-cache'):
        (scratch/sub).mkdir(parents=True, exist_ok=True)
    for path in [Path(root), *tool_state(provider, login)]:
        key = str(path.resolve()).lower() if path.exists() else None
        if key and key not in done and label(path):
            done.add(key)
    write_json(record, sorted(done))
    return {'TEMP': str(scratch/'tmp'), 'TMP': str(scratch/'tmp'), 'npm_config_cache': str(scratch/'npm-cache'),
            'PIP_CACHE_DIR': str(scratch/'pip-cache'), 'UV_CACHE_DIR': str(scratch/'uv-cache'), 'YARN_CACHE_FOLDER': str(scratch/'yarn-cache')}


# ── Network: a filtering proxy ──

def allowed(host, patterns):
    host = host.lower().rstrip('.')
    if host in ('localhost', '127.0.0.1', '::1'):
        return True
    for pattern in patterns:
        pattern = pattern.lower().strip()
        if not pattern:
            continue
        if pattern.startswith('*.') and (host.endswith(pattern[1:]) or host == pattern[2:]):
            return True
        if host == pattern:
            return True
    return False


class NetworkFilter:
    """An HTTP proxy on loopback for one turn: CONNECT and plain requests to allowed hosts go through;
    anything else gets 403 and is remembered, so the turn can say what it was refused."""
    def __init__(self, patterns):
        self.patterns, self.blocked, self.server = list(patterns), [], None

    async def start(self):
        self.server = await asyncio.start_server(self.handle, '127.0.0.1', 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), 2)
            except (TimeoutError, asyncio.TimeoutError):
                pass

    def refuse(self, host):
        if host not in self.blocked and not allowed(host, QUIET):
            self.blocked.append(host)

    async def handle(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, asyncio.TimeoutError, ConnectionError):
            writer.close()
            return
        first = head.split(b'\r\n', 1)[0].decode('latin-1')
        try:
            method, target, _ = first.split(' ', 2)
        except ValueError:
            writer.close()
            return
        if method.upper() == 'CONNECT':
            host, _, port = target.rpartition(':')
            host = host.strip('[]')
            port = int(port or 443)
            rest = b''
        else:
            from urllib.parse import urlsplit
            parts = urlsplit(target)
            host, port = parts.hostname or '', parts.port or 80
            path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
            rest = head.replace(target.encode('latin-1'), path.encode('latin-1'), 1)
        if not host or not allowed(host, self.patterns):
            self.refuse(host or target)
            writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n'
                         + f'Frontier sandbox: {host} is not on this project\'s network allowlist.\n'.encode())
            await writer.drain()
            writer.close()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
        except (OSError, TimeoutError, asyncio.TimeoutError):
            writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
            await writer.drain()
            writer.close()
            return
        if method.upper() == 'CONNECT':
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
        else:
            upstream_writer.write(rest)
            await upstream_writer.drain()
        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass
            finally:
                try:
                    dst.close()
                except OSError:
                    pass
        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))


def tool_hosts(servers):
    """The workspace's MCP servers keep working in the sandbox: a remote server's host is allowed, and a
    server started with npx may fetch its package (the agent browser also downloads its browser driver)."""
    from urllib.parse import urlsplit
    hosts = []
    for server in servers or []:
        if server.get('url'):
            hosts.append(urlsplit(server['url']).hostname or '')
        if 'npx' in ' '.join([server.get('command') or '', *(server.get('args') or [])]):
            hosts += ['registry.npmjs.org', 'cdn.playwright.dev', 'playwright.download.prss.microsoft.com']
    return [h for h in dict.fromkeys(hosts) if h]


def proxy_env(port):
    url = f'http://127.0.0.1:{port}'
    no_proxy = 'localhost,127.0.0.1,::1'
    return {'HTTPS_PROXY': url, 'HTTP_PROXY': url, 'https_proxy': url, 'http_proxy': url, 'ALL_PROXY': url,
            'NO_PROXY': no_proxy, 'no_proxy': no_proxy, 'npm_config_proxy': url, 'npm_config_https_proxy': url}


def policy(project):
    """The project's sandbox settings, with defaults: off."""
    found = (project or {}).get('sandbox') or {}
    return {'files': bool(found.get('files')) and supported(), 'network': found.get('network') if found.get('network') in ('open', 'agent', 'allowlist') else 'open',
            'allow': [h for h in found.get('allow') or [] if isinstance(h, str)][:100]}
