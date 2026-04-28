"""Unit tests for src/panel_digest.py — threshold selection,
signature stability, and normaliser bounds.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.panel_digest import (
    DIGEST_VERSION,
    _normalise_digest,
    _signature,
    collect_qualifying_clusters,
)


# ── _signature ───────────────────────────────────────────────────

def test_signature_is_order_independent():
    assert _signature("c1", ["b", "a"]) == _signature("c1", ["a", "b"])


def test_signature_changes_with_membership():
    a = _signature("c1", ["a", "b"])
    b = _signature("c1", ["a", "b", "c"])
    assert a != b


def test_signature_changes_with_cluster_id():
    assert _signature("c1", ["a"]) != _signature("c2", ["a"])


# ── collect_qualifying_clusters ──────────────────────────────────

def _art(aid, cid, score, dup=None):
    a = {"id": aid, "cluster_id": cid, "score": score, "title": "t", "source": "s"}
    if dup:
        a["duplicate_of"] = dup
    return a


def test_qualifying_includes_large_cluster():
    arts = [_art(f"a{i}", "big", 5) for i in range(4)]
    out = collect_qualifying_clusters(arts)
    assert len(out) == 1 and out[0][0] == "big"


def test_qualifying_includes_high_score_small_cluster():
    arts = [_art("a1", "imp", 9), _art("a2", "imp", 5)]
    out = collect_qualifying_clusters(arts)
    assert len(out) == 1 and out[0][0] == "imp"


def test_qualifying_excludes_small_low_score_cluster():
    arts = [_art("a1", "small", 5), _art("a2", "small", 6)]
    assert collect_qualifying_clusters(arts) == []


def test_qualifying_excludes_singleton_cluster():
    arts = [_art("a1", "solo", 10)]
    assert collect_qualifying_clusters(arts) == []


def test_qualifying_skips_duplicate_articles():
    arts = [_art("a1", "x", 9, dup="real"), _art("a2", "x", 5)]
    # Only a2 counts; cluster has size 1 after skipping dup → excluded.
    assert collect_qualifying_clusters(arts) == []


def test_qualifying_sorted_by_peak_then_size():
    arts = (
        [_art(f"big{i}", "big", 5) for i in range(5)] +     # peak 5, size 5
        [_art(f"hot{i}", "hot", 9) for i in range(2)]       # peak 9, size 2
    )
    out = [cid for cid, _ in collect_qualifying_clusters(arts)]
    assert out == ["hot", "big"]


# ── _normalise_digest ────────────────────────────────────────────

def test_normalise_digest_happy_path():
    out = _normalise_digest({
        "headline": "重大事件",
        "consensus": "各方一致報導事實",
        "angles": [
            {"label": "焦點A", "sources": ["明報"], "detail": "點寫"},
            {"label": "焦點B", "sources": ["RTHK", "HK01"], "detail": ""},
        ],
        "tension": "",
    })
    assert out["headline"] == "重大事件"
    assert len(out["angles"]) == 2
    assert out["angles"][0]["sources"] == ["明報"]
    assert out["version"] == DIGEST_VERSION


def test_normalise_digest_drops_when_too_few_angles():
    out = _normalise_digest({
        "headline": "重大事件",
        "consensus": "X",
        "angles": [{"label": "只有一個", "sources": ["明報"], "detail": ""}],
        "tension": "",
    })
    assert out is None


def test_normalise_digest_drops_when_headline_missing():
    out = _normalise_digest({
        "headline": "",
        "consensus": "X",
        "angles": [
            {"label": "A", "sources": ["X"], "detail": ""},
            {"label": "B", "sources": ["Y"], "detail": ""},
        ],
        "tension": "",
    })
    assert out is None


def test_normalise_digest_caps_angles_at_four():
    out = _normalise_digest({
        "headline": "事件",
        "consensus": "X",
        "angles": [
            {"label": f"焦點{i}", "sources": ["S"], "detail": ""}
            for i in range(6)
        ],
        "tension": "",
    })
    assert len(out["angles"]) == 4
