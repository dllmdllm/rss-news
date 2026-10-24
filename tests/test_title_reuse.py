"""Published source-title bindings prevent cosmetic translation cache churn."""
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone

import pytest
from src import fetch, analyse
from src.publisher_identity import identity_key
from src.title_translation import bind_translation, reuse_translation, published_titles
from src.analysis_input import input_hash
from src.article_evidence import validate_summary_evidence
from src.summary_contract import normalise_points
from test_format_diagnostics import complete
from test_phase_one_recovery import write_snapshot
from tools.validate_publication import validate_site

RAW='Department introduces five traffic measures today'
A='部門由今日起實施五項交通措施'
B='部門今日起推行五項交通措施'
URL='https://9to5mac.com/2026/10/24/synthetic-traffic/'


def row(**changes):
    return dict({'id':fetch._make_id(URL),'url':URL,'source':'9to5Mac','title':RAW,
                 'date':'2026-10-24T00:00:00+00:00'},**changes)


def lookup(prior): return {(identity_key(prior),prior['source']):prior}


def rss(titles=(RAW,)):
    return ('<rss version="2.0"><channel>'+''.join(
        '<item><title>'+title+'</title><link>'+URL+('' if i==0 else str(i))+'</link>'
        '<pubDate>Sat, 24 Oct 2026 00:00:00 GMT</pubDate></item>' for i,title in enumerate(titles))+'</channel></rss>').encode()


def test_two_fresh_responses_reuse_translation_and_complete_analysis(monkeypatch,tmp_path):
    calls=[]
    async def read(*args,**kwargs): return 200,rss(),{'Last-Modified':'new-response'}
    async def translate(session,titles): calls.append(titles);return [A if len(calls)==1 else B]
    monkeypatch.setattr(fetch,'_read_feed_with_tls_fallback',read)
    monkeypatch.setattr(fetch,'_translate_titles_minimax',translate)
    feed={'name':'9to5Mac','url':'https://9to5mac.com/feed/','category':'科技'}
    cutoff=datetime(2026,10,23,tzinfo=timezone.utc)
    first=asyncio.run(fetch._fetch_one(None,dict(feed),cutoff,{}))[0][0]
    assert first['title']==A and first['title_translation_status']=='translated'
    path=tmp_path/'articles.json';path.write_text(json.dumps({'articles':[first]}),encoding='utf-8')
    second=asyncio.run(fetch._fetch_one(None,dict(feed,_title_reuse=published_titles(path)),cutoff,{}))[0][0]
    assert calls==[[RAW]] and second['title']==A and second['title_translation_status']=='cached'
    body='<p>'+''.join(s for p in complete()['summary_points'] for s in p['sentences'])+'</p>'
    first.update(content=body,content_quality={'fallback':'none'},content_hash=hashlib.sha256(body.encode()).hexdigest())
    first.update(normalise_points(complete()['summary_points']),score=6,analysis_version=analyse.ANALYSIS_VERSION,
                 analysis_input_hash=input_hash(first,analyse.ANALYSIS_VERSION))
    first['summary_evidence']=validate_summary_evidence(first,first['summary_evidence'])
    cached={**first,'input_hash':first['analysis_input_hash'],'version':first['analysis_version']}
    second.update(content=body,content_hash=first['content_hash'],content_quality={'fallback':'none'})
    assert input_hash(second,analyse.ANALYSIS_VERSION)==first['analysis_input_hash']
    assert not analyse._needs_full_analysis(cached,second)
    cache=tmp_path/'cache.json';cache.write_text(json.dumps({second['id']:cached}),encoding='utf-8')
    monkeypatch.setattr(analyse,'CACHE_PATH',cache);monkeypatch.setattr(analyse,'MINIMAX_API_KEY','offline-only')
    async def forbidden(*a,**kw): pytest.fail('stable source must not regenerate')
    monkeypatch.setattr(analyse,'_analyse_batch',forbidden)
    asyncio.run(analyse.analyse_all([second]))
    assert second['summary_status']=='complete' and second['summary']==first['summary']
    write_snapshot(tmp_path/'docs',second);validate_site(tmp_path/'docs',require_current=True)


@pytest.mark.parametrize('change',[{'title':'Department does not introduce five traffic measures today'},
    {'title':'Department introduces six traffic measures today'}, {'url':'https://another.example/story'},
    {'source':'HuffPost 新聞'}])
def test_material_source_changes_miss_without_overwriting_incoming_title(change):
    prior=row();assert bind_translation(prior,RAW,A,fetch.TITLE_TRANSLATION_VERSION)
    incoming=row(**change);saved=copy.deepcopy(incoming)
    assert not reuse_translation(incoming,lookup(prior),fetch.TITLE_TRANSLATION_VERSION)
    assert incoming==saved


@pytest.mark.parametrize('damage',['missing','source-hash','translation-hash','identity','version','manual-title','source-title'])
def test_missing_or_corrupt_binding_is_not_trusted(damage):
    prior=row();bind_translation(prior,RAW,A,fetch.TITLE_TRANSLATION_VERSION)
    if damage=='missing':prior.pop('title_translation')
    elif damage=='manual-title':prior['title']='手動修改，必須保留，不能當成已驗證譯題'
    else:
        key={'source-hash':'source_hash','translation-hash':'translated_hash','source-title':'source_title'}.get(damage,damage)
        prior['title_translation'][key]='changed'
    saved=copy.deepcopy(prior);incoming=row()
    assert not reuse_translation(incoming,lookup(prior),fetch.TITLE_TRANSLATION_VERSION)
    assert prior==saved and incoming['title']==RAW


@pytest.mark.parametrize('bad',[RAW,'','<script>bad</script>','{"title":"錯誤"}',None])
def test_translation_failure_does_not_publish_successful_binding(bad):
    incoming=row();assert not bind_translation(incoming,RAW,bad,fetch.TITLE_TRANSLATION_VERSION)
    assert incoming['title']==RAW and 'title_translation' not in incoming


def test_body_change_still_invalidates_analysis_with_stable_translated_title():
    prior=row();bind_translation(prior,RAW,A,fetch.TITLE_TRANSLATION_VERSION)
    prior['content']='<p>原來正文。</p>';old_hash=input_hash(prior,analyse.ANALYSIS_VERSION)
    incoming=row(content='<p>真正更新了的正文。</p>')
    assert reuse_translation(incoming,lookup(prior),fetch.TITLE_TRANSLATION_VERSION)
    assert input_hash(incoming,analyse.ANALYSIS_VERSION)!=old_hash


def test_304_does_not_translate_and_retained_title_stays_bound(monkeypatch):
    prior=row();bind_translation(prior,RAW,A,fetch.TITLE_TRANSLATION_VERSION)
    async def read(*a,**kw):return 304,b'',{}
    async def forbidden(*a,**kw):pytest.fail('304 must not translate')
    monkeypatch.setattr(fetch,'_read_feed_with_tls_fallback',read)
    monkeypatch.setattr(fetch,'_materialized',lambda *a:True)
    monkeypatch.setattr(fetch,'_translate_titles_minimax',forbidden)
    feed={'name':'9to5Mac','url':'https://9to5mac.com/feed/','category':'科技','_title_reuse':lookup(prior)}
    rows,error,unchanged=asyncio.run(fetch._fetch_one(None,feed,datetime(2026,10,23,tzinfo=timezone.utc),{}))
    assert unchanged and not rows and error is None
    before=copy.deepcopy(prior);asyncio.run(fetch.retranslate_english_titles([prior]));assert prior==before


def test_cancelled_feed_preserves_cached_hit_but_never_binds_pending_translation(monkeypatch):
    prior=row();bind_translation(prior,RAW,A,fetch.TITLE_TRANSLATION_VERSION)
    async def read(*a,**kw):return 200,rss((RAW,'A second story')),{}
    async def stalled(*a,**kw):await asyncio.Event().wait()
    monkeypatch.setattr(fetch,'_read_feed_with_tls_fallback',read)
    monkeypatch.setattr(fetch,'_translate_titles_minimax',stalled)
    monkeypatch.setattr(fetch,'_PER_FEED_TIMEOUT',.02)
    feed={'name':'9to5Mac','url':'https://9to5mac.com/feed/','category':'科技','_title_reuse':lookup(prior)}
    rows,error,_=asyncio.run(fetch._fetch_one_capped(None,feed,datetime(2026,10,23,tzinfo=timezone.utc),{}))
    assert 'timeout' in error and rows[0]['title']==A and rows[0]['title_translation_status']=='cached'
    assert 'title_translation' not in rows[1] and rows[1]['title']=='A second story'


def test_cancelled_retranslation_keeps_completed_chunk_only(monkeypatch):
    monkeypatch.setattr(fetch,'MINIMAX_API_KEY','offline-only')
    async def translate(session,titles):
        if titles[0]=='Story 10':await asyncio.Event().wait()
        return ['已完成譯題 '+t for t in titles]
    monkeypatch.setattr(fetch,'_translate_titles_minimax',translate)
    rows=[row(id=str(i),title='Story '+str(i)) for i in range(11)]
    async def run():
        with pytest.raises(asyncio.TimeoutError):await asyncio.wait_for(fetch.retranslate_english_titles(rows),.03)
    asyncio.run(run())
    assert all(a.get('title_translation_status')=='translated' for a in rows[:10])
    assert rows[-1]['title']=='Story 10' and 'title_translation' not in rows[-1]


def test_title_reuse_does_not_bless_invalid_analysis_evidence(tmp_path):
    a=row();bind_translation(a,RAW,A,fetch.TITLE_TRANSLATION_VERSION)
    b=row();assert reuse_translation(b,lookup(a),fetch.TITLE_TRANSLATION_VERSION)
    body='<p>'+''.join(s for p in complete()['summary_points'] for s in p['sentences'])+'</p>'
    b.update(content=body,content_hash=hashlib.sha256(body.encode()).hexdigest(),content_quality={'fallback':'none'})
    b.update(normalise_points(complete()['summary_points']),score=6,tags=[],sentiment='neutral',analysis_version=analyse.ANALYSIS_VERSION,
             analysis_input_hash=input_hash(b,analyse.ANALYSIS_VERSION))
    b['summary_evidence']=validate_summary_evidence(b,b['summary_evidence'])
    b['summary_evidence'][0]['quote']=b['summary_evidence'][0]['quotes'][0]='這一句根本不存在於原始正文內。'
    write_snapshot(tmp_path/'docs',b)
    with pytest.raises(ValueError,match='quote'):validate_site(tmp_path/'docs',require_current=True)
