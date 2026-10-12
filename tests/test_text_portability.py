"""Repository text reads must not depend on the Windows service locale."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repository_path_text_reads_choose_encoding_explicitly():
    paths = [ROOT/'build.py']
    for folder in ['src','tools','tests']:
        paths.extend((ROOT/folder).rglob('*.py'))
    missing=[]
    for path in paths:
        tree=ast.parse(path.read_text(encoding='utf-8'))
        for call in ast.walk(tree):
            if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Attribute) or call.func.attr!='read_text':
                continue
            if not call.args and not any(k.arg in ('encoding',None) for k in call.keywords):
                missing.append(f'{path.relative_to(ROOT)}:{call.lineno}')
    assert not missing, 'Default-locale text reads: '+', '.join(missing)
