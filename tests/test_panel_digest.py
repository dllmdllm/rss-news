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


def _evidenced_pair(a, b):
    members = [dict(id='a', source='甲', url='https://example.org/a', title=a),
               dict(id='b', source='乙', url='https://example.org/b', title=b)]
    pair = dict(claim_a=a, claim_b=b, quote_a=a, quote_b=b,
                source_a='甲', source_b='乙', article_id_a='a', article_id_b='b', type='數字')
    return pair, members


def test_identical_dates_and_early_year_are_not_contradictions():
    from src.panel_digest import _validated_contradiction
    for claim in ['預定10月27日發售', '預定2027年初推出']:
        pair, members = _evidenced_pair(claim, claim)
        assert _validated_contradiction(pair, members) is None


def test_compatible_percentage_bounds_are_not_contradictions():
    from src.panel_digest import _validated_contradiction
    pair, members = _evidenced_pair('價格上升至少15%', '價格上升15–20%')
    assert _validated_contradiction(pair, members) is None


def test_disjoint_same_context_percentages_preserve_verified_evidence():
    from src.panel_digest import _validated_contradiction
    pair, members = _evidenced_pair('價格上升15%', '價格上升20%')
    pair['url_a'] = 'https://attacker.invalid/'
    evidence = _validated_contradiction(pair, members)
    assert evidence['url_a'] == 'https://example.org/a'
    assert evidence['quote_a'] == '價格上升15%'


def test_unsupported_or_wrong_source_contradiction_is_rejected():
    from src.panel_digest import _validated_contradiction
    for field, value in [('quote_a', '假的原文15%'), ('source_a', '錯誤來源'),
                         ('article_id_a', 'missing'), ('claim_a', '價格上升50%')]:
        pair, members = _evidenced_pair('價格上升15%', '價格上升20%')
        pair[field] = value
        assert _validated_contradiction(pair, members) is None


def test_different_numeric_contexts_do_not_prove_contradiction():
    from src.panel_digest import _validated_contradiction
    pair, members = _evidenced_pair('價格上升15%', '銷量上升20%')
    assert _validated_contradiction(pair, members) is None


def test_content_signature_changes_on_relevant_input_and_ignores_scores():
    base = dict(id='a', title='Title', summary='Summary', content='<p>Body</p>', date='2026-10-01',
                source='Publisher', url='https://example.org/a', score=9)
    original = _signature('c', [base])
    for key in ['title', 'summary', 'content', 'source', 'url']:
        assert _signature('c', [{**base, key: base[key] + ' correction'}]) != original
    assert _signature('c', [{**base, 'date': '2026-10-02'}]) != original
    assert _signature('c', [{**base, 'score': 10}]) == original


def test_approximate_estimates_and_dates_do_not_prove_contradiction():
    from src.panel_digest import _provably_conflicting
    assert not _provably_conflicting('裁員約15%', '裁員約20%')
    assert not _provably_conflicting('預計10月12日啟用', '預計10月13日啟用')


def test_failed_digest_refresh_removes_obsolete_artifact(monkeypatch):
    import asyncio
    import src.panel_digest as panel
    members = [_art('a', 'c', 9), _art('b', 'c', 8)]
    stale = {'c': {'signature': 'old', 'version': panel.DIGEST_VERSION, 'digest': {'headline': 'old'}}}
    saved = []
    monkeypatch.setattr(panel, 'MINIMAX_API_KEY', 'offline-test-key')
    monkeypatch.setattr(panel, 'load_cache', lambda: stale)
    monkeypatch.setattr(panel, 'save_cache', lambda value: saved.append(value))
    async def failed(*args):
        return None
    monkeypatch.setattr(panel, '_digest_one', failed)
    assert asyncio.run(panel.generate_panel_digests(members)) == {}
    assert saved == [{}]



def test_summary_is_not_source_evidence():
    from src.panel_digest import _validated_contradiction
    pair, members = _evidenced_pair('價格上升15%', '價格上升20%')
    for member in members:
        member['summary'] = member['title']
        member['title'] = '市場報道'
        member['content'] = '<p>只係討論市場</p>'
    assert _validated_contradiction(pair, members) is None


def test_html_body_is_source_evidence_and_content_change_invalidates_signature():
    from src.panel_digest import _validated_contradiction
    pair, members = _evidenced_pair('價格上升15%', '價格上升20%')
    for member in members:
        member['content'] = '<p>' + member['title'] + '</p>'
        member['title'] = '市場報道'
    assert _validated_contradiction(pair, members) is not None
    initial = _signature('c', members)
    members[0]['content'] = '<p>價格上升25%</p>'
    assert _signature('c', members) != initial
    assert _validated_contradiction(pair, members) is None


def test_rss_body_change_invalidates_digest_signature():
    article = dict(id='a', title='市場報道', rss_content='<p>價格上升15%</p>')
    assert _signature('c', [article]) != _signature('c', [{**article, 'rss_content': '<p>價格上升25%</p>'}])


def test_offline_build_prunes_obsolete_digest_without_calls(monkeypatch):
    import asyncio
    import src.panel_digest as panel
    members = [_art('a', 'c', 9), _art('b', 'c', 8)]
    saved = []
    monkeypatch.setattr(panel, 'MINIMAX_API_KEY', '')
    monkeypatch.setattr(panel, 'load_cache', lambda: {'c': {'signature': 'old', 'version': panel.DIGEST_VERSION, 'digest': {'headline': 'stale'}}})
    monkeypatch.setattr(panel, 'save_cache', lambda value: saved.append(value))
    assert asyncio.run(panel.generate_panel_digests(members)) == {}
    assert saved == [{}]


def test_no_qualifying_clusters_clears_digest_artifact(monkeypatch):
    import asyncio
    import src.panel_digest as panel
    saved = []
    monkeypatch.setattr(panel, 'MINIMAX_API_KEY', 'offline-test-key')
    monkeypatch.setattr(panel, 'save_cache', lambda value: saved.append(value))
    assert asyncio.run(panel.generate_panel_digests([_art('a', 'solo', 9)])) == {}
    assert saved == [{}]
