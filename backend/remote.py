"""Reaching Frontier from another device on the same network or tailnet.

Off by default. Turning it on writes a flag the next start reads to bind every interface
instead of loopback; the desktop window keeps using loopback. The other device signs in with
the workspace user's password over plain HTTP, so use it on a network you trust or a tailnet.
"""
import socket
from pathlib import Path

from .store import read_json, write_json


def flag_path(directory):
    return Path(directory)/'remote.json'


def enabled(directory):
    return bool((read_json(flag_path(directory)) or {}).get('enabled'))


def set_enabled(directory, value):
    write_json(flag_path(directory), {'enabled': bool(value)})


def remote_host(directory):
    return '0.0.0.0' if directory and enabled(directory) else '127.0.0.1'


_CACHE = {'at': 0.0, 'value': None}
VIRTUAL = ('hyper-v', 'virtualbox', 'vmware', 'loopback', 'bluetooth', 'npcap')
VPN = ('wireguard', 'openvpn', 'tap-windows', 'wintun', 'nordlynx', 'protonvpn', 'mullvad', 'vpn')


def classify(address, alias='', description=''):
    """Whether another device can reach this address: 'lan' (the local network), 'tailnet' (Tailscale),
    'vpn' (a VPN tunnel: its address exists only inside the VPN), or None for virtual and link-local ones."""
    name = f'{alias} {description}'.lower()
    first, second = (int(part) for part in address.split('.')[:2])
    if address.startswith(('127.', '169.254.')) or any(word in name for word in VIRTUAL):
        return None
    if 'tailscale' in name or (first == 100 and 64 <= second <= 127):
        return 'tailnet'
    if any(word in name for word in VPN):
        return 'vpn'
    return 'lan'


def windows_interfaces():
    """Each IPv4 address with its adapter's name and description, from Windows itself."""
    import json
    import subprocess
    script = ('Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $a = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue; '
              "[pscustomobject]@{ip=$_.IPAddress; alias=$_.InterfaceAlias; desc=[string]$a.InterfaceDescription; status=[string]$a.Status} } | ConvertTo-Json -Compress")
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True, text=True, timeout=15, creationflags=0x08000000)
    rows = json.loads(done.stdout or '[]')
    return [r for r in (rows if isinstance(rows, list) else [rows]) if r.get('status') in ('Up', '')]


def interfaces():
    """Addresses another device could use, the local network first, then Tailscale. VPN tunnel addresses
    are left out: a phone on the same Wi-Fi cannot reach them. Cached for a minute."""
    import os
    import time
    if _CACHE['value'] is not None and time.monotonic() - _CACHE['at'] < 60:
        return _CACHE['value']
    found = []
    if os.name == 'nt':
        try:
            for row in windows_interfaces():
                kind = classify(row['ip'], row.get('alias') or '', row.get('desc') or '')
                if kind in ('lan', 'tailnet'):
                    label = f'{row["alias"]}, your local network' if kind == 'lan' else 'Tailscale, reachable away from home too'
                    found.append({'address': row['ip'], 'kind': kind, 'label': label})
        except (OSError, ValueError, KeyError):
            found = []
    if not found:
        found = [{'address': a, 'kind': classify(a) or 'lan', 'label': a} for a in guessed_addresses() if classify(a)]
    found.sort(key=lambda i: {'lan': 0, 'tailnet': 1}.get(i['kind'], 2))
    _CACHE.update(at=time.monotonic(), value=found)
    return found


def addresses():
    """The addresses another device can reach this machine on, most useful first."""
    return [i['address'] for i in interfaces()]


def guessed_addresses():
    """Every non-loopback IPv4 address this machine answers on, without adapter names (other systems)."""
    found = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith('127.') and address not in found:
                found.append(address)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('10.255.255.255', 1))
            address = probe.getsockname()[0]
            if not address.startswith('127.') and address not in found:
                found.insert(0, address)
    except OSError:
        pass
    return found


def tray_enabled(directory):
    """Closing the window keeps Frontier in the tray unless this flag says otherwise. Default on."""
    return bool((read_json(Path(directory)/'tray.json') or {}).get('enabled', True))


def set_tray_enabled(directory, value):
    write_json(Path(directory)/'tray.json', {'enabled': bool(value)})
