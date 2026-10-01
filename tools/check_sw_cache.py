"""Reject frontend changes that leave installed clients on the old SW cache."""
import re
import subprocess
import sys


def check(base):
    changed = subprocess.check_output(['git', 'diff', '--name-only', base, 'HEAD'], text=True).splitlines()
    assets = ('docs/js/', 'docs/css/', 'docs/vendor/')
    shell = {'docs/index.html', 'docs/article.html', 'docs/manifest.json'}
    if not any(p.startswith(assets) or p in shell for p in changed):
        return
    pattern = r'const\s+CACHE\s*=\s*["\']([^"\']+)'
    before = subprocess.check_output(['git', 'show', f'{base}:docs/sw.js'], text=True)
    after = subprocess.check_output(['git', 'show', 'HEAD:docs/sw.js'], text=True)
    old, new = re.search(pattern, before), re.search(pattern, after)
    if not old or not new or old.group(1) == new.group(1):
        raise SystemExit('Frontend changed: bump docs/sw.js CACHE before merging.')


if __name__ == '__main__':
    check(sys.argv[1])
