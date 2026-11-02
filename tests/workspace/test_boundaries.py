"""Keep workspace coordination optional for core and passive artifact readers."""

import ast
import json
from pathlib import Path
import subprocess
import sys

from workspace.state import WorkspaceStore


def test_shared_core_has_no_workspace_imports():
    root = Path(__file__).resolve().parents[2]
    imports = []
    for path in (root / 'core').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = [node.module or '']
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:
                continue
            imports.extend(str(path.relative_to(root)) for name in names
                           if name == 'workspace' or name.startswith('workspace.'))
    assert not imports, imports


def test_saved_state_and_reports_work_without_application_or_provider_imports(tmp_path):
    store = WorkspaceStore(tmp_path)
    run = store.create_run('Saved question', {})
    store.publish(run['id'], 'worker-1', 'Evidence', 'Saved finding')
    store.update_run(run['id'], status='completed')
    before = {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    script = '''
import builtins, json, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('app', 'providers', 'ui', 'legacy'):
        raise AssertionError('Passive reader imported ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from workspace.state import WorkspaceStore
from workspace.report import run_report
store = WorkspaceStore(sys.argv[1])
print(json.dumps({'snapshot': store.snapshot(), 'report': run_report(store)}))
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            text=True, check=True, timeout=5)
    records = json.loads(result.stdout)
    assert records['snapshot']['question'] == 'Saved question'
    assert records['report']['notes'] == 1
    assert {path: path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()} == before
