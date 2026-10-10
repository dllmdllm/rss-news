"""Execute guardian's actual shell with local mocks: no network or notifications."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _guardian(tmp_path, *, stale, response, initial=None):
    workflow = (ROOT / '.github/workflows/guardian.yml').read_text(encoding="utf-8")
    script = workflow.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0]
    script = '\n'.join(line[10:] for line in script.splitlines())
    bindir = tmp_path / 'bin'
    bindir.mkdir(exist_ok=True)
    # Guardian runs on Ubuntu with GNU date. Mock its clock as well as its
    # network tools so delivery transitions also run deterministically on macOS.
    last_commit = '2026-10-10T10:00:00Z' if stale else '2026-10-10T11:59:00Z'
    (bindir / 'gh').write_text('#!/bin/bash\nif [ "$1" = api ]; then printf "%s\\n" "' + last_commit + '"; fi\n')
    (bindir / 'date').write_text('#!' + sys.executable + '\n'
        'import sys\nfrom datetime import datetime, timezone\n'
        'args = sys.argv[1:]\n'
        'value = args[args.index("-d") + 1] if "-d" in args else "2026-10-10T12:00:00Z"\n'
        'dt = datetime.fromtimestamp(int(value[1:]), timezone.utc) if value.startswith("@") else datetime.fromisoformat(value.replace("Z", "+00:00"))\n'
        'print(int(dt.timestamp()) if args[-1] == "+%s" else dt.strftime(args[-1][1:]))\n')
    (bindir / 'curl').write_text('#!/bin/bash\nprintf \'%s\' \' ' + response + '\'\n')
    for item in bindir.iterdir():
        item.chmod(0o755)
    if initial is not None:
        (tmp_path / 'state.json').write_text(json.dumps(initial))
    env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'], TG_TOKEN='mock', TG_CHAT='mock', GITHUB_REPOSITORY='mock/repo', STALE_MIN='75', WEDGE_MIN='40', REALERT_SEC='21600')
    subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env, check=True, capture_output=True)
    return json.loads((tmp_path / 'state.json').read_text(encoding="utf-8"))


@pytest.mark.skipif(os.name == "nt", reason="Guardian runs on Ubuntu; POSIX shell mocks are exercised by hosted CI")
def test_guardian_failed_alert_retries_until_acknowledged(tmp_path):
    failed = _guardian(tmp_path, stale=True, response='{"ok":false}')
    assert failed == dict(status='stale', delivered_status='ok', pending_delivery=True, last_alert=0)
    success = _guardian(tmp_path, stale=True, response='{"ok":true}')
    assert success['delivered_status'] == 'stale'
    assert success['last_alert'] > 0
    assert not success['pending_delivery']


@pytest.mark.skipif(os.name == "nt", reason="Guardian runs on Ubuntu; POSIX shell mocks are exercised by hosted CI")
def test_guardian_failed_recovery_retries(tmp_path):
    initial = dict(status='stale', delivered_status='stale', last_alert=123)
    failed = _guardian(tmp_path, stale=False, response='{"ok":false}', initial=initial)
    assert failed['status'] == 'ok' and failed['delivered_status'] == 'stale'
    assert failed['pending_delivery']
    recovered = _guardian(tmp_path, stale=False, response='{"ok":true}')
    assert recovered['delivered_status'] == 'ok'


def test_update_failure_state_written_only_after_api_ack():
    # PowerShell is unavailable in the offline Linux fixture environment.
    source = (ROOT / '.github/workflows/update.yml').read_text(encoding="utf-8").split('- name: Notify Telegram on failure', 1)[1]
    assert source.index('$response = Invoke-RestMethod') < source.index('if ($response.ok -ne $true)') < source.index('Set-Content -Path $statusFile -Value "failure"')
    assert source.count('Set-Content -Path $statusFile -Value "failure"') == 1
