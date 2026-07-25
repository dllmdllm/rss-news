"""AGENTS.md must stay a faithful mirror of CLAUDE.md.

Why this test exists: the two were hand-synced, and on 2026-07-25 they
turned out to have diverged silently for six weeks — AGENTS.md was frozen
at 2026-06-10, still listed SkyPost (dead) as an active source, and was
missing every design decision made since mid-June. Agents that read
AGENTS.md instead of CLAUDE.md were working off a stale map, and nothing
surfaced it. A test is cheap; remembering to copy a file is not.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SYNC_SCRIPT = ROOT / "tools" / "sync_agents_md.py"

sys.path.insert(0, str(ROOT))
from tools.sync_agents_md import MIRROR, SOURCE, render  # noqa: E402


def _normalise(text: str) -> str:
    """Compare content, not checkout line-ending settings (Windows is CRLF)."""
    return text.replace("\r\n", "\n")


def test_agents_md_matches_claude_md():
    assert MIRROR.exists(), "AGENTS.md is missing — run: python tools/sync_agents_md.py"
    assert _normalise(MIRROR.read_text(encoding="utf-8")) == _normalise(render()), (
        "AGENTS.md has drifted from CLAUDE.md. "
        "CLAUDE.md is the source of truth — do not hand-edit AGENTS.md. "
        "Fix with: python tools/sync_agents_md.py"
    )


def test_agents_md_carries_the_full_body():
    # Guards the failure mode that actually happened: a *truncated* mirror
    # that still looks plausible. Equality above covers it, but this asserts
    # the intent directly so a future refactor of render() can't quietly
    # start emitting a stub.
    body = _normalise(MIRROR.read_text(encoding="utf-8"))
    source = _normalise(SOURCE.read_text(encoding="utf-8"))
    assert source in body, "AGENTS.md must contain CLAUDE.md verbatim, not a summary"


def test_sync_script_check_mode_passes_when_in_sync():
    result = subprocess.run(
        [sys.executable, str(SYNC_SCRIPT), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_sync_script_check_mode_detects_drift(tmp_path, monkeypatch):
    import tools.sync_agents_md as sync

    drifted = tmp_path / "AGENTS.md"
    drifted.write_text("# stale copy\n", encoding="utf-8")
    monkeypatch.setattr(sync, "MIRROR", drifted)

    assert sync.main(["--check"]) == 1


def test_sync_script_rewrites_mirror(tmp_path, monkeypatch):
    import tools.sync_agents_md as sync

    out = tmp_path / "AGENTS.md"
    monkeypatch.setattr(sync, "MIRROR", out)

    assert sync.main([]) == 0
    assert _normalise(SOURCE.read_text(encoding="utf-8")) in _normalise(
        out.read_text(encoding="utf-8")
    )


def test_design_history_links_resolve():
    """Every DESIGN-HISTORY.md#anchor referenced by CLAUDE.md must exist.

    CLAUDE.md now delegates its case histories to DESIGN-HISTORY.md, so a
    renamed anchor would silently strand the "why" behind a rule.
    """
    import re

    claude = SOURCE.read_text(encoding="utf-8")
    history_path = ROOT / "DESIGN-HISTORY.md"
    assert history_path.exists(), "DESIGN-HISTORY.md is missing"
    history = history_path.read_text(encoding="utf-8")

    referenced = set(re.findall(r"DESIGN-HISTORY\.md#([a-zA-Z0-9_-]+)", claude))
    anchors = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', history))
    missing = referenced - anchors
    assert not missing, f"CLAUDE.md links to missing DESIGN-HISTORY.md anchors: {sorted(missing)}"
    assert referenced, "expected CLAUDE.md to link into DESIGN-HISTORY.md"
