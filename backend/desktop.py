"""Internal desktop service entrypoint; lifecycle belongs to the native application."""
import os
import threading
import time
import subprocess
import sys

def watch_parent():
    parent = int(os.environ.get('HARNESS_PARENT_PID', '0'))
    if not parent:
        return
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x00100000,False,parent)
        if handle:
            kernel.WaitForSingleObject(ctypes.c_void_p(handle),0xFFFFFFFF)
            # Stop descendant project checks if the native host disappears abruptly.
            subprocess.run(['taskkill','/PID',str(os.getpid()),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x08000000)
            os._exit(0)
    else:
        # Not getppid(): the frozen backend's parent is PyInstaller's bootloader, not Frontier.
        # ponytail: a recycled pid keeps this alive; pidfd_open if that ever shows up.
        while True:
            try:
                os.kill(parent,0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass
            time.sleep(2)

def log_to_data_dir(directory):
    """Frontier's own warnings (a failed agent turn's stderr tail, for one) go to frontier.log in the data
    folder. Without a handler they fell through to stderr, which the desktop host may never flush to
    backend.log, so a failed turn left nothing to diagnose it with."""
    if not directory:
        return
    import logging
    from logging.handlers import RotatingFileHandler
    from pathlib import Path
    try:
        handler = RotatingFileHandler(Path(directory)/'frontier.log', maxBytes=1_000_000, backupCount=2, encoding='utf-8')
    except OSError:
        return
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logger = logging.getLogger('frontier')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

def main():
    # The bundled Python interpreter also runs the supported project checks.
    # Do this before importing server modules or opening the private database.
    if len(sys.argv)>2 and sys.argv[1]=='--project-check':
        module=sys.argv[2]
        if module not in ('unittest','pytest','compileall'):
            raise SystemExit('Unsupported project check module.')
        import runpy
        sys.path.insert(0,os.getcwd())
        sys.argv=[module,*sys.argv[3:]]
        runpy.run_module(module,run_name='__main__',alter_sys=True)
        return
    if len(sys.argv)>1 and sys.argv[1]=='--mcp-server':
        from backend.frontier_mcp import serve as serve_mcp
        serve_mcp()
        return
    if len(sys.argv)>2 and sys.argv[1]=='--sandboxed':
        from backend.sandbox import run_confined
        run_confined(sys.argv[3:] if sys.argv[2]=='--' else sys.argv[2:])
        return
    if len(sys.argv)>1 and sys.argv[1]=='--permission-tool':
        from backend.permission_tool import serve
        serve()
        return
    log_to_data_dir(os.environ.get('HARNESS_DATA_DIR'))
    import uvicorn
    from backend.app import app
    from backend.remote import remote_host
    threading.Thread(target=watch_parent,daemon=True).start()
    from backend import tempsweep
    tempsweep.start()
    uvicorn.run(app,host=remote_host(os.environ.get('HARNESS_DATA_DIR')),port=int(os.environ.get('HARNESS_DESKTOP_PORT','8765')),access_log=False)

if __name__=='__main__':
    main()
