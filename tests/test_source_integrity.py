"""Source integrity counterexamples from the October 22 review. No live calls."""
import asyncio
import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
import pytest

import build
from src import fetch, scrape
from src.media_fields import image_url
from src.publisher_identity import identity_key, reconcile


def article(aid, **fields):
    return dict(id=aid, url=f'https://example.com/{aid}', source='Source', category='新聞',
                date=datetime.now(timezone.utc).isoformat(), **fields)


def test_successful_partial_source_batch_preserves_all_in_window_old_articles(monkeypatch):
    a, b, c = [article(x) for x in 'abc']
    expired = article('expired')
    expired['date'] = (datetime.now(timezone.utc) - timedelta(hours=31)).isoformat()
    a['fetched_at'] = '2026-10-21T00:00:00+00:00'
    monkeypatch.setattr(build, '_load_old_content_record', lambda aid: {'content': f'<p>{aid}</p>'})
    stats = {'Source': {'count': 1, 'fetch_status':'success', 'observed_at': '2026-10-22T12:00:00+00:00'}}
    merged = build._merge_missing_sources([c], [a, b, expired], stats)
    assert {row['id'] for row in merged} == {'a', 'b', 'c'}
    assert next(row for row in merged if row['id'] == 'a')['fetched_at'] == a['fetched_at']
    assert stats['Source']['effective_count'] == 3
    assert 'content' not in a  # Never mutate the fallback snapshot itself.


def test_304_keeps_article_clock_even_when_history_has_left_the_provided_feed(monkeypatch):
    old = article('outside-current-feed', fetched_at='2026-10-21T19:00:00+00:00')
    monkeypatch.setattr(build, '_load_old_content_record', lambda aid: None)
    rows = build._merge_missing_sources([], [old], {'Source':{'fetch_status':'not_modified','count':0,'observed_at':'2026-10-22T11:00:00+00:00'}})
    assert rows[0]['fetched_at'] == old['fetched_at']


def test_slug_correction_preserves_legacy_id_aliases_and_memberships():
    old = article('legacy_saved_id')
    old['url'] = 'https://news.tvb.com/tc/1202195-洽溝'
    corrected = dict(old, id='new_url_hash', url='https://news.tvb.com/tc/1202195-洽購', title='正確標題')
    row, = reconcile([corrected], [old])
    assert row['id'] == 'legacy_saved_id'
    assert row['publisher_article_id'] == '1202195'
    assert row['id_aliases'] == ['new_url_hash']
    assert set(row['url_aliases']) == {old['url'], corrected['url']}
    other = dict(corrected, source='Other feed', category='科技')
    row, = reconcile([corrected, other], [old])
    assert {m['source'] for m in row['memberships']} == {'Source', 'Other feed'}
    assert identity_key(old) == identity_key(corrected)
    previous_revision = dict(old, id='another_saved_id', url='https://news.tvb.com/tc/1202195-earlier-slug')
    row, = reconcile([corrected], [old,previous_revision])
    assert set(row['id_aliases']) == {'new_url_hash','another_saved_id'}
    assert set(row['url_aliases']) == {old['url'],previous_revision['url'],corrected['url']}


def test_unknown_query_parameters_still_distinguish_articles():
    assert identity_key(article('a') | {'url':'https://example.com/image?id=one'}) != identity_key(article('b') | {'url':'https://example.com/image?id=two'})
    assert identity_key(article('a') | {'url':'https://example.com/a?utm_source=rss'}) == identity_key(article('b') | {'url':'https://example.com/a'})
    assert identity_key(article('a') | {'url':'https://example.com/a%2Fb'}) != identity_key(article('b') | {'url':'https://example.com/a/b'})


def test_new_publisher_identity_is_stable_without_previous_slug():
    a = article('hash-a') | {'url':'https://www.hk01.com/社會新聞/60402566/old'}
    b = dict(a, id='hash-b', url='https://www.hk01.com/社會新聞/60402566/new')
    assert reconcile([a])[0]['id'] == reconcile([b])[0]['id']


def test_rss_time_window_keeps_all_valid_items_after_old_pinned_and_duplicates(monkeypatch):
    entries = [('bad', 'javascript:bad()', 'bad'), ('old', 'https://example.com/old', 'Wed, 21 Oct 2026 00:00:00 GMT'),
               ('one', 'https://example.com/one', 'Thu, 22 Oct 2026 10:00:00 GMT'),
               ('one duplicate', 'https://example.com/one', 'Thu, 22 Oct 2026 10:00:00 GMT'),
               ('two', 'https://example.com/two', 'Thu, 22 Oct 2026 10:00:00 GMT'),
               ('three', 'https://example.com/three', 'Thu, 22 Oct 2026 10:00:00 GMT')]
    raw = ('<rss version="2.0"><channel>' + ''.join(f'<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate></item>' for t,u,d in entries) + '</channel></rss>').encode()
    async def read(*args, **kwargs):
        return 200, raw, {'ETag':'proposed'}
    monkeypatch.setattr(fetch, '_read_feed_with_tls_fallback', read)
    info = dict(name='Source', url='https://example.com/feed', category='新聞', max_items=2)
    rows, error, modified = asyncio.run(fetch._fetch_one(None, info, datetime(2026,10,22,tzinfo=timezone.utc), {}))
    assert not error and not modified
    assert [a['title'] for a in rows] == ['one', 'two', 'three']
    items = info['_intake']['items']
    assert len(items) == len(entries)
    assert [sum(i['status'] == s for i in items) for s in ('accepted','excluded','pending')] == [3,3,0]
    assert info['_intake']['coverage'] == 'provided-feed-only'


def test_rss_invalid_200_does_not_commit_etag_and_orphan_304_is_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, '_FEED_CACHE_PATH', tmp_path/'feed_http_cache.json')
    async def invalid(*args, **kwargs):
        return 200, b'<rss><channel><item><link>javascript:bad()</link></item></channel></rss>', {'ETag':'poison'}
    monkeypatch.setattr(fetch, '_read_feed_with_tls_fallback', invalid)
    info = dict(name='Source', url='https://example.com/feed', category='新聞')
    cache = {}
    assert asyncio.run(fetch._fetch_one(None, info, datetime.now(timezone.utc), cache))[1]
    assert not cache
    async def not_modified(*args, **kwargs):
        return 304, b'', {}
    monkeypatch.setattr(fetch, '_read_feed_with_tls_fallback', not_modified)
    cache[info['url']] = {'etag':'old'}
    rows, error, modified = asyncio.run(fetch._fetch_one(None, info, datetime.now(timezone.utc), cache))
    assert not rows and 'without a materialized' in error and not modified and not cache


class Response:
    charset = 'utf-8'
    def __init__(self, data, status=200):
        self.raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status = status
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


def fake_public(monkeypatch, responses):
    calls = []
    def get(session, url, **kwargs):
        calls.append(url)
        return responses[len(calls)-1]
    async def read(response): return response.raw
    monkeypatch.setattr(fetch, 'public_get', get)
    monkeypatch.setattr(fetch, 'read_public_body', read)
    return calls


def test_now_schema_error_is_not_successful_empty_feed(monkeypatch):
    fake_public(monkeypatch, [Response({'error':'try later'})])
    info = dict(name='Now', url='https://news.now.com/api/list?page=1', category='新聞')
    assert 'schema' in asyncio.run(fetch._fetch_nowtv(None, info, datetime.now(timezone.utc)))[1]


def test_now_partial_second_page_failure_keeps_first_page_and_gap(monkeypatch):
    item = dict(newsId='123', title='新聞', publishDate=1792663200000)
    calls = fake_public(monkeypatch, [Response([item]), Response({},500)])
    info = dict(name='Now', url='https://news.now.com/api/list?page=1', category='新聞', max_items=2)
    rows, error, _ = asyncio.run(fetch._fetch_nowtv(None, info, datetime(2026,10,22,tzinfo=timezone.utc)))
    assert len(rows) == 1 and '500' in error
    assert len(calls) == 2 and 'page=2' in calls[1]
    assert info['_intake']['coverage'] == 'incomplete'


def test_tvb_invalid_sitemap_reports_error(monkeypatch):
    fake_public(monkeypatch, [Response(b'<html>challenge or broken XML')])
    info = dict(name='TVB', url='https://news.tvb.com/sitemap.xml', category='新聞')
    assert 'schema' in asyncio.run(fetch._fetch_tvb(None, info, datetime.now(timezone.utc)))[1]


def test_oncc_thirty_hour_window_includes_third_hkt_date_and_partial_error(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026,10,22,17,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(fetch, 'datetime', Clock)
    calls = fake_public(monkeypatch, [Response([],500), Response([]), Response([])])
    info = dict(name='東網 娛樂', url='https://hk.on.cc/', category='娛樂')
    _, error, _ = asyncio.run(fetch._fetch_oncc_daily(None, info, datetime(2026,10,21,11,tzinfo=timezone.utc)))
    assert len(calls) == 3 and '20261021' in calls[-1] and '500' in error


def test_oncc_expired_invalid_and_duplicate_items_have_ledger_dispositions(monkeypatch):
    current = dict(articleId='bkn-20261022120000000-1022_00862_001',title='新聞')
    expired = dict(articleId='bkn-20261019120000000-1019_00862_001',title='舊稿')
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None): return datetime(2026,10,22,12,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(fetch,'datetime',Clock)
    fake_public(monkeypatch,[Response([current,expired,None]),Response([current])])
    info = dict(name='東網 娛樂',url='https://hk.on.cc/',category='娛樂')
    rows,error,_ = asyncio.run(fetch._fetch_oncc_daily(None,info,datetime(2026,10,21,6,tzinfo=timezone.utc)))
    assert not error and len(rows) == 1
    items=info['_intake']['items']
    assert len(items) == 4 and sum(row['status']=='accepted' for row in items) == 1
    assert {i['reason'] for i in items} >= {'expired-or-invalid-date','invalid-record','duplicate-publisher-item'}


@pytest.mark.parametrize('parser,wrapper', [
    (scrape._build_yahoo_content, '<section class="module-article-body"><div class="atoms">{}</div></section>'),
    (scrape._build_lookmedia_content, '<div class="entry-content">{}</div>'),
])
def test_figures_preserve_caption_credit_order_mixed_text_and_repeated_occurrences(parser, wrapper):
    source = '<p>正文開始，保留完整文字及圖片前後次序，不能因為內嵌圖片而丟失內容。甲<img src="https://example.com/mixed.jpg">乙</p>'
    source += '<figure><img src="https://example.com/same.jpg" alt="0"><figcaption>圖片來源：官方圖片</figcaption></figure>'
    source += '<p>再一次引用同圖，圖說有不同用途。</p><figure><img src="https://example.com/same.jpg" alt="0"><figcaption>圖片來源：TVB</figcaption></figure>'
    result = BeautifulSoup(parser(wrapper.format(source)), 'html.parser')
    assert [c.text for c in result.find_all('figcaption')] == ['圖片來源：官方圖片','圖片來源：TVB']
    assert len(result.find_all('img')) == 3
    assert '甲' in result.text and '乙' in result.text
    assert [tag.name for tag in result.select('figure > *')] == ['img','figcaption','img','figcaption']


@pytest.mark.parametrize('parser,wrapper,url', [
    (scrape._build_yahoo_content,'<section class="module-article-body"><div class="atoms">{}</div></section>','https://hk.news.yahoo.com/news/story'),
    (scrape._build_lookmedia_content,'<div class="entry-content">{}</div>','https://www.gotrip.hk/travel/story/'),
])
def test_article_builders_resolve_relative_images_against_requested_publisher(parser,wrapper,url):
    source='<p>正文包含兩張相對網址配圖，需要使用來源文章網址作為基底，不能因為網址未有 scheme 而刪除。</p><img src="//cdn.example.com/photo"><img src="/images/local.jpg">'
    parsed=BeautifulSoup(parser(wrapper.format(source),url),'html.parser')
    images=[i['src'] for i in parsed.find_all('img')]
    assert images[0] == 'https://cdn.example.com/photo'
    assert images[1].startswith(url.split('/news/')[0].split('/travel/')[0]+'/images/')


def test_hero_figure_is_retained_and_different_assets_never_match_by_basename():
    html = '<figure><img src="https://example.com/story-photo.jpg"><figcaption>廣華醫院。(資料圖片)</figcaption></figure>'
    assert build.remove_duplicate_leading_thumbnail(html, 'https://example.com/story-photo.jpg') == (html, False)
    assert not build._images_match('https://a.example.com/story-photo.jpg','https://b.example.com/story-photo.jpg')
    assert not build._images_match('https://example.com/image?id=one','https://example.com/image?id=two')


def test_news_about_helpline_is_not_deleted_but_known_contact_footer_is():
    html = '<p>政府公布情緒通服務使用量，議員要求交代成效。</p><p>情緒通：18111</p>'
    # A short service number alone is ambiguous and is retained conservatively.
    assert '政府公布情緒通' in scrape._strip_suicide_helpline(html)
    html = '<p>政府公布情緒通服務使用量，議員要求交代成效。</p><p>生命熱線：2382 0000</p>'
    output = scrape._strip_suicide_helpline(html)
    assert '政府公布情緒通' in output and '2382' not in output
    news = '<p>情緒通去年接到12345678宗查詢，議員要求交代服務使用情況。</p>'
    assert BeautifulSoup(scrape._strip_suicide_helpline(news),'html.parser').get_text() == BeautifulSoup(news,'html.parser').get_text()


def test_known_weekend_placeholder_is_rejected_without_generic_default_image_guessing():
    assert image_url('https://www.weekendhk.com/wp-content/themes/theme-content/images/default.jpg') is None
    assert image_url('https://example.com/theme-content/images/default.jpg') is not None


def test_hk01_payload_identity_mismatch_refuses_unrelated_content():
    data = {'props': {'initialProps': {'pageProps': {'article': {'articleId':999, 'description':'Wrong article'}}}}}
    html = '<script id="__NEXT_DATA__">' + json.dumps(data) + '</script>'
    with pytest.raises(scrape.SourceIdentityMismatch):
        scrape._build_hk01_content(html, 'https://www.hk01.com/社會新聞/123/requested')


def test_tvb_selects_requested_identity_and_preserves_repeated_image_occurrences():
    pool = [['ShallowReactive',1], {'data':2}, ['ShallowReactive',3],
            {'article-detail-999-tc':4,'article-detail-123-tc':6},
            {'content':5}, '<p>Wrong related article</p>', {'content':7},
            '<p>Correct article</p><img src="https://example.com/same.jpg"><p>Again</p><img src="https://example.com/same.jpg">']
    html = '<script id="__NUXT_DATA__">'+json.dumps(pool)+'</script>'
    body = scrape._build_tvb_content(html,'https://news.tvb.com/tc/123-requested')
    assert 'Wrong' not in body and 'Correct' in body
    assert len(BeautifulSoup(body,'html.parser').find_all('img')) == 2
    with pytest.raises(scrape.SourceIdentityMismatch):
        scrape._build_tvb_content(html,'https://news.tvb.com/tc/555-absent')


@pytest.mark.parametrize('sample', json.loads((Path(__file__).parent/'fixtures/source_figures_20261022.json').read_text(encoding='utf-8')), ids=lambda row: row['source'])
def test_public_source_figure_fixture_retains_its_caption_after_its_image(sample):
    prose = '<p>這是測試自行撰寫的前置段落，用來檢查圖片、可見圖說及 credit 的閱讀次序，並非原站報道內容。</p>'
    if sample['source'] == 'Yahoo 科技':
        html = '<section class="module-article-body"><div class="atoms">' + prose + sample['figure'] + '</div></section>'
        body = scrape._build_yahoo_content(html)
    else:
        body = scrape._build_lookmedia_content('<div class="entry-content">'+prose+sample['figure']+'</div>')
    parsed = BeautifulSoup(body, 'html.parser')
    assert [node.name for node in parsed.find_all(['img','figcaption'])] == ['img','figcaption']
    assert parsed.find('figcaption').get_text(' ',strip=True) == sample['caption']
    assert parsed.find('figcaption').find_parent('figure') is parsed.find('img').find_parent('figure')
