import io
import json
from pathlib import Path
import stat
import subprocess
import zipfile

import pytest

from tools.site_snapshot import MANIFEST, _files, check_source, restore, snapshot, verify


def _repo(tmp_path):
    repo = tmp_path / 'repo'
    data = repo / 'docs' / 'data'
    (data / 'content').mkdir(parents=True)
    article = {'id':'a1','title':'article','summary':'summary','score':5,
               'sentiment':'neutral','tags':[],'date':'2026-10-12T00:00:00+00:00'}
    payload = {'articles':[article]}
    for name in ['articles.json','articles_index.json']:
        (data / name).write_text(json.dumps(payload), encoding='utf-8')
    (data / 'content' / 'a1.json').write_text(json.dumps({'version':1,'content':'<p>original public body</p>'}), encoding='utf-8')
    (repo / 'docs' / 'index.html').write_text('<h1>prior site</h1>', encoding='utf-8')
    (repo / '.env').write_text('PRIVATE_SENTINEL=never_archive', encoding='utf-8')
    subprocess.run(['git','init',str(repo)], check=True, capture_output=True)
    subprocess.run(['git','-C',str(repo),'add','.'], check=True, capture_output=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.com',
                    'commit','-m','fixture'], check=True, capture_output=True)
    return repo


def test_recovery_captures_exact_commit_not_untracked_or_dirty_bytes(tmp_path):
    repo = _repo(tmp_path)
    (repo / 'docs' / 'index.html').write_text('uncommitted different page', encoding='utf-8')
    (repo / 'docs' / 'not-tracked.json').write_text('not published', encoding='utf-8')
    out = tmp_path / 'prior.zip'
    manifest = snapshot(repo, out)
    assert manifest['validation'] == 'passed'
    metadata, files = verify(out)
    assert metadata == manifest and files['docs/index.html'] == b'<h1>prior site</h1>'
    assert '.env' not in files and 'docs/not-tracked.json' not in files
    restored = tmp_path / 'review'
    restore(out, restored)
    assert (restored / 'docs/index.html').read_text(encoding='utf-8') == '<h1>prior site</h1>'
    assert not (restored / '.env').exists()
    with pytest.raises(ValueError, match='empty directory'):
        restore(out, restored)
    with pytest.raises(ValueError, match='outside'):
        snapshot(repo, repo / 'docs' / 'bad.zip')


def test_recovery_rejects_changed_bytes_before_writing(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / 'prior.zip'
    snapshot(repo, out)
    with zipfile.ZipFile(out) as archive:
        files = {info.filename:archive.read(info) for info in archive.infolist()}
    files['docs/index.html'] = b'changed after snapshot'
    with zipfile.ZipFile(out, 'w') as archive:
        for name, value in files.items(): archive.writestr(name, value)
    with pytest.raises(ValueError, match='checksum'):
        restore(out, tmp_path / 'review')
    assert not (tmp_path / 'review').exists()


@pytest.mark.parametrize('name', ['../escape','docs/../../escape','docs\\escape','C:/escape'])
def test_recovery_rejects_unsafe_archive_paths(name):
    raw = io.BytesIO()
    # Windows ZipInfo normalises its input. Patch both raw name records to
    # simulate an untrusted archive produced on another operating system.
    placeholder = 'x' * len(name)
    with zipfile.ZipFile(raw, 'w') as archive: archive.writestr(placeholder, 'unsafe')
    raw = io.BytesIO(raw.getvalue().replace(placeholder.encode('utf-8'), name.encode('utf-8')))
    with zipfile.ZipFile(raw) as archive, pytest.raises(ValueError, match='unsafe'):
        _files(archive)


def test_recovery_rejects_symlink_and_duplicate_entries():
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as archive:
        info = zipfile.ZipInfo('docs/link')
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, '/private')
    raw.seek(0)
    with zipfile.ZipFile(raw) as archive, pytest.raises(ValueError, match='unsafe'):
        _files(archive)


def test_unvalidated_prior_version_is_preserved_but_not_replayed_as_good(tmp_path):
    repo = _repo(tmp_path)
    (repo / 'docs/data/content/a1.json').write_text('{broken', encoding='utf-8')
    subprocess.run(['git','-C',str(repo),'add','.'], check=True, capture_output=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.com',
                    'commit','-m','broken fixture'], check=True, capture_output=True)
    out = tmp_path / 'invalid.zip'
    with pytest.raises(ValueError): snapshot(repo, out)
    assert not out.exists()
    assert snapshot(repo, out, allow_unvalidated=True)['validation'] == 'unvalidated'
    with pytest.raises(ValueError, match='unvalidated'):
        restore(out, tmp_path / 'review')


def test_publication_workflow_blocks_push_until_code_and_data_gates_pass():
    root = Path(__file__).resolve().parents[1]
    source = (root / '.github/workflows/update.yml').read_text(encoding='utf-8')
    names = ['Retain prior public site','Run offline code regressions','Fetch RSS, scrape & analyse',
             'Validate generated site before publication','Commit & push if changed']
    positions = [source.index('- name: ' + name) for name in names]
    assert positions == sorted(positions)
    gate = source.split('- name: Validate generated site before publication',1)[1].split('- name:',1)[0]
    assert '--require-current' in gate and 'if ($LASTEXITCODE -ne 0)' in gate
    assert 'continue-on-error' not in gate
    assert 'Retain recovery artifact' in source and 'retention-days: 3' in source
    assert source.index('check-source --tested') < source.index('git reset --mixed origin/main')


def test_concurrent_data_commit_is_allowed_but_changed_code_requires_rebuild(tmp_path):
    repo = _repo(tmp_path)
    tested = subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'], text=True).strip()
    def commit():
        subprocess.run(['git','-C',str(repo),'add','.'], check=True, capture_output=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.com',
                        'commit','-m','fixture change'], check=True, capture_output=True)
    (repo / 'docs/data/new.json').write_text('{}', encoding='utf-8')
    commit()
    check_source(repo, tested, 'HEAD')
    (repo / 'docs/index.html').write_text('<h1>new untested assets</h1>', encoding='utf-8')
    commit()
    with pytest.raises(ValueError, match='changed since tested'):
        check_source(repo, tested, 'HEAD')


def test_recovery_trusts_only_selected_checkout_without_persistent_config(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    original = (repo / '.git/config').read_bytes()
    monkeypatch.setenv('GIT_TEST_ASSUME_DIFFERENT_OWNER', '1')
    # Hosted images can trust all paths globally; isolate this security fixture
    # from machine configuration without modifying any real Git settings.
    import os
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.delenv('GIT_CONFIG_COUNT', raising=False)
    monkeypatch.delenv('GIT_CONFIG_PARAMETERS', raising=False)
    denied = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True)
    assert denied.returncode != 0 and b'dubious ownership' in denied.stderr
    manifest = snapshot(repo, tmp_path / 'trusted.zip')
    check_source(repo, manifest['revision'], 'HEAD')
    assert (repo / '.git/config').read_bytes() == original
    from tools.site_snapshot import _git
    command = _git(repo)
    assert command[2] == 'safe.directory='
    assert command[4] == 'safe.directory=' + repo.resolve().as_posix()
    assert 'safe.directory=*' not in command


def test_command_trust_clears_inherited_wildcard_only_for_current_process(tmp_path, monkeypatch):
    from tools.site_snapshot import _git
    selected = _repo(tmp_path / 'selected')
    other = _repo(tmp_path / 'other')
    inherited = tmp_path / 'global-config'
    inherited.write_text('[safe]\n\tdirectory = *\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(inherited))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_TEST_ASSUME_DIFFERENT_OWNER', '1')
    monkeypatch.delenv('GIT_CONFIG_COUNT', raising=False)
    monkeypatch.delenv('GIT_CONFIG_PARAMETERS', raising=False)
    assert subprocess.run(['git','-C',str(other),'rev-parse','HEAD'], capture_output=True).returncode == 0
    denied = subprocess.run(_git(selected) + ['-C',str(other),'rev-parse','HEAD'], capture_output=True)
    assert denied.returncode != 0 and b'dubious ownership' in denied.stderr
    assert subprocess.run(_git(selected) + ['rev-parse','HEAD'], capture_output=True).returncode == 0
    assert inherited.read_text(encoding='utf-8') == '[safe]\n\tdirectory = *\n'


def test_archive_original_name_rejected_after_platform_normalisation():
    info = zipfile.ZipInfo('docs/escape')
    info.orig_filename = 'docs\\escape'
    class Archive:
        def infolist(self): return [info]
    with pytest.raises(ValueError, match='unsafe'):
        _files(Archive())
