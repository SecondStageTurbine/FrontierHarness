"""Reaching Frontier from another device on the same network or tailnet.

Off by default. Turning it on writes a flag the next start reads to bind every interface
instead of loopback; the desktop window keeps using loopback. The other device signs in with
the workspace user's password over plain HTTP, so use it on a network you trust or a tailnet.
"""
import json
import socket
from pathlib import Path


def flag_path(directory):
    return Path(directory)/'remote.json'


def enabled(directory):
    try:
        return bool(json.loads(flag_path(directory).read_text(encoding='utf-8')).get('enabled'))
    except (OSError, ValueError):
        return False


def set_enabled(directory, value):
    flag_path(directory).write_text(json.dumps({'enabled': bool(value)}), encoding='utf-8')


def remote_host(directory):
    return '0.0.0.0' if directory and enabled(directory) else '127.0.0.1'


def addresses():
    """Every non-loopback IPv4 address this machine answers on, tailnet included."""
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
    try:
        return bool(json.loads((Path(directory)/'tray.json').read_text(encoding='utf-8')).get('enabled', True))
    except (OSError, ValueError):
        return True


def set_tray_enabled(directory, value):
    (Path(directory)/'tray.json').write_text(json.dumps({'enabled': bool(value)}), encoding='utf-8')
