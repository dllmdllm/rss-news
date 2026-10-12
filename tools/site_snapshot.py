"""Retain/verify a public tracked-site version; restore only into an empty folder.

No fetch, push, provider call or live deployment is performed by this tool.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.validate_publication import validate_site

MANIFEST = '.rss-recovery.json'
MAX_BYTES = 512 * 1024 * 1024


def _git(repo):
    # Trust only this explicitly selected checkout for this read-only process.
    # Windows service runners may not own the checkout; never persist config.
    repo = Path(repo).resolve()
    return ['git', '-c', 'safe.directory=', '-c', 'safe.directory=' + repo.as_posix(), '-C', str(repo)]


def _files(archive):
    files, total = {}, 0
    infos = archive.infolist()
    if len(infos) > 10000:
        raise ValueError('too many snapshot entries')
    for info in infos:
        # ZipInfo may normalise backslashes on Windows or truncate a NUL.
        # Validate the original archive name before using its interpreted path.
        name = info.orig_filename
        path = PurePosixPath(name)
        if ('\\' in name or '\x00' in name or ':' in name or path.is_absolute() or '..' in path.parts
                or (name != MANIFEST and (not path.parts or path.parts[0] != 'docs'))
                or stat.S_ISLNK(info.external_attr >> 16)):
            raise ValueError('unsafe snapshot entry')
        if info.is_dir():
            continue
        if name in files:
            raise ValueError('duplicate snapshot entry')
        total += info.file_size
        if total > MAX_BYTES:
            raise ValueError('snapshot exceeds size limit')
        files[name] = archive.read(info)
    return files


def snapshot(repo: Path, out: Path, *, allow_unvalidated=False):
    repo, out = Path(repo).resolve(), Path(out).resolve()
    if out.is_relative_to(repo / 'docs'):
        raise ValueError('recovery archive must stay outside published docs')
    revision = subprocess.check_output(_git(repo) + ['rev-parse', 'HEAD'], text=True).strip()
    raw = subprocess.check_output(_git(repo) + ['archive', '--format=zip', revision, 'docs'])
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = _files(archive)
    # Validate the archived commit's bytes, not potentially modified checkout
    # files. This also excludes .env, user reading state and untracked secrets.
    with tempfile.TemporaryDirectory(prefix='rss-recovery-') as tmp:
        root = Path(tmp)
        for name, value in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        try:
            validate_site(root / 'docs')
            validation = 'passed'
        except ValueError:
            if not allow_unvalidated:
                raise
            validation = 'unvalidated'
    manifest = {'version': 1, 'revision': revision, 'validation': validation,
                'sha256': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}}
    out.parent.mkdir(parents=True, exist_ok=True)
    staged = out.with_suffix(out.suffix + '.tmp')
    try:
        with zipfile.ZipFile(staged, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in files.items(): archive.writestr(name, value)
            archive.writestr(MANIFEST, json.dumps(manifest, sort_keys=True))
        staged.replace(out)
    finally:
        staged.unlink(missing_ok=True)
    return manifest


def verify(archive_path: Path):
    with zipfile.ZipFile(archive_path) as archive:
        files = _files(archive)
    try:
        manifest = json.loads(files.pop(MANIFEST))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('missing/invalid recovery manifest') from exc
    if (manifest.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{40}', str(manifest.get('revision', '')))
            or manifest.get('validation') not in {'passed', 'unvalidated'}):
        raise ValueError('invalid recovery identity')
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    if hashes != manifest.get('sha256'):
        raise ValueError('snapshot checksum mismatch')
    return manifest, files


def restore(archive_path: Path, out: Path):
    manifest, files = verify(archive_path)
    if manifest['validation'] != 'passed':
        raise ValueError('unvalidated prior snapshot requires manual review; cannot restore as good')
    out = Path(out).resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError('restore destination must be an empty directory')
    out.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    validate_site(out / 'docs')
    return manifest


def check_source(repo: Path, tested: str, current: str):
    # Data commits can restack normally. A new code/assets revision needs its
    # own build/tests, not a silent mixture of old output and new source.
    changed = subprocess.check_output(_git(repo) + ['diff', '--name-only',
                                      tested, current, '--'], text=True).splitlines()
    allowed = {'config/watch_keywords.txt', 'config/trending_keywords.txt'}
    if any(not name.startswith('docs/data/') and name not in allowed for name in changed):
        raise ValueError('source/assets changed since tested build; keep prior publication and rebuild normally')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    save = commands.add_parser('snapshot')
    save.add_argument('--repo', type=Path, default=Path.cwd())
    save.add_argument('--out', type=Path, required=True)
    save.add_argument('--allow-unvalidated', action='store_true')
    check = commands.add_parser('verify')
    check.add_argument('archive', type=Path)
    recover = commands.add_parser('restore')
    recover.add_argument('archive', type=Path)
    recover.add_argument('--out', type=Path, required=True)
    guard = commands.add_parser('check-source')
    guard.add_argument('--repo', type=Path, default=Path.cwd())
    guard.add_argument('--tested', required=True)
    guard.add_argument('--current', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'snapshot':
            result = snapshot(args.repo, args.out, allow_unvalidated=args.allow_unvalidated)
        elif args.command == 'restore':
            result = restore(args.archive, args.out)
        elif args.command == 'check-source':
            check_source(args.repo, args.tested, args.current)
            print('publication source/assets match tested build')
            return
        else:
            result, _ = verify(args.archive)
        print(f"recovery {args.command}: {result['revision']} ({result['validation']})")
    except (ValueError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'recovery failed: {exc}\n')


if __name__ == '__main__':
    main()
