"""Time-window intake and bounded published continuation; no live providers."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from src import fetch as F


NOW = datetime.now(timezone.utc)
CUTOFF = NOW - timedelta(hours=30)
WP = {'name':'Publisher','category':'網媒','url':'https://publisher.example/feed/',
      'api_url':'https://publisher.example/wp-json/wp/v2/posts','fetcher':'wordpress_public','max_items':2}


def post(aid, date=None):
    return {'id':aid,'link':f'https://publisher.example/story-{aid}/','status':'publish',
            'date_gmt':(date or NOW).replace(tzinfo=None).isoformat(timespec='seconds'),
            'title':{'rendered':f'公開新聞 {aid}'},
            'content':{'rendered':'<p>公開正文。</p>','protected':False}}


class Response:
    charset = 'utf-8'
    def __init__(self, data, status=200, headers=None):
        self.raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.status = status
        self.headers = headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def read(self): return self.raw


def fake_public(monkeypatch, response):
    calls=[]
    def get(session,url,**kwargs):
        calls.append((url,kwargs))
        return response(url,kwargs)
    monkeypatch.setattr(F,'public_get',get)
    async def read(resp): return resp.raw
    monkeypatch.setattr(F,'read_public_body',read)
    return calls


def resume(kind='page', **values):
    return {'version':1,'kind':kind,'checked_at':NOW.isoformat(),**values}


def test_wordpress_advances_published_continuation_instead_of_repeating_first_cap(monkeypatch):
    calls=fake_public(monkeypatch,lambda url,kwargs:Response([
        post(int(parse_qs(urlsplit(url).query)['page'][0])*10+i) for i in range(2)]))
    feed=dict(WP)
    first,error,_=asyncio.run(F._fetch_wordpress_public(None,feed,CUTOFF))
    assert not error and len(first)==6 > feed['max_items']
    assert [parse_qs(urlsplit(u).query)['page'][0] for u,_ in calls]==['1','2','3']
    assert feed['_intake']['resume']['next_page']==4
    assert feed['_intake']['continuation_pending'] is True
    assert feed['_intake']['coverage']=='incomplete'
    second_feed={**WP,'_resume':feed['_intake']['resume']}
    second,error,_=asyncio.run(F._fetch_wordpress_public(None,second_feed,CUTOFF))
    assert not error and len(second)==6
    assert [parse_qs(urlsplit(u).query)['page'][0] for u,_ in calls[3:]]==['1','4','5']
    assert second_feed['_intake']['resume']['next_page']==6


def test_wordpress_old_pinned_item_does_not_end_scan_and_empty_page_closes_index(monkeypatch):
    pages={1:[post(1,NOW-timedelta(days=3)),post(2)],2:[post(3),post(4)],3:[]}
    calls=fake_public(monkeypatch,lambda url,kwargs:Response(pages[int(parse_qs(urlsplit(url).query)['page'][0])]))
    feed=dict(WP)
    rows,error,_=asyncio.run(F._fetch_wordpress_public(None,feed,CUTOFF))
    assert not error and {r['publisher_article_id'] for r in rows}=={'2','3','4'}
    assert len(calls)==3 and feed['_intake']['pagination_complete'] is True
    assert feed['_intake']['completion_basis']=='empty-official-page'


@pytest.mark.parametrize('head_count',[1,2])
def test_wordpress_entire_old_pinned_head_does_not_hide_fresh_continuation(monkeypatch,head_count):
    pages={1:[post(i,NOW-timedelta(days=3)) for i in range(head_count)],
           2:[post(20),post(21)],3:[]}
    calls=fake_public(monkeypatch,lambda url,kwargs:Response(pages[int(parse_qs(urlsplit(url).query)['page'][0])]))
    feed=dict(WP)
    rows,error,_=asyncio.run(F._fetch_wordpress_public(None,feed,CUTOFF))
    assert not error and {row['publisher_article_id'] for row in rows}=={'20','21'}
    assert [parse_qs(urlsplit(url).query)['page'][0] for url,_ in calls]==['1','2','3']
    assert feed['_intake']['coverage']=='provided-index-only'
    assert feed['_intake']['completion_basis']=='empty-official-page'


def test_wordpress_partial_failure_keeps_rows_and_does_not_advance_failed_page(monkeypatch):
    fake_public(monkeypatch,lambda url,kwargs:Response([post(1),post(2)])
                if parse_qs(urlsplit(url).query)['page']==['1'] else Response({},403))
    feed=dict(WP)
    rows,error,_=asyncio.run(F._fetch_wordpress_public(None,feed,CUTOFF))
    assert len(rows)==2 and '403' in error
    assert feed['_intake']['resume']['next_page']==2
    assert feed['_intake']['coverage']=='incomplete'


def test_wordpress_total_timeout_is_shared_across_pages(monkeypatch):
    clock=[0.0]
    monkeypatch.setattr(F.time,'monotonic',lambda:clock[0])
    def response(url,kwargs):
        clock[0]+=6
        page=int(parse_qs(urlsplit(url).query)['page'][0])
        return Response([post(page*10+i) for i in range(2)])
    calls=fake_public(monkeypatch,response)
    asyncio.run(F._fetch_wordpress_public(None,dict(WP),CUTOFF))
    assert [kwargs['timeout'].total for _,kwargs in calls]==[15,9,3]


@pytest.mark.parametrize('bad',[-1,0,True,513,100000,'https://127.0.0.1/'])
def test_untrusted_resume_cannot_choose_a_host_or_unbounded_page(bad):
    feed={**WP,'_resume':resume(next_page=bad,url='https://127.0.0.1/')}
    assert F._continuation_pages(feed)==[1,2,3]


def test_expired_or_future_resume_restarts_safely():
    for checked in (NOW-timedelta(hours=31),NOW+timedelta(hours=1)):
        feed={**WP,'_resume':resume(next_page=50,checked_at=checked.isoformat())}
        assert F._continuation_pages(feed)==[1,2,3]
    for invalid in (None,100,[],{}):
        assert F._continuation_pages({**WP,'_resume':resume(next_page=50,checked_at=invalid)})==[1,2,3]


def test_invalid_published_state_never_breaks_collection(monkeypatch,tmp_path):
    monkeypatch.setattr(F,'_FEED_CACHE_PATH',tmp_path/'feed_http_cache.json')
    for value in ([],None,{'sources':[]}):
        (tmp_path/'articles.json').write_text(json.dumps(value))
        assert F._load_collection_resume()=={}


def test_now_uses_official_page_number_and_resumes_after_three_requests(monkeypatch):
    def response(url,kwargs):
        q=parse_qs(urlsplit(url).query)
        assert 'page' not in q
        page=int(q['pageNo'][0])
        return Response([{'newsId':str(page*10+i),'title':'最新新聞',
                         'publishDate':int(NOW.timestamp()*1000)} for i in range(2)])
    calls=fake_public(monkeypatch,response)
    feed={'name':'Now','category':'新聞','max_items':1,
          'url':'https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=119&pageNo=1&pageSize=30'}
    rows,error,_=asyncio.run(F._fetch_nowtv(None,feed,CUTOFF))
    assert not error and len(rows)==6
    assert feed['_intake']['resume']['next_page']==4
    next_feed={k:v for k,v in feed.items() if not k.startswith('_')}
    next_feed['_resume']=feed['_intake']['resume']
    asyncio.run(F._fetch_nowtv(None,next_feed,CUTOFF))
    assert [parse_qs(urlsplit(u).query)['pageNo'][0] for u,_ in calls[3:]]==['1','4','5']


def test_now_entire_old_pinned_head_does_not_hide_fresh_continuation(monkeypatch):
    old={'newsId':'old','title':'舊置頂稿','publishDate':int((NOW-timedelta(days=3)).timestamp()*1000)}
    fresh={'newsId':'new','title':'最新報道','publishDate':int(NOW.timestamp()*1000)}
    pages={1:[old],2:[fresh],3:[]}
    calls=fake_public(monkeypatch,lambda url,kwargs:Response(pages[int(parse_qs(urlsplit(url).query)['pageNo'][0])]))
    feed={'name':'Now','category':'新聞',
          'url':'https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=119&pageNo=1&pageSize=30'}
    rows,error,_=asyncio.run(F._fetch_nowtv(None,feed,CUTOFF))
    assert not error and len(rows)==1 and rows[0]['title']=='最新報道'
    assert [parse_qs(urlsplit(url).query)['pageNo'][0] for url,_ in calls]==['1','2','3']
    assert feed['_intake']['coverage']=='provided-index-only'
    assert feed['_intake']['completion_basis']=='empty-official-page'


def test_published_resume_is_read_only_and_bound_to_configured_feed(monkeypatch,tmp_path):
    monkeypatch.setattr(F,'_FEED_CACHE_PATH',tmp_path/'feed_http_cache.json')
    monkeypatch.setattr(F,'RSS_FEEDS',[WP])
    state={'sources':{WP['name']:{'feed_url':WP['url'],'intake':{'resume':resume(next_page=12)}}}}
    path=tmp_path/'articles.json'; path.write_text(json.dumps(state)); original=path.read_bytes()
    captured=[]
    async def fetch(session,feed,cutoff,cache):
        captured.append(feed.get('_resume'));return [],None,False
    monkeypatch.setattr(F,'_fetch_one_capped',fetch)
    asyncio.run(F.fetch_all())
    assert captured==[state['sources'][WP['name']]['intake']['resume']]
    assert path.read_bytes()==original
    state['sources'][WP['name']]['feed_url']='https://different.example/feed/'
    path.write_text(json.dumps(state));asyncio.run(F.fetch_all())
    assert captured[-1] is None


def test_yahoo_request_budget_rotates_tail_and_keeps_original_candidate_urls(monkeypatch):
    urls=[f'https://hk.news.yahoo.com/article-{100000+i}.html' for i in range(20)]
    listing=''.join(f'<a href="{u}">新聞</a>' for u in urls)
    html=f'<h1>新聞</h1><time datetime="{NOW.isoformat()}"></time>'
    def response(url,kwargs):return Response(listing.encode() if url.endswith('/tech/') else html.encode())
    calls=fake_public(monkeypatch,response)
    feed={'name':'Yahoo 科技','category':'科技','url':'https://hk.news.yahoo.com/tech/',
          'metadata_request_budget':12}
    first,error,_=asyncio.run(F._fetch_yahoo_tech(None,feed,CUTOFF))
    assert not error and len(first)==12
    assert all(kwargs['max_field_size']==65536 for _,kwargs in calls)
    next_feed={k:v for k,v in feed.items() if not k.startswith('_')}
    next_feed['_resume']=feed['_intake']['resume']
    second,error,_=asyncio.run(F._fetch_yahoo_tech(None,next_feed,CUTOFF))
    assert not error and urls[12] in {a['url'] for a in second}
    assert feed['_intake']['continuation_pending'] is True
    assert sum(i['status']=='pending' for i in feed['_intake']['items'])==8


def test_yahoo_oversized_official_headers_use_finite_actual_http_parser_budget():
    from aiohttp.http_parser import HttpResponseParser
    class Protocol:
        _reading_paused=False
        def pause_reading(self):pass
        def resume_reading(self):pass
    class ParsedResponse:
        charset='utf-8'
        def __init__(self,message,payload):
            self.status=message.code;self.headers=message.headers;self.content=payload
        async def __aenter__(self):return self
        async def __aexit__(self,*args):return False
    class Session:
        def get(self,url,**kwargs):
            assert kwargs['allow_redirects'] is False
            body=(b'<a href="https://hk.finance.yahoo.com/news/story-100000.html">news</a>'
                  if url.endswith('/tech/') else f'<h1>公開新聞</h1><time datetime="{NOW.isoformat()}"></time>'.encode())
            parser=HttpResponseParser(Protocol(),asyncio.get_running_loop(),65536,
                                      max_field_size=kwargs.get('max_field_size',8190))
            raw=b'HTTP/1.1 200 OK\r\nContent-Security-Policy: '+b'x'*21500+b'\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
            parsed=parser.feed_data(raw)[0][0]
            return ParsedResponse(*parsed)
    rows,error,_=asyncio.run(F._fetch_yahoo_tech(Session(),{
        'name':'Yahoo 科技','url':'https://hk.news.yahoo.com/tech/','category':'科技'},CUTOFF))
    assert not error and len(rows)==1
    assert rows[0]['url']=='https://hk.finance.yahoo.com/news/story-100000.html'


def test_yahoo_cancelled_batch_remains_pending_and_only_completed_progress_advances(monkeypatch):
    urls=[f'https://hk.news.yahoo.com/article-{100000+i}.html' for i in range(20)]
    listing=''.join(f'<a href="{u}">新聞</a>' for u in urls)
    html=f'<h1>新聞</h1><time datetime="{NOW.isoformat()}"></time>'
    def response(url,kwargs):
        value=Response(listing.encode() if url.endswith('/tech/') else html.encode())
        value.block=url in urls[10:]
        return value
    fake_public(monkeypatch,response)
    async def read(value):
        if value.block: await asyncio.Event().wait()
        return value.raw
    monkeypatch.setattr(F,'read_public_body',read)
    feed={'name':'Yahoo 科技','category':'科技','url':'https://hk.news.yahoo.com/tech/',
          'metadata_request_budget':12}
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(F._fetch_yahoo_tech(None,feed,CUTOFF),timeout=0.04))
    assert len(feed['_partial_batch'])==10
    assert feed['_intake']['resume']['offset']==4
    assert sum(row['status']=='pending' for row in feed['_intake']['items'])==10
    assert feed['_intake']['continuation_pending'] is True


def test_expanded_english_metadata_does_not_expand_title_calls_or_retranslate_same_cycle(monkeypatch):
    entries=''.join(f'<item><title>English title {i}</title><link>https://example.com/{i}</link><pubDate>{NOW.strftime("%a, %d %b %Y %H:%M:%S GMT")}</pubDate></item>' for i in range(6))
    async def read(*args,**kwargs):return 200,('<rss version="2.0"><channel>'+entries+'</channel></rss>').encode(),{}
    calls=[]
    async def translate(session,titles):calls.append(list(titles));return titles
    monkeypatch.setattr(F,'_read_feed_with_tls_fallback',read)
    monkeypatch.setattr(F,'_translate_titles_minimax',translate)
    monkeypatch.setattr(F,'MINIMAX_API_KEY','offline-mock')
    feed={'name':'HuffPost 新聞','url':'https://example.com/feed','category':'外媒','max_items':2}
    monkeypatch.setattr(F,'RSS_FEEDS',[feed])
    rows,error,_=asyncio.run(F._fetch_one(None,dict(feed),CUTOFF,{}))
    assert not error and len(rows)==6 and len(calls[0])==2
    asyncio.run(F.retranslate_english_titles(rows))
    assert len(calls)==1
    assert sum(a.get('title_translation_status')=='pending-budget' for a in rows)==4
    assert all('_title_translation_skip_cycle' not in a and '_title_translation_attempted' not in a for a in rows)


def test_retranslate_old_raw_titles_respects_legacy_per_source_budget(monkeypatch):
    monkeypatch.setattr(F,'MINIMAX_API_KEY','offline-mock')
    monkeypatch.setattr(F,'RSS_FEEDS',[{'name':'HuffPost 新聞','max_items':2}])
    calls=[]
    async def translate(session,titles):calls.extend(titles);return ['譯文']*len(titles)
    monkeypatch.setattr(F,'_translate_titles_minimax',translate)
    rows=[{'id':str(i),'source':'HuffPost 新聞','title':f'English {i}'} for i in range(5)]
    asyncio.run(F.retranslate_english_titles(rows))
    assert len(calls)==2
    assert [a['title'] for a in rows[:2]]==['譯文','譯文']
    assert all(a['title_translation_status']=='pending-budget' for a in rows[2:])
