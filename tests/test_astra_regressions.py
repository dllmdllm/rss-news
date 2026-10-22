"""Offline reproductions of the reviewed data and operational boundaries."""
import asyncio
import hashlib
import json

import pytest

import build
from src import analyse, media_qa, scrape
from src.article_urls import valid_article_url
from src.fetch import _clean_url
from tools import verify_publication as publication
from tools.validate_publication import validate_site
from test_publication_validation import snapshot, mutate
from test_publication_loop import Clock, expected


@pytest.mark.parametrize('available', [65, 80])
def test_publication_can_finish_in_last_thirty_seconds(available):
    clock = Clock()
    async def fetch(path):
        await asyncio.sleep(.02)  # Real response latency, including final assets.
        return b'current' if clock.now() >= available else b'old'
    result = asyncio.run(publication.verify(expected(), fetch, clock=clock.now, sleep=clock.sleep))
    assert result['status'] == 'verified'
    assert available <= clock.now() < 90


@pytest.mark.parametrize('result', [429, 500, TimeoutError('offline')])
def test_qa_delivery_keeps_pending_until_success(tmp_path, monkeypatch, result):
    path = tmp_path / 'qa.json'
    now = [100.0]
    monkeypatch.setattr(media_qa.time, 'time', lambda: now[0])
    media_qa.atomic_save(path, {'version': 1, 'sources': {'source': {
        'pending_event': {'status': 'failed', 'fingerprint': 'new', 'problem': 'test'}}}})
    calls = []
    async def failed(message):
        calls.append(message)
        if isinstance(result, Exception): raise result
        return result
    assert asyncio.run(media_qa.notify_media_qa(path=path, sender=failed)) == []
    row = media_qa.load_state(path)['sources']['source']
    assert row['pending_event'] and not row.get('acknowledged')
    assert asyncio.run(media_qa.notify_media_qa(path=path, sender=failed)) == []
    assert len(calls) == 1
    now[0] = row['notification_retry_at']
    async def okay(message): return 200
    assert asyncio.run(media_qa.notify_media_qa(path=path, sender=okay)) == ['source']
    assert asyncio.run(media_qa.notify_media_qa(path=path, sender=okay)) == []


@pytest.mark.parametrize('parts', [('一', '二'), ('1', '2'), ('I', 'II')])
def test_programme_parts_cannot_collapse_even_with_same_body(parts):
    rows = [dict(id=str(i), title=f'Now節目：建造業前景與工人薪酬（{part}）',
                 url=f'https://news.now.com/home/local/player?newsId={i}', content='<p>'+'共同節目簡介。'*50+'</p>')
            for i, part in enumerate(parts)]
    build.detect_duplicates(rows)
    assert all(not row.get('duplicate_of') for row in rows)


def test_fuzzy_titles_without_identity_are_related_not_duplicates():
    rows = [dict(id=str(i), title=title, url=f'https://example.com/{i}', content='<p>'+str(i)*100+'</p>')
            for i, title in enumerate(['港股今日收市升300點，恆指突破兩萬五', '港股今日收市升301點，恆指突破兩萬五'])]
    build.detect_duplicates(rows)
    assert not any(row.get('duplicate_of') for row in rows)
    rows[1]['title'] = rows[0]['title']
    rows[1]['content'] = rows[0]['content']
    build.detect_duplicates(rows)
    assert sum(bool(row.get('duplicate_of')) for row in rows) == 1


def test_duplicate_identity_does_not_expand_transitively():
    rows = [dict(id=str(i), title='同一事件最新消息', url=url, content=body)
            for i, (url, body) in enumerate([
                ('https://example.com/a', 'a'*100), ('https://example.com/a', 'b'*100),
                ('https://example.com/b', 'b'*100)])]
    build.detect_duplicates(rows)
    assert sum(bool(row.get('duplicate_of')) for row in rows) == 1


def test_key_sentences_accept_only_current_verbatim_text():
    article = dict(content='<p>今日有<strong>重要</strong>消息，警方表示\n  正在調查。</p>')
    exact = '今日有重要消息，警方表示 正在調查。'
    assert analyse._normalise_key_sentences([exact, '今日有重要消息。', '完全無來源支持的虛構句子。'], article) == [exact]
    assert analyse._normalise_key_sentences([exact], dict(content='<p>已更正的其他內容。</p>')) == []


def test_analysis_application_and_cache_hydration_filter_quotes(tmp_path, monkeypatch):
    article = dict(id='a', title='消息', content='<p>這是來源確實提供的完整句子。</p>')
    cache = {}
    parsed = dict(summary='・摘要', score=5, tags=[], sentiment='neutral', topic='',
                  version=analyse.ANALYSIS_VERSION, key_sentences=['這是來源確實提供的完整句子。', '這一句是完全沒有來源支持。'])
    asyncio.run(analyse._apply_results([article], [parsed], cache, asyncio.Lock(), [0]))
    assert cache['a']['key_sentences'] == ['這是來源確實提供的完整句子。']
    cache['a']['key_sentences'].append('這一句是完全沒有來源支持。')
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline')
    monkeypatch.setattr(analyse, 'load_cache', lambda: cache)
    monkeypatch.setattr(analyse, 'save_cache', lambda value: None)
    async def forbidden(*args): raise AssertionError('must use existing cache')
    monkeypatch.setattr(analyse, '_analyse_batch', forbidden)
    asyncio.run(analyse.analyse_all([article]))
    assert article['key_sentences'] == ['這是來源確實提供的完整句子。']


def test_publication_rejects_unsupported_key_sentence(snapshot):
    mutate(snapshot, 'articles.json', lambda d: d['articles'][0].update(key_sentences=['This is a fabricated source sentence.']))
    with pytest.raises(ValueError, match='key_sentences'):
        validate_site(snapshot)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,x', '//example.com/a',
    'https://u:p@example.com/a', 'https://example.com/\x00x', 'https://example.com\\@evil.com/a'])
def test_article_url_boundary(url, snapshot):
    assert not valid_article_url(url)
    assert not _clean_url(url)
    mutate(snapshot, 'articles.json', lambda d: d['articles'][0].update(url=url))
    with pytest.raises(ValueError, match='article URL'):
        validate_site(snapshot, require_current=True)


def test_article_url_keeps_valid_unicode_identity():
    url = 'https://example.com/香港/新聞?id=42'
    assert valid_article_url(url) and _clean_url(url + '" target="blank"') == url


@pytest.mark.parametrize('suffix', ['\ttrailing', '\ntrailing', '\rtrailing'])
def test_control_characters_are_rejected_before_legacy_suffix_cleanup(suffix):
    assert not _clean_url('https://example.com/path' + suffix)


def test_oncc_ingress_rejects_script_url_containing_valid_article_path():
    from src.fetch import _parse_oncc_index
    from datetime import datetime, timezone
    endpoint = 'https://hk.on.cc/hk/bkn/cnt/news/20261022/bkn-20261022120000000-1022_00822_001.html'
    html = "<a href='javascript:alert(\"" + endpoint + "\")'>惡意連結</a>"
    feed = dict(name='東網 本地', url='https://hk.on.cc/hk/news/index.html', category='新聞', oncc_section='news')
    assert _parse_oncc_index(html, feed, datetime(2026, 10, 21, tzinfo=timezone.utc)) == []


def test_nuxt_cycles_shared_dag_and_budget():
    with pytest.raises(ValueError, match='cyclic'):
        scrape._nuxt_deref([[0, 0]], 0)
    pool = ['text']
    for i in range(1, 30): pool.append([i-1, i-1])
    resolved = scrape._nuxt_deref(pool, 29)
    assert resolved[0] is resolved[1]
    with pytest.raises(ValueError, match='budget'):
        scrape._nuxt_deref([[1]*20001, 'text'], 0)
    with pytest.raises(ValueError, match='budget'):
        scrape._nuxt_deref(['x'*(2*1024*1024+1)], 0)
    deep = [[i+1] for i in range(42)] + ['end']
    with pytest.raises(ValueError, match='budget'):
        scrape._nuxt_deref(deep, 0)


@pytest.mark.parametrize('status', [3, -1])
def test_nuxt_skips_article_metadata_before_real_article(status):
    pool = [None, {'data':2}, {'article-status':status, 'article-detail-123-tc':4},
            None, {'content':5}, '<p>Actual TVB article body.</p>']
    html = '<script id="__NUXT_DATA__">'+json.dumps(pool)+'</script>'
    assert 'Actual TVB article body.' in scrape._build_tvb_content(html)


def test_breaking_requires_independent_publishers_and_explicit_event_agreement():
    from datetime import datetime, timezone
    from src.breaking_alert import detect_breaking_clusters
    rows = [dict(id=str(i), cluster_id='topic', title='同一事件最新消息', source=name,
                 date=datetime.now(timezone.utc).isoformat(), score=5)
            for i, name in enumerate(['HK01 突發', 'HK01 社會', 'HK01 國際'])]
    assert detect_breaking_clusters(rows) == []
    for row, name in zip(rows, ['HK01 突發', '明報 本地', 'RTHK 本地']): row['source'] = name
    assert len(detect_breaking_clusters(rows)) == 1
    rows[-1]['title'] = '另一個不相干的事件'
    assert detect_breaking_clusters(rows) == []
