"""Write the updater feed (latest.json) for a built, signed installer.

Upload it to the GitHub release beside the installer and its .sig; the desktop app reads
releases/latest/download/latest.json on launch and offers the newer version.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = json.loads((root/'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version']
bundles = sorted((root/'src-tauri/target/release/bundle/nsis').glob('Frontier_*_x64-setup.exe'))
if len(bundles) != 1: raise SystemExit(f'Expected exactly one built installer, found {[b.name for b in bundles]}.')
installer = bundles[0]
signature = installer.with_name(installer.name+'.sig')
if not signature.exists(): raise SystemExit('No .sig beside the installer: build with TAURI_SIGNING_PRIVATE_KEY set, or run `npx tauri signer sign -f <key> <installer>`.')
notes = root/f'releases/release-notes-{version}.md'
manifest = {'version': version,
            'notes': notes.read_text(encoding='utf-8').strip() if notes.exists() else f'Frontier {version}',
            'pub_date': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'platforms': {'windows-x86_64': {'signature': signature.read_text(encoding='utf-8').strip(),
                                             'url': f'https://github.com/SecondStageTurbine/FrontierHarness/releases/download/v{version}/{installer.name}'}}}
out = installer.with_name('latest.json')
out.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(out)
