"""Record the final installer checksum and verify the installed sidecar matches it."""
import hashlib
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
installer=root/'src-tauri/target/release/bundle/nsis/Frontier_0.1.0_x64-setup.exe'
sidecar=root/'src-tauri/binaries/frontier-backend-x86_64-pc-windows-msvc.exe'
installed=Path(os.environ['LOCALAPPDATA'])/'Frontier'
def digest(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()
assert digest(sidecar)==digest(installed/'frontier-backend.exe'),'Installed backend differs from the current build.'
report=json.loads((root/'docs/packaged-verification.json').read_text(encoding='utf-8'))
assert digest(sidecar)==report['executable_sha256'],'The installed backend was not the one tested.'
desktop=(root/'src-tauri/target/release/frontier.exe').read_bytes()
installed_desktop=(installed/'frontier.exe').read_bytes()
# Tauri stamps the installer copy as NSS and restores the loose exe to UNK.
marker=b'__TAURI_BUNDLE_TYPE_VAR_UNK'
offset=desktop.index(marker)
assert installed_desktop[offset:offset+len(marker)]==b'__TAURI_BUNDLE_TYPE_VAR_NSS'
normalized=installed_desktop[:offset]+marker+installed_desktop[offset+len(marker):]
assert normalized==desktop,'Installed desktop differs beyond the expected NSIS bundle marker.'
manifest={'filename':installer.name,'bytes':installer.stat().st_size,'sha256':digest(installer),'architecture':'Windows x64','signed':False,'installed_executables_match_current_build':True,'installed_backend_matches_tested_package':True}
(root/'docs/installer-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print(json.dumps(manifest,indent=2))
