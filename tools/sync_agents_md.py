"""Regenerate AGENTS.md from CLAUDE.md.

CLAUDE.md is the single source of truth; AGENTS.md is a byte-for-byte
mirror (plus a header) so agents that only look for AGENTS.md — Codex and
friends — see the same guidance. Keeping two hand-maintained copies did
not work: they silently diverged for six weeks (AGENTS.md froze on
2026-06-10 and still advertised SkyPost as a live source long after it
died), which is what `tests/test_docs_sync.py` now guards against.

    python tools/sync_agents_md.py           # rewrite AGENTS.md
    python tools/sync_agents_md.py --check   # exit 1 if out of sync
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "CLAUDE.md"
MIRROR = ROOT / "AGENTS.md"

HEADER = """<!-- ⚠️ 呢個檔案由 CLAUDE.md 自動生成，唔好直接改呢邊。
     改 CLAUDE.md，然後行：python tools/sync_agents_md.py
     tests/test_docs_sync.py 會 catch 兩邊唔一致。
     （2026-07-25 之前係人手同步，靜靜哋分岔咗六個星期。） -->

"""


def render() -> str:
    return HEADER + SOURCE.read_text(encoding="utf-8")


def main(argv: list[str]) -> int:
    expected = render()
    if "--check" in argv:
        current = MIRROR.read_text(encoding="utf-8") if MIRROR.exists() else ""
        # Normalise line endings: the working copy is CRLF on Windows but the
        # comparison is about content, not checkout settings.
        if current.replace("\r\n", "\n") == expected.replace("\r\n", "\n"):
            print("AGENTS.md is in sync with CLAUDE.md")
            return 0
        print("AGENTS.md is OUT OF SYNC — run: python tools/sync_agents_md.py")
        return 1
    MIRROR.write_text(expected, encoding="utf-8")
    print(f"AGENTS.md regenerated from CLAUDE.md ({len(expected.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
