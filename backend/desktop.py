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
        while os.getppid()==parent:
            time.sleep(2)
        os._exit(0)

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
    import uvicorn
    from backend.app import app
    threading.Thread(target=watch_parent,daemon=True).start()
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('HARNESS_DESKTOP_PORT','8765')),access_log=False)

if __name__=='__main__':
    main()
