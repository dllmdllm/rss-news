import asyncio
import json

import build
from src import analyse, panel_digest as panel
from src import fetch
from src.article_evidence import update_provenance, validate_summary_evidence, body_text, source_outlet


def article(**changes):
    return {"id": "a", "date": "2026-10-12T00:00:00+00:00", "source": "甲報",
            "url": "https://example.org/a", "title": "消息", "summary": "・政府公布新安排\n・學校繼續運作",
            "content": "<p>政府今日公布<strong>新的交通安排</strong>，明日起實施。</p><p>所有學校將繼續正常運作。</p>",
            "content_quality": {"fallback": "none"}, **changes}


def test_summary_evidence_exact_pair_only_and_inline_text():
    current = article()
    supports = [{"point": "政府公布新安排", "quote": "政府今日公布新的交通安排，明日起實施。"},
                {"point": "學校繼續運作", "quote": "不存在的原文句子，絕不可當作證據。"},
                {"point": "不是目前摘要", "quote": "所有學校將繼續正常運作。"}]
    assert validate_summary_evidence(current, supports) == [{
        "point": "政府公布新安排", "quote": supports[0]["quote"], "article_id": "a", "verified": True}]
    assert validate_summary_evidence(current, None) == []


def test_summary_or_title_and_other_article_are_not_body_support():
    current = article(title="政府今日公布新的交通安排，明日起實施。", content="<p>其他正文，與以上說法不同。</p>")
    raw = [{"point": "政府公布新安排", "quote": current["title"]}]
    assert validate_summary_evidence(current, raw) == []
    assert validate_summary_evidence(article(), [{**raw[0], "article_id": "other"}]) == []


def test_key_sentence_position_never_creates_summary_support():
    current = article(key_sentences=["所有學校將繼續正常運作。"])
    analyse._ensure_analysis_defaults([current])
    assert current["summary_evidence"] == []
    assert current["summary_evidence_status"] == "unavailable"


def test_generated_minimal_placeholder_is_not_original_source_evidence():
    current = article(content='<p>政府今日公布新的交通安排，明日起實施。</p>',
                      content_quality={'fallback': 'minimal'})
    raw = [{'point': '政府公布新安排', 'quote': '政府今日公布新的交通安排，明日起實施。'}]
    assert validate_summary_evidence(current, raw) == []


def test_analysis_caches_only_valid_summary_quotes(monkeypatch):
    current = article()
    parsed = {"summary": current["summary"], "score": 8, "tags": [], "sentiment": "neutral", "topic": "安排",
              "summary_evidence": [{"point": "政府公布新安排", "quote": "政府今日公布新的交通安排，明日起實施。"}]}
    cache = {}
    asyncio.run(analyse._apply_results([current], [parsed], cache, asyncio.Lock(), [0]))
    assert cache['a']['summary_evidence'][0]['verified'] is True
    assert current['analysis_input_hash'] == analyse.analysis_input_hash(current)


def test_body_text_ignores_executable_or_hidden_template_content():
    assert body_text('<p>今日<strong>消息</strong>。</p><script>假的。</script><template>隱藏。</template>') == '今日消息。'


def test_unchanged_body_keeps_update_time_and_new_fetch_time():
    current = article(fetched_at="2026-10-12T02:00:00+00:00")
    old = {"content": current['content'], "content_updated_at": "2026-10-11T22:00:00+00:00"}
    out = update_provenance(current, old, now="2026-10-12T03:00:00+00:00")
    assert out['content_updated_at'] == old['content_updated_at']
    assert out['fetched_at'] == current['fetched_at']


def test_changed_body_gets_observed_change_timestamp():
    current = article(fetched_at="2026-10-12T02:00:00+00:00")
    out = update_provenance(current, {"content": "<p>舊文章。</p>"})
    assert out['content_updated_at'] == current['fetched_at']
    assert len(out['content_hash']) == 64


def test_legacy_unchanged_body_does_not_invent_update_age():
    current = article(fetched_at="2026-10-12T02:00:00+00:00")
    out = update_provenance(current, {"content": current['content'], "scraped_at": "2026-10-01T00:00:00+00:00"}, now="2026-10-12T03:00:00+00:00")
    assert out['content_updated_at'] is None


def test_retention_does_not_claim_current_update_or_observation():
    current = article(content_retention={"scraped_at": "2026-10-01T00:00:00Z"}, scrape_error="HTTP 503")
    out = update_provenance(current, {"content": current['content']})
    assert out['fetched_at'] is None and out['content_updated_at'] is None


def test_failed_feed_has_check_timestamp_but_no_fetched_observation(monkeypatch):
    monkeypatch.setattr(fetch, 'RSS_FEEDS', [{'name': '甲報', 'url': 'https://example.org/feed', 'category': '新聞'}])
    monkeypatch.setattr(fetch, '_load_feed_http_cache', lambda: {})
    monkeypatch.setattr(fetch, '_save_feed_http_cache', lambda value: None)
    async def failed(*args):
        return [], 'HTTP 503', False
    monkeypatch.setattr(fetch, '_fetch_one_capped', failed)
    articles, stats = asyncio.run(fetch.fetch_all())
    assert articles == []
    assert stats['甲報']['checked_at'].endswith('+00:00')
    assert stats['甲報']['observed_at'] is None


def test_save_json_provenance_reaches_full_slim_and_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(build, "CONTENT_DIR", tmp_path / "data" / "content")
    monkeypatch.setattr(build, "DOCS_DIR", tmp_path)
    current = article(fetched_at="2026-10-12T02:00:00Z", content_quality={"fallback": "rss-blocked"}, scrape_error="HTTP 403")
    build.save_json([current], {"甲報": {"count": 1}})
    full = json.loads((build.DATA_DIR / "articles.json").read_text())
    slim = json.loads((build.DATA_DIR / "articles_index.json").read_text())
    sidecar = json.loads((build.CONTENT_DIR / "a.json").read_text())
    assert full['schema_version'] == slim['schema_version'] == 2
    for record in [full['articles'][0], slim['articles'][0], sidecar]:
        assert record['evidence_status'] == 'partial'
        assert record['fetched_at'] == '2026-10-12T02:00:00Z'
        assert record['scrape_error'] == 'HTTP 403'
    first = sidecar['content_updated_at']
    current['fetched_at'] = '2026-10-12T03:00:00Z'
    build.save_json([current], {"甲報": {"count": 1}})
    assert json.loads((build.CONTENT_DIR / 'a.json').read_text())['content_updated_at'] == first


def comparison_members():
    quote = '政府今日公布新的交通安排，明日起實施。'
    return [article(id='a', content=f'<p>{quote}</p>'),
            article(id='b', source='乙報', url='https://example.org/b', date='2026-10-12T01:00:00Z', content=f'<p>{quote}</p>')], quote


def test_shared_fact_requires_both_outlets_actual_same_source_statement():
    members, quote = comparison_members()
    entry = {'text': quote, 'evidence': [{'article_id': 'a', 'quote': quote}, {'article_id': 'b', 'quote': quote}]}
    result = panel._grounded_comparison({'shared_facts': [entry]}, members)
    assert len(result['shared_facts']) == 1
    members[1]['content'] = '<p>所有學校將繼續正常運作。</p>'
    assert panel._grounded_comparison({'shared_facts': [entry]}, members)['shared_facts'] == []
    members[1]['source'] = members[0]['source']
    assert panel._grounded_comparison({'shared_facts': [entry]}, members)['shared_facts'] == []


def test_newer_publication_alone_is_not_a_development():
    members, quote = comparison_members()
    unrelated = '所有學校將繼續正常運作。'
    members[1]['content'] = f'<p>{unrelated}</p>'
    raw = {'new_developments': [{'text': unrelated, 'evidence': [{'article_id': 'b', 'quote': unrelated}]}]}
    assert panel._grounded_comparison(raw, members)['new_developments'] == []


def test_shared_fact_distinct_feed_categories_are_not_distinct_outlets():
    members, quoted = comparison_members()
    entry = {'text': quoted, 'evidence': [{'article_id': 'a', 'quote': quoted}, {'article_id': 'b', 'quote': quoted}]}
    for a, b in [('HK01 突發', 'HK01 社會'), ('RTHK 本地', 'RTHK 國際'),
                 ('HuffPost 新聞', 'HuffPost 娛樂'), ('明報 本地', '明報 中國'),
                 ('東網 本地', '東網 娛樂'), ('Now 新聞', 'Now 國際'),
                 ('星島頭條', '星島 即時國際')]:
        members[0]['source'], members[1]['source'] = a, b
        assert panel._grounded_comparison({'shared_facts': [entry]}, members)['shared_facts'] == []
    assert source_outlet('The Collective HK') != source_outlet('The Witness')
    assert source_outlet('星島其他機構') != source_outlet('星島頭條')
    members[1]['source'] = 'The Witness'
    assert panel._grounded_comparison({'shared_facts': [entry]}, members)['shared_facts']


def test_new_developments_sort_by_actual_instant_across_timezone_offsets():
    first = '政府今日公布新的交通安排，明日起實施。'
    later = '警方其後公布新的調查結果，將繼續跟進。'
    members = [article(id='a', content=f'<p>{first}</p>', date='2026-10-12T08:00:00+08:00'),
               article(id='b', content=f'<p>{later}</p>', date='2026-10-12T01:00:00+00:00')]
    entries = [{'text': text, 'evidence': [{'article_id': aid, 'quote': text}]}
               for aid, text in [('b', later), ('a', first)]]
    assert [item['text'] for item in panel._grounded_comparison({'new_developments': entries}, members)['new_developments']] == [first, later]


def test_comparison_rejects_model_paraphrase_even_if_quote_exists():
    members, quote = comparison_members()
    raw = {'differing_accounts': [{'text': '政府禁止交通', 'evidence': [{'article_id': 'a', 'quote': quote}]}]}
    assert not panel._grounded_comparison(raw, members)['comparison_available']


def test_grounded_quote_binds_actual_body_hash_not_stale_metadata():
    import hashlib
    members, quoted = comparison_members()
    members[0]['content_hash'] = '0' * 64
    entry = {'text': quoted, 'evidence': [{'article_id': 'a', 'quote': quoted}]}
    raw = {'new_developments': [entry]}
    support = panel._grounded_comparison(raw, members)['new_developments'][0]['evidence'][0]
    assert support['verified'] is True
    assert support['content_hash'] == hashlib.sha256(members[0]['content'].encode()).hexdigest()
    original = support['content_hash']
    members[0]['content'] += '<p>來源其後補充另一項安排。</p>'
    updated = panel._grounded_comparison(raw, members)['new_developments'][0]['evidence'][0]
    assert updated['content_hash'] != original
    members[0]['content'] = '<p>原有引用已被來源刪除。</p>'
    assert not panel._grounded_comparison(raw, members)['new_developments']


def test_current_legacy_panel_retained_but_comparison_unavailable(monkeypatch):
    members, quote = comparison_members()
    for item in members:
        item.update(cluster_id='c', score=9)
    entry = {'version': panel.LEGACY_DIGEST_VERSION,
             'signature': panel._signature('c', members, version=panel.LEGACY_DIGEST_VERSION),
             'digest': {'headline': '舊摘要', 'shared_facts': [{'text': '未驗證'}]}}
    saved = []
    monkeypatch.setattr(panel, 'MINIMAX_API_KEY', '')
    monkeypatch.setattr(panel, 'load_cache', lambda: {'c': entry})
    monkeypatch.setattr(panel, 'save_cache', saved.append)
    result = asyncio.run(panel.generate_panel_digests(members))
    assert result['c']['headline'] == '舊摘要'
    assert result['c']['shared_facts'] == [] and not result['c']['comparison_available']
