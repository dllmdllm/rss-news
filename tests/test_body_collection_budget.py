"""Expanded metadata never expands the bounded body-fetch workload."""
import asyncio
import copy
import hashlib
from contextlib import asynccontextmanager

import pytest

from src import scrape


def article(aid, **values):
    row = {'id':aid,'source':'Test source','title':f'Title {aid}',
           'url':f'https://example.com/{aid}','thumbnail':None,
           'rss_content':f'<p>RSS summary {aid}.</p>'}
    row.update(values)
    return row


def cached(aid):
    body = '<p>Previously verified complete body.</p>'
    return article(aid,content=body,content_hash=hashlib.sha256(body.encode()).hexdigest(),
                   content_quality={'fallback':'none','completeness':'extracted'})


def successful_network(monkeypatch):
    calls = []
    async def fetch(session,url):
        calls.append(url)
        return '<article><p>Actual article body.</p></article>'
    monkeypatch.setattr(scrape,'_fetch_html',fetch)
    monkeypatch.setattr(scrape,'_process_html_sync',lambda *args:('<p>Actual article body.</p>',None))
    return calls


def test_body_budget_keeps_all_metadata_and_prioritizes_pending_before_fresh(monkeypatch):
    monkeypatch.setattr(scrape,'legacy_source_limit',lambda source:2)
    calls = successful_network(monkeypatch)
    pending = article('pending',body_status='pending-budget',content='<p>Stored RSS fallback.</p>',
                      content_quality={'fallback':'rss-budget'})
    rows = [article('fresh-a'),cached('cached'),pending,article('fresh-b'),
            article('pending-b',body_status='pending-budget')]
    original_ids = [a['id'] for a in rows]
    result = asyncio.run(scrape.scrape_all(rows))
    assert [a['id'] for a in result]==original_ids
    assert calls==['https://example.com/pending','https://example.com/pending-b']
    assert result[1]['body_status']=='available'
    assert result[2]['body_status']=='available' and 'Actual article body.' in result[2]['content']
    assert result[4]['body_status']=='available'
    for i in (0,3):
        assert result[i]['body_status']=='pending-budget'
        assert result[i]['content_quality']['fallback']=='rss-budget'
        assert 'scrape_error' not in result[i]


def test_next_cycle_admits_backlog_and_full_cached_bodies_are_free(monkeypatch):
    monkeypatch.setattr(scrape,'legacy_source_limit',lambda source:1)
    calls = successful_network(monkeypatch)
    first = asyncio.run(scrape.scrape_all([article('a'),article('b'),article('c')]))
    assert calls==['https://example.com/a']
    # New metadata arrives first; older pending records still get the next
    # request slot. Completed bodies are retained and consume no slot.
    second_rows = [article('new'),*copy.deepcopy(first)]
    for a in second_rows:
        if a.get('body_status')=='available':
            a['content_hash']=hashlib.sha256(a['content'].encode()).hexdigest()
    second = asyncio.run(scrape.scrape_all(second_rows))
    assert calls==['https://example.com/a','https://example.com/b']
    assert [a['id'] for a in second]==['new','a','b','c']
    assert [a['body_status'] for a in second]==['pending-budget','available','available','pending-budget']


def test_body_slots_are_bounded_independently_for_each_source(monkeypatch):
    monkeypatch.setattr(scrape,'legacy_source_limit',lambda source:1)
    calls = successful_network(monkeypatch)
    rows = [article('a'),article('b'),article('c',source='Other source'),article('d',source='Other source')]
    result = asyncio.run(scrape.scrape_all(rows))
    assert calls==['https://example.com/a','https://example.com/c']
    assert len(result)==4
    assert [a['body_status'] for a in result]==['available','pending-budget','available','pending-budget']


def test_hash_mismatch_does_not_treat_body_as_free_verified_cache(monkeypatch):
    monkeypatch.setattr(scrape,'legacy_source_limit',lambda source:1)
    calls = successful_network(monkeypatch)
    row = cached('bad-cache')
    row['content_hash']='0'*64
    result = asyncio.run(scrape.scrape_all([row]))
    assert calls==['https://example.com/bad-cache']
    assert 'Actual article body.' in result[0]['content']


def test_denied_body_is_not_retried_or_left_in_budget_backlog(monkeypatch):
    calls=[]
    async def denied(session,url):
        calls.append(url)
        raise scrape.SourceAccessDenied('HTTP 403')
    monkeypatch.setattr(scrape,'_fetch_html',denied)
    row=article('denied')
    result=asyncio.run(scrape.scrape_all([row]))
    assert calls==['https://example.com/denied']
    assert result[0]['body_status']=='failed'
    assert result[0]['content_quality']['fallback']=='rss-blocked'
    assert 'RSS summary denied.' in result[0]['content']
    again=asyncio.run(scrape.scrape_all(result))
    assert calls==['https://example.com/denied']
    assert again[0]['body_status']=='failed'


@pytest.mark.parametrize('rss, expected', [('<p>Available RSS text.</p>','partial'),(None,'failed')])
def test_empty_body_extraction_discloses_partial_or_unavailable(monkeypatch,rss,expected):
    async def fetch(session,url): return '<html><body>No extractable article.</body></html>'
    monkeypatch.setattr(scrape,'_fetch_html',fetch)
    monkeypatch.setattr(scrape,'_process_html_sync',lambda *args:(None,None))
    row=article('empty',rss_content=rss)
    result=asyncio.run(scrape.scrape_all([row]))
    assert result[0]['body_status']==expected
    assert result[0]['content']


def test_deferred_existing_body_is_not_cut_or_overwritten(monkeypatch):
    monkeypatch.setattr(scrape,'legacy_source_limit',lambda source:1)
    successful_network(monkeypatch)
    body='<p>Previously captured source prose with all text.</p><img src="https://example.com/source.jpg">'
    row=article('later',content=body,content_quality={'fallback':'rss-empty'})
    result=asyncio.run(scrape.scrape_all([article('first'),row]))
    assert result[1]['content']==body
    assert result[1]['body_status']=='pending-budget'


def test_stage_timeout_keeps_all_metadata_and_unfinished_work_pending(monkeypatch):
    original_wait_for=asyncio.wait_for
    async def short_wait(awaitable,timeout): return await original_wait_for(awaitable,timeout=0.02)
    monkeypatch.setattr(scrape.asyncio,'wait_for',short_wait)
    drained=[]
    async def blocked(session,url):
        try:
            await asyncio.Event().wait()
        finally:
            drained.append(url)
    monkeypatch.setattr(scrape,'_fetch_html',blocked)
    rows=[cached('good'),article('waiting')]
    result=asyncio.run(scrape.scrape_all(rows))
    assert [a['id'] for a in result]==['good','waiting']
    assert result[0]['body_status']=='available'
    assert result[1]['body_status']=='pending-budget'
    assert 'scrape_error' not in result[1]
    assert drained==['https://example.com/waiting']


def test_outer_cancellation_drains_children_and_materializes_pending_fallback(monkeypatch):
    async def run():
        started=asyncio.Event()
        drained=asyncio.Event()
        async def blocked(session,url):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        monkeypatch.setattr(scrape,'_fetch_html',blocked)
        rows=[cached('good'),article('waiting',body_status='failed')]
        task=asyncio.create_task(scrape.scrape_all(rows))
        await asyncio.wait_for(started.wait(),timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
        assert rows[0]['body_status']=='available'
        assert rows[1]['body_status']=='pending-budget'
        assert 'RSS summary waiting.' in rows[1]['content']
        assert 'scrape_error' not in rows[1]
    asyncio.run(run())


@pytest.mark.parametrize('url, expected', [
    ('https://hk.news.yahoo.com/a.html',65536),
    ('https://hk.finance.yahoo.com/news/a.html',65536),
    ('https://www.hk.news.yahoo.com/a.html',None),
    ('https://hk.news.yahoo.com.evil.test/a.html',None),
    ('https://example.com/a.html',None),
])
def test_yahoo_body_header_parser_is_bounded_and_scoped_to_exact_official_hosts(monkeypatch,url,expected):
    calls=[]
    class Response:
        status=200
        charset='utf-8'
        async def read(self): return b'<p>Body.</p>'
    @asynccontextmanager
    async def get(session,requested,**kwargs):
        calls.append((requested,kwargs))
        yield Response()
    monkeypatch.setattr(scrape,'public_get',get)
    assert asyncio.run(scrape._fetch_html(None,url))=='<p>Body.</p>'
    assert calls[0][0]==url
    assert calls[0][1].get('max_field_size')==expected
    assert calls[0][1]['timeout'].total==scrape._MAIN_TIMEOUT
