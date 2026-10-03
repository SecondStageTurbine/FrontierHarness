"""Remove the unpacked copies of Frontier's backend that its force-stopped processes leave in the temp folder.

The packaged backend is one PyInstaller file: every start of it (the server, and each permission tool, MCP server,
sandbox launcher or project check a turn runs) unpacks itself into a fresh %TEMP%\\_MEI… folder of about 16 MB and
deletes that folder when it exits normally. Frontier ends those processes with `taskkill /F`, which skips that
cleanup, so the folders piled up: 322 of them (5 GB) on one machine.

A folder is only removed when it is Frontier's and nothing is using it. On Windows a folder whose DLLs a running
process has loaded cannot be renamed, so a folder is first renamed aside and only deleted once that succeeds.
"""
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

PREFIX = '_MEI'
MIN_AGE_SECONDS = 600  # A copy still unpacking is younger than this; one in use is also refused by the rename.
EVERY_SECONDS = 1800   # Turns keep starting helpers, so the sweep repeats rather than running once.


def is_frontier_copy(path):
    """Frontier's unpacked backend carries the web app it serves; other PyInstaller programs' copies do not."""
    assets = Path(path)/'dist'/'assets'
    return (Path(path)/'dist'/'index.html').is_file() and assets.is_dir() and any(assets.glob('DesktopWorkspace-*.js'))


def sweep(temp_dir=None, now=None, own=None):
    """Delete Frontier's stale, unused unpacked copies in the temp folder. Returns how many were removed."""
    temp_dir = Path(temp_dir or tempfile.gettempdir())
    now = time.time() if now is None else now
    own = os.path.normcase(os.path.abspath(own if own is not None else getattr(sys, '_MEIPASS', '') or ''))
    removed = 0
    try:
        candidates = [p for p in temp_dir.iterdir() if p.name.startswith(PREFIX) and p.is_dir()]
    except OSError:
        return 0
    for path in candidates:
        if os.path.normcase(os.path.abspath(path)) == own:
            continue
        try:
            if now - path.stat().st_mtime < MIN_AGE_SECONDS or not is_frontier_copy(path):
                continue
            aside = path.with_name(path.name + '.stale')
            os.rename(path, aside)  # Fails while a running process has the folder's DLLs loaded.
        except OSError:
            continue
        shutil.rmtree(aside, ignore_errors=True)
        removed += 1
    return removed


def start():
    """Sweep now and every half hour, in the background. Only the packaged Windows backend unpacks itself."""
    if not getattr(sys, 'frozen', False) or os.name != 'nt':
        return None

    def loop():
        log = logging.getLogger('frontier.tempsweep')
        while True:
            try:
                count = sweep()
                if count:
                    log.info('removed %d stale unpacked backend folder(s) from the temp folder', count)
            except Exception:  # Housekeeping never takes the backend down.
                log.exception('temp folder sweep failed')
            time.sleep(EVERY_SECONDS)

    thread = threading.Thread(target=loop, name='frontier-tempsweep', daemon=True)
    thread.start()
    return thread
