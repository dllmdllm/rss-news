"""Execute the actual publisher shell with offline command mocks."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _publisher(tmp_path, failure=''):
    source = (ROOT / '.github/workflows/update.yml').read_text(encoding='utf-8')
    section = source.split('- name: Commit & push if changed', 1)[1].split('\n      - name:', 1)[0]
    script = section.split('        run: |\n', 1)[1]
    script = '\n'.join(line[10:] for line in script.splitlines())
    bindir = tmp_path / 'bin'; bindir.mkdir()
    record = tmp_path / 'calls.jsonl'
    selected = str(tmp_path / 'checkout with space')
    mock = '''import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name; args=sys.argv[1:]
with open(os.environ['CALLS'],'a',encoding='utf-8') as f:f.write(json.dumps([name,args])+'\\n')
if name=='cygpath': print(os.environ['SELECTED']);sys.exit(0)
if name=='sleep':sys.exit(0)
if name=='python':sys.exit(1 if os.environ['FAIL']=='source' else 0)
while args and args[0]=='-c':args=args[2:]
cmd=args[0]
fail=os.environ['FAIL']
if cmd=='rev-parse':print('a'*40 if args[-1]=='HEAD' else os.environ['SELECTED'])
if cmd=='diff':sys.exit(128 if fail=='diff' else 1)
if cmd=='reset' and fail=='reset':sys.exit(128)
if cmd=='add' and fail=='stage':sys.exit(128)
if cmd=='push' and fail=='push':sys.exit(1)
'''
    for name in ['git','cygpath','python','sleep']:
        path = bindir / name
        path.write_text('#!' + sys.executable + '\n' + mock, encoding='utf-8'); path.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'], CALLS=str(record), SELECTED=selected, FAIL=failure)
    result = subprocess.run(['bash','-c',script],cwd=tmp_path,env=env,capture_output=True,text=True)
    calls = [json.loads(line) for line in record.read_text(encoding='utf-8').splitlines()]
    return result, calls, selected


def _commands(calls):
    result=[]
    for name,args in calls:
        if name!='git':continue
        rest=args[:]
        while rest and rest[0]=='-c':rest=rest[2:]
        result.append(rest[0])
    return result


@pytest.mark.skipif(os.name=='nt', reason='Publisher uses Git Bash; shell mocks run in hosted POSIX CI')
def test_publisher_trust_scope_identity_and_order(tmp_path):
    result,calls,selected = _publisher(tmp_path)
    assert result.returncode==0,result.stderr
    git_calls=[args for name,args in calls if name=='git']
    assert all(args[:4]==['-c','safe.directory=','-c','safe.directory='+selected] for args in git_calls)
    commands=_commands(calls)
    assert 'config' not in commands and 'push' in commands
    assert all('--force' not in args and '-f' not in args for args in git_calls)
    for args in git_calls:
        if 'commit' in args:
            assert 'user.name=github-actions[bot]' in args
            assert 'user.email=github-actions[bot]@users.noreply.github.com' in args
    guard=next(i for i,(name,args) in enumerate(calls) if name=='python')
    reset=next(i for i,(name,args) in enumerate(calls) if 'reset' in args)
    assert guard<reset


@pytest.mark.skipif(os.name=='nt', reason='Hosted POSIX shell mocks')
@pytest.mark.parametrize('failure',['diff','reset','stage','source'])
def test_publisher_errors_cannot_report_success_or_push(tmp_path,failure):
    result,calls,_=_publisher(tmp_path,failure)
    assert result.returncode!=0
    assert 'push' not in _commands(calls)
    if failure=='source':assert 'reset' not in _commands(calls)


@pytest.mark.skipif(os.name=='nt', reason='Hosted POSIX shell mocks')
def test_publisher_push_retries_are_bounded_without_force(tmp_path):
    result,calls,_=_publisher(tmp_path,'push')
    assert result.returncode!=0
    assert _commands(calls).count('push')==3
