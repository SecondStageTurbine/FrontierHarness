"""Colour themes from the VS Code family installed on this computer, for Settings → Appearance.

Themes come from each editor's extensions folder and its built-in extensions. A theme file may
`include` another, which is merged underneath it, as VS Code does. Only files a listed theme names
are ever read.
"""
import json
import os
import re
from pathlib import Path

EDITORS = {'VS Code': ('.vscode', 'Microsoft VS Code'), 'VS Code Insiders': ('.vscode-insiders', 'Microsoft VS Code Insiders'),
           'Cursor': ('.cursor', 'cursor'), 'Windsurf': ('.windsurf', 'Windsurf'), 'VSCodium': ('.vscode-oss', 'VSCodium')}
STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.S)


def extension_folders():
    home = Path.home()
    local = Path(os.environ.get('LOCALAPPDATA') or home/'AppData'/'Local')
    programs = [Path(os.environ.get('PROGRAMFILES') or r'C:\Program Files'), local/'Programs']
    for editor, (dot, install) in EDITORS.items():
        yield editor, home/dot/'extensions'
        for base in programs:
            yield editor, base/install/'resources'/'app'/'extensions'


def jsonc(text):
    """JSON with comments and trailing commas, as theme files are written."""
    text = STRING_OR_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else '', text)
    return json.loads(re.sub(r',(\s*[}\]])', r'\1', text))


def installed():
    themes, seen = [], set()
    for editor, folder in extension_folders():
        if not folder.is_dir():
            continue
        for manifest in folder.glob('*/package.json'):
            try:
                package = jsonc(manifest.read_text(encoding='utf-8', errors='replace'))
            except (OSError, ValueError):
                continue
            for theme in (package.get('contributes') or {}).get('themes') or []:
                path = (manifest.parent/theme.get('path', '')).resolve()
                label = theme.get('label') or theme.get('id') or path.stem
                if not path.is_file() or (label, str(path)) in seen or label.startswith('%'):
                    continue
                seen.add((label, str(path)))
                themes.append({'label': label, 'editor': editor, 'dark': theme.get('uiTheme') not in ('vs', 'hc-light'),
                               'extension': package.get('displayName') or package.get('name'), 'path': str(path)})
    return sorted(themes, key=lambda t: (t['editor'], t['label'].lower()))


def load(path):
    """A listed theme's colours, with anything it includes merged underneath."""
    if path not in {t['path'] for t in installed()}:
        raise KeyError(path)
    def read(file, depth=0):
        data = jsonc(Path(file).read_text(encoding='utf-8', errors='replace'))
        colors = {}
        if data.get('include') and depth < 4:
            colors.update(read((Path(file).parent/data['include']).resolve(), depth+1).get('colors', {}))
        colors.update(data.get('colors') or {})
        return {'name': data.get('name'), 'type': data.get('type'), 'colors': colors}
    return read(path)
