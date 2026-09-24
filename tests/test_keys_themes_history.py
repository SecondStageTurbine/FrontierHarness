"""First-run history import, and VS Code themes found on this computer."""
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from backend import maintenance, vscode_themes
from backend.app import create_app
from tests.harness import ScriptedAgent
from tests.test_api import setup


def seed(home, root):
    claude = home/'.claude'/'projects'/maintenance.claude_slug(root); claude.mkdir(parents=True)
    (claude/'a.jsonl').write_text('\n'.join(json.dumps(r) for r in [
        {'type': 'user', 'entrypoint': 'cli', 'cwd': str(root), 'timestamp': '2026-05-01T10:00:00Z', 'message': {'role': 'user', 'content': 'Fix the widget.'}},
        {'type': 'assistant', 'timestamp': '2026-05-01T10:00:05Z', 'message': {'role': 'assistant', 'content': 'Fixed.'}}]), encoding='utf-8')
    (claude/'frontier.jsonl').write_text(json.dumps({'type': 'user', 'entrypoint': 'sdk-cli', 'cwd': str(root), 'message': {'role': 'user', 'content': 'x'}}), encoding='utf-8')
    codex = home/'.codex'/'sessions'/'2026'/'05'/'02'; codex.mkdir(parents=True)
    for name, originator in (('r1.jsonl', 'codex_cli_rs'), ('r2.jsonl', 'codex_exec')):
        (codex/name).write_text('\n'.join(json.dumps(r) for r in [
            {'type': 'session_meta', 'payload': {'id': name, 'cwd': str(root), 'originator': originator, 'timestamp': '2026-05-02T09:00:00Z'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Add a test.'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Added.'}]}}]), encoding='utf-8')


def test_first_run_finds_folders_with_past_conversations_and_imports_them_as_projects(tmp_path, monkeypatch):
    home = tmp_path/'home'; home.mkdir()
    root = tmp_path/'widget'; root.mkdir()
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path/'elsewhere'))
    seed(home, root)
    (home/'.claude'/'projects'/'gone').mkdir()
    (home/'.claude'/'projects'/'gone'/'b.jsonl').write_text(json.dumps({'type': 'user', 'cwd': str(tmp_path/'deleted'), 'message': {}}), encoding='utf-8')
    with TestClient(create_app(str(tmp_path/'state'), ScriptedAgent())) as c:
        t = setup(c)
        [folder] = c.get(f'/api/t/{t}/setup/history').json()  # The deleted folder is left out.
        assert folder['root'] == str(root.resolve()) and folder['claude'] == 1 and folder['codex'] == 1 and folder['project_id'] is None
        [done] = c.post(f'/api/t/{t}/setup/history', json={'roots': [folder['root']]}).json()
        assert done['name'] == 'widget' and done['imported'] == 2
        assert c.get(f'/api/t/{t}/setup/history').json()[0]['project_id'] == done['project_id']
        assert c.post(f'/api/t/{t}/setup/history', json={'roots': [folder['root']]}).json()[0]['imported'] == 0  # Once only.
        assert len(c.get(f'/api/t/{t}/projects').json()) == 1


def test_vscode_themes_are_listed_and_loaded_with_their_includes(tmp_path, monkeypatch):
    home = tmp_path/'home'
    ext = home/'.vscode'/'extensions'/'me.nice-theme-1.0.0'
    (ext/'themes').mkdir(parents=True)
    (ext/'package.json').write_text(json.dumps({'displayName': 'Nice Theme', 'contributes': {'themes': [
        {'label': 'Nice Dark', 'uiTheme': 'vs-dark', 'path': './themes/dark.json'},
        {'label': 'Missing', 'uiTheme': 'vs', 'path': './themes/none.json'}]}}), encoding='utf-8')
    (ext/'themes'/'base.json').write_text('{"colors": {"editor.background": "#000000", "button.background": "#123456"}}', encoding='utf-8')
    (ext/'themes'/'dark.json').write_text('{\n  // A comment, as theme files have\n  "name": "Nice Dark", "include": "./base.json",\n'
                                          '  "colors": {"editor.background": "#101010", "textLink.foreground": "#88aaff",},\n}', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: home))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'nowhere')); monkeypatch.setenv('PROGRAMFILES', str(tmp_path/'nowhere'))
    [theme] = vscode_themes.installed()
    assert theme['label'] == 'Nice Dark' and theme['dark'] and theme['editor'] == 'VS Code' and theme['extension'] == 'Nice Theme'
    loaded = vscode_themes.load(theme['path'])
    assert loaded['colors'] == {'editor.background': '#101010', 'button.background': '#123456', 'textLink.foreground': '#88aaff'}
    try:
        vscode_themes.load(str(tmp_path/'secret.json'))
        raise AssertionError('an unlisted file was read')
    except KeyError:
        pass
