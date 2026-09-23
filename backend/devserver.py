"""The project's own dev server, started and stopped from the Preview panel.

One server per folder. The command is the user's own configuration for the project, so it runs
through the shell with the user's full environment, as it would from their terminal. Output is
kept in a short ring so the panel can show why a server did not come up.
"""
import asyncio
import os
import re
from collections import deque

from .localprocess import child_flags, terminate
from .store import now

PORT_PATTERN = re.compile(r'(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])?:(\d{2,5})\b')
servers = {}


class DevServer:
    def __init__(self, root, command):
        self.root, self.command = str(root), command
        self.proc = None
        self.output = deque(maxlen=200)
        self.port = None
        self.started_at = None
        self.exit_code = None

    def status(self):
        return {'root': self.root, 'command': self.command, 'running': self.proc is not None and self.proc.returncode is None,
                'pid': self.proc.pid if self.proc and self.proc.returncode is None else None, 'port': self.port, 'started_at': self.started_at,
                'exit_code': self.exit_code, 'output': '\n'.join(self.output)}

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        env = {**os.environ, 'FORCE_COLOR': '0', 'NO_COLOR': '1', 'CI': 'true'}
        # The shell, because the user's command is a shell command (npm.cmd, pipes, env vars); Python quotes it for cmd.exe correctly.
        self.proc = await asyncio.create_subprocess_shell(self.command, cwd=self.root, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                          stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **child_flags())
        self.started_at, self.exit_code, self.port = now(), None, None
        self.output.clear()
        asyncio.create_task(self.pump())

    async def pump(self):
        proc = self.proc
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode('utf-8', 'replace').rstrip()
            self.output.append(text[:400])
            if self.port is None:
                found = PORT_PATTERN.search(text)
                if found and 1024 <= int(found.group(1)) <= 65535:
                    self.port = int(found.group(1))
        self.exit_code = await proc.wait()

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            await terminate(self.proc)
        self.port = None


def get(root):
    return servers.get(str(root))


async def start(root, command):
    server = servers.get(str(root))
    if server and server.proc and server.proc.returncode is None:
        if server.command == command:
            return server
        await server.stop()
    server = DevServer(root, command)
    servers[str(root)] = server
    await server.start()
    await asyncio.sleep(0.8)  # Long enough for most servers to print their address.
    return server


async def stop(root):
    server = servers.get(str(root))
    if server:
        await server.stop()
    return server


async def stop_all():
    for server in list(servers.values()):
        await server.stop()


async def kill_port(port):
    """Whatever else holds a local port, for when a stale server blocks the one we want."""
    if os.name != 'nt':
        proc = await asyncio.create_subprocess_exec('/bin/sh', '-c', f'lsof -ti tcp:{int(port)} | xargs -r kill', **child_flags())
        await proc.wait()
        return
    proc = await asyncio.create_subprocess_exec('netstat', '-ano', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **child_flags())
    out, _ = await proc.communicate()
    pids = {line.split()[-1] for line in out.decode('utf-8', 'replace').splitlines() if f':{int(port)} ' in line and 'LISTENING' in line}
    for pid in pids:
        if pid.isdigit() and int(pid) != os.getpid():
            killer = await asyncio.create_subprocess_exec('taskkill', '/PID', pid, '/T', '/F', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **child_flags())
            await killer.wait()
    return sorted(pids)
