"""Bundle only explicit application assets, never local state or credentials."""
import importlib.util
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
# PyInstaller cannot cross-compile, so each platform's backend is built on that platform.
TARGETS={'win32':'x86_64-pc-windows-msvc','linux':'x86_64-unknown-linux-gnu'}
if sys.platform not in TARGETS:
    raise SystemExit('Frontier packages on Windows x64 and Linux x64 only.')
if importlib.util.find_spec('PyInstaller') is None:
    raise SystemExit('Install packaging tools: <.venv python> -m pip install -r requirements-build.txt')
triple=next(line.split(': ',1)[1] for line in subprocess.check_output(['rustc','-vV'],text=True).splitlines() if line.startswith('host: '))
if triple!=TARGETS[sys.platform]:
    raise SystemExit(f'This installer configuration targets {TARGETS[sys.platform]}, not {triple}.')
if not (root/'dist/index.html').is_file():
    raise SystemExit('Build frontend assets first with npm run build.')
command=[sys.executable,'-m','PyInstaller','--noconfirm','--onefile','--console',
    '--name','frontier-backend-'+triple,
    '--distpath',str(root/'src-tauri/binaries'),
    '--workpath',str(root/'build/pyinstaller'),
    '--specpath',str(root/'build'),
    '--paths',str(root),
    '--add-data',str(root/'dist')+':dist',
    '--collect-submodules','uvicorn',
    '--collect-submodules','unittest',
    '--collect-submodules','pytest',
    '--collect-submodules','_pytest',
    '--hidden-import','compileall',
    '--copy-metadata','pytest',
    '--copy-metadata','pygments',
    '--exclude-module','tkinter',
    str(root/'scripts/desktop_entry.py')]
subprocess.run(command,cwd=root,check=True)
