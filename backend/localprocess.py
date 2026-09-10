"""Shared rules for every local process this application starts.

The environment is an allow-list, not a filtered copy, so no ambient credential reaches a
child. That matters most for subscription agent CLIs: an inherited ANTHROPIC_API_KEY makes
the Claude CLI bill per token instead of using the signed-in subscription.
"""
import asyncio
import os

CHILD_ENV_KEYS = {'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE','HOME','APPDATA','LOCALAPPDATA','LANG'}

def child_env(**extra):
    env={k:v for k,v in os.environ.items() if k.upper() in CHILD_ENV_KEYS}
    env.update(extra)
    return env

def child_flags():
    """No console window on Windows, own process group elsewhere so the tree stays killable."""
    return {'creationflags':0x08000000} if os.name=='nt' else {'start_new_session':True}

async def terminate(proc):
    """Stop a child and its descendants. Shims and agent CLIs both leave grandchildren."""
    if proc.returncode is not None:return
    if os.name=='nt':
        killer=await asyncio.create_subprocess_exec('taskkill','/PID',str(proc.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,creationflags=0x08000000)
        await killer.wait()
    else:
        import signal
        os.killpg(proc.pid,signal.SIGKILL)
    await proc.wait()
