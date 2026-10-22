"""Publisher-supported public posts fixtures; no network or paid providers."""
import asyncio
import json
import ssl
from datetime import datetime, timedelta, timezone

import pytest

from src import fetch as F
from src.feeds import RSS_CLIENT_HEADERS, RSS_FEEDS
from src.scrape import _rss_fallback_content

FEED = {'name':'法庭線','url':'https://publisher.example/feed/',
        'api_url':'https://publisher.example/wp-json/wp/v2/posts',
        'fetcher':'wordpress_public','category':'網媒','headers':RSS_CLIENT_HEADERS}
CUTOFF = datetime(2026,10,9,tzinfo=timezone.utc)


def post(**changes):
    value={'id':123,'status':'publish','link':'https://publisher.example/%e6%96%b0%e8%81%9e/',
           'date_gmt':'2026-10-10T03:25:44','modified_gmt':'2026-10-12T07:00:00',
           'title':{'rendered':'公開報道 &amp; 日期'},
           'content':{'protected':False,'rendered':'<p>公開原文。</p><img src="https://publisher.example/photo.jpg" onerror="alert(1)"><script>bad()</script>'},
           'excerpt':{'protected':False,'rendered':'<p>公開摘要。</p>'}}
    value.update(changes);return value


def test_public_posts_preserve_canonical_id_gmt_date_and_public_body():
    rows=F._parse_wordpress_posts([post(),post()],FEED,CUTOFF)
    assert len(rows)==1
    article=rows[0]
    assert article['id']==F._make_id(post()['link'])
    assert article['url']==post()['link']
    assert article['date']=='2026-10-10T03:25:44+00:00'
    assert article['title']=='公開報道 & 日期'
    assert article['content'] is None and '公開原文' in article['rss_content']
    assert article['thumbnail']=='https://publisher.example/photo.jpg'
    assert _rss_fallback_content(article,fallback='rss-blocked',allow_minimal=True)
    assert '<script' not in article['content'] and 'onerror=' not in article['content']
    assert article['content_quality']['fallback']=='rss-blocked'


@pytest.mark.parametrize('change',[
    {'date_gmt':None},{'date_gmt':'2026-10-10'},{'date_gmt':'invalid'},
    {'link':'https://127.0.0.1/secret'}, {'link':'https://other.example/story'},
    {'link':'https://user:pass@publisher.example/story'},
    {'link':'https://publisher.example/wp-admin/'},
    {'link':'https://publisher.example/story/#duplicate'},
    {'link':'https://publisher.example:8080/story/'},
    {'link':None}, {'status':'private'}, {'status':'draft'},
    {'content':{'rendered':'missing privacy flag'},'excerpt':None},
])
def test_invalid_public_metadata_cannot_become_articles(change):
    with pytest.raises(ValueError,match='no valid published metadata'):
        F._parse_wordpress_posts([post(**change)],FEED,CUTOFF)


def test_protected_records_are_ignored_without_using_their_body():
    protected=post(content={'protected':True,'rendered':'do not use'},excerpt={'protected':False,'rendered':'preview'})
    assert F._parse_wordpress_posts([protected],FEED,CUTOFF)==[]
    assert F._parse_wordpress_posts([post(excerpt={'protected':True,'rendered':'secret'})],FEED,CUTOFF)==[]


def test_old_records_are_quiet_without_using_modified_time_or_now():
    assert F._parse_wordpress_posts([post(date_gmt='2026-10-08T23:59:59')],FEED,CUTOFF)==[]
    assert F._parse_wordpress_posts([],FEED,CUTOFF)==[]
    with pytest.raises(ValueError,match='not a posts list'):
        F._parse_wordpress_posts({'code':'rest_cannot_view'},FEED,CUTOFF)


def test_excerpt_only_and_featured_media_are_not_claimed_as_full_content():
    value=post(content=None,_embedded={'wp:featuredmedia':[{'media_type':'image','source_url':'https://publisher.example/cover.jpg'}]})
    row=F._parse_wordpress_posts([value],FEED,CUTOFF)[0]
    assert row['content'] is None and row['rss_content']=='<p>公開摘要。</p>'
    assert row['thumbnail']=='https://publisher.example/cover.jpg'


class Response:
    def __init__(self,status,body):self.status=status;self.body=body;self.headers={}
    async def read(self):return self.body
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass


class Session:
    def __init__(self,status,body):self.status=status;self.body=body;self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs));return Response(self.status,self.body)


def test_fetch_one_uses_one_view_request_no_paid_translation_or_fallback(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('paid title translation called')
    monkeypatch.setattr(F,'_translate_titles_minimax',forbidden)
    session=Session(200,json.dumps([post()],ensure_ascii=False).encode('utf-8'))
    rows,error,nm=asyncio.run(F._fetch_one(session,{**FEED,'name':'The Collective HK'},CUTOFF,{}))
    assert len(rows)==1 and error is None and not nm
    assert len(session.calls)==1
    url,kwargs=session.calls[0]
    assert 'context=view' in url and 'status=publish' in url
    assert kwargs['headers']['User-Agent'].startswith('rss-news/')
    assert kwargs['headers']['Accept']=='application/json' and not kwargs['allow_redirects']


@pytest.mark.parametrize('status,body,reason',[
    (401,b'authentication required','HTTP 401'),
    (403,b'<html>denied</html>','HTTP 403'),
    (406,b'not acceptable','HTTP 406'),
    (429,b'rate limited','HTTP 429'),
    (451,b'unavailable','HTTP 451'),
    (200,b'<!DOCTYPE html><html>challenge</html>','non-JSON'),
    (200,b'{"code":"rest_cannot_view"}','not a posts list'),
])
def test_denials_and_challenge_responses_remain_errors_without_retry(status,body,reason):
    session=Session(status,body)
    rows,error,nm=asyncio.run(F._fetch_one(session,FEED,CUTOFF,{}))
    assert rows==[] and reason in error and not nm
    assert len(session.calls)==1


def test_source_change_is_limited_to_two_official_publishers():
    feeds=[f for f in RSS_FEEDS if f.get('fetcher')=='wordpress_public']
    assert {f['name'] for f in feeds}=={'法庭線','The Collective HK'}
    for f in feeds:
        assert f['api_url'].endswith('/wp-json/wp/v2/posts')
        assert f['url'].endswith('/feed/') and f['rss_media']


def test_bounded_first_page_is_sorted_deduped_without_article_eligibility_cap():
    values=[post(link='https://publisher.example/older/',date_gmt='2026-10-10T01:00:00'),
            post(link='https://publisher.example/new/',date_gmt='2026-10-10T03:00:00'),
            post(link='https://publisher.example/middle/',date_gmt='2026-10-10T02:00:00')]
    rows=F._parse_wordpress_posts(values+values,{**FEED,'max_items':2},CUTOFF)
    assert [r['url'] for r in rows]==['https://publisher.example/new/','https://publisher.example/middle/','https://publisher.example/older/']
    with pytest.raises(ValueError,match='bounded page size'):
        F._parse_wordpress_posts([post()]*41,FEED,CUTOFF)
    session=Session(200,json.dumps([post()]).encode('utf-8'))
    asyncio.run(F._fetch_one(session,{**FEED,'max_items':1000},CUTOFF,{}))
    from urllib.parse import parse_qs,urlsplit
    query=parse_qs(urlsplit(session.calls[0][0]).query)
    assert query['page']==['1'] and query['per_page']==['40']
    assert len(session.calls)==1


@pytest.mark.parametrize('api_url',['https://127.0.0.1/wp-json/wp/v2/posts',
                                  'https://other.example/wp-json/wp/v2/posts',
                                  'https://publisher.example/wp-json/wp/v2/users',
                                  'https://publisher.example/wp-json/wp/v2/posts?context=edit',
                                  'https://publisher.example/wp-json/wp/v2/posts?password=secret',
                                  'https://publisher.example/wp-json/wp/v2/posts#fragment'])
def test_api_target_cannot_leave_public_configured_posts_route(api_url):
    session=Session(200,b'[]')
    rows,error,nm=asyncio.run(F._fetch_one(session,{**FEED,'api_url':api_url},CUTOFF,{}))
    assert rows==[] and error and not nm and session.calls==[]


def test_json_chinese_does_not_depend_on_windows_default_codepage(monkeypatch):
    import locale
    monkeypatch.setattr(locale,'getpreferredencoding',lambda *args:'cp1252')
    if hasattr(locale,'getencoding'):
        monkeypatch.setattr(locale,'getencoding',lambda:'cp1252')
    session=Session(200,json.dumps([post()],ensure_ascii=False).encode('utf-8'))
    rows,error,_=asyncio.run(F._fetch_one(session,FEED,CUTOFF,{}))
    assert error is None and rows[0]['title']=='公開報道 & 日期'


@pytest.mark.parametrize('configured,expected', [(0, 1), (-2, 1), (1000, 40)])
def test_request_page_size_does_not_limit_valid_response_articles(configured, expected):
    feed = {**FEED, 'max_items': configured}
    assert F._wordpress_item_limit(feed) == expected
    rows = F._parse_wordpress_posts([post(), post(link='https://publisher.example/other/')], feed, CUTOFF)
    assert len(rows) == 2
    session = Session(200, b'[]')
    asyncio.run(F._fetch_one(session, feed, CUTOFF, {}))
    assert f'per_page={expected}' in session.calls[0][0]


@pytest.mark.parametrize('configured,expected', [(0, 1), (-10, 1), (1000, 15)])
def test_public_request_timeout_is_positive_and_bounded(configured, expected):
    session = Session(200, b'[]')
    asyncio.run(F._fetch_one(session, {**FEED, 'request_timeout': configured}, CUTOFF, {}))
    assert session.calls[0][1]['timeout'].total == expected


def test_future_dates_and_fragment_duplicates_never_become_articles():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert F._parse_wordpress_posts([post(date_gmt=future)], FEED, CUTOFF) == []
    rows = F._parse_wordpress_posts([post(), post(link=post()['link'] + '#different')], FEED, CUTOFF)
    assert len(rows) == 1 and rows[0]['id'] == F._make_id(post()['link'])


def test_private_media_url_is_ignored_and_public_excerpt_is_sanitised():
    value = post(content=None, excerpt={'protected': False, 'rendered': '<p>公開摘要。</p><script>bad()</script>'},
                 _embedded={'wp:featuredmedia': [{'media_type': 'image', 'source_url': 'http://127.0.0.1/private'}]})
    article = F._parse_wordpress_posts([value], FEED, CUTOFF)[0]
    assert article['thumbnail'] is None
    assert _rss_fallback_content(article, fallback='rss-blocked', allow_minimal=True)
    assert '<script' not in article['content']
    assert article['content_quality']['fallback'] == 'rss-blocked'


def test_tls_failure_is_not_retried_or_routed_elsewhere():
    class TLSFailure(Session):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise ssl.SSLCertVerificationError('certificate validation failed')
    session = TLSFailure(200, b'[]')
    rows, error, _ = asyncio.run(F._fetch_one(session, FEED, CUTOFF, {}))
    assert rows == [] and 'SSLCertVerificationError' in error and len(session.calls) == 1


def test_private_redirect_is_rejected_before_second_request():
    class PrivateRedirect(Session):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            response = Response(302, b'')
            response.headers = {'Location': 'http://127.0.0.1/private'}
            return response
    session = PrivateRedirect(302, b'')
    rows, error, _ = asyncio.run(F._fetch_one(session, FEED, CUTOFF, {}))
    assert rows == [] and 'UnsafeDestination' in error and len(session.calls) == 1


def test_source_statistics_disclose_collection_route_without_overwriting_rss(monkeypatch):
    async def fetched(*args):
        return F._parse_wordpress_posts([post()], FEED, CUTOFF), None, False
    monkeypatch.setattr(F, 'RSS_FEEDS', [FEED])
    monkeypatch.setattr(F, '_fetch_one_capped', fetched)
    monkeypatch.setattr(F, '_load_feed_http_cache', lambda: {})
    monkeypatch.setattr(F, '_save_feed_http_cache', lambda data: None)
    rows, stats = asyncio.run(F.fetch_all())
    source = stats[FEED['name']]
    assert len(rows) == 1
    assert source['feed_url'] == FEED['url']
    assert source['collection_url'] == FEED['api_url']
    assert source['collection_method'] == 'wordpress-public-posts'
    assert source['collection_scope'] == 'public-posts-window' and source['collection_limit'] is None
    assert source['collection_page_size'] == 30 and source['collection_request_limit'] == 3
