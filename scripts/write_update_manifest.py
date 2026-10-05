"""Write the updater feed (latest.json) for a built, signed installer.

Upload it to the GitHub release beside the installer and its .sig; the desktop app reads
releases/latest/download/latest.json on launch and offers the newer version.

Each platform is built on its own machine, so each run adds the platform it finds here to the
feed. Pass the release's current latest.json (`--merge <file>`) to keep the other platforms in it.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = json.loads((root/'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version']
bundle = root/'src-tauri/target/release/bundle'
if sys.platform == 'win32':
    platform, bundles = 'windows-x86_64', sorted((bundle/'nsis').glob(f'Frontier_{version}_x64-setup.exe'))
else:
    platform, bundles = 'linux-x86_64', sorted((bundle/'appimage').glob(f'Frontier_{version}_amd64.AppImage'))
if len(bundles) != 1: raise SystemExit(f'Expected exactly one built installer, found {[b.name for b in bundles]}.')
installer = bundles[0]
signature = installer.with_name(installer.name+'.sig')
if not signature.exists(): raise SystemExit('No .sig beside the installer: build with TAURI_SIGNING_PRIVATE_KEY set, or run `npx tauri signer sign -f <key> <installer>`.')
merge = Path(sys.argv[sys.argv.index('--merge')+1]) if '--merge' in sys.argv else None
previous = json.loads(merge.read_text(encoding='utf-8')) if merge and merge.exists() else {}
# Another version's feed would point this version's users at an older build: start fresh.
if previous.get('version') != version: previous = {}
platforms = previous.get('platforms', {})
notes = root/f'releases/release-notes-{version}.md'
platforms[platform] = {'signature': signature.read_text(encoding='utf-8').strip(),
                       'url': f'https://github.com/SecondStageTurbine/FrontierHarness/releases/download/v{version}/{installer.name}'}
manifest = {'version': version,
            'notes': notes.read_text(encoding='utf-8').strip() if notes.exists() else previous.get('notes', f'Frontier {version}'),
            'pub_date': previous.get('pub_date') or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'platforms': platforms}
out = installer.with_name('latest.json')
out.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(out)
