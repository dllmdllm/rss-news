import asyncio
import copy
import json
import ssl
import time

import aiohttp
import pytest
from src import media_qa as qa
from src.media_fields import image_url, repair_thumbnail, repair_inline_images, clean_unit_markers
from src.public_http import UnsafeDestination
from src.scrape import SourceAccessDenied, SourceHTTPError, _process_html_sync, _build_hk01_content_from_data, _rss_fallback_content


@pytest.mark.parametrize('bad', ['data:image/png;base64,AAAA', 'javascript:bad()',
    'https://127.0.0.1/x.jpg', 'https://user:pass@images.example.com/x.jpg',
    'https://images.example.com/_nuxt/default.a.jpg', 'https://images.example.com/player.html', ''])
def test_invalid_inline_is_omitted_before_gate_without_text_or_analysis_change(bad):
    from src.analyse import analysis_input_hash
    import hashlib
    content=f'<p>First text.</p><figure><img src="{bad}"><figcaption>Original caption.</figcaption></figure><img src="https://images.example.com/good.jpg"><p>Last text.</p><img src="https://images.example.com/good.jpg">'
    row={'id':'bad','url':'https://news.example.com/a','content':content,'content_hash':hashlib.sha256(content.encode()).hexdigest(),'content_quality':{'fallback':'none','origin':'article-html'}}
    before=analysis_input_hash(row)
    with pytest.raises(ValueError,match='invalid inline image'):
        qa.inventory([row])
    assert repair_inline_images(row)==1
    assert analysis_input_hash(row)==before
    assert row['content'].count('good.jpg')==2
    assert row['content'].index('First text.')<row['content'].index('Original caption.')<row['content'].index('good.jpg')<row['content'].index('Last text.')
    assert row['content_quality']=={'fallback':'none','origin':'article-html','omitted_invalid_images':1,'images':2}
    assert qa.inventory([row])['records']==1
    result=copy.deepcopy(row)
    assert repair_inline_images(row)==0 and row==result


def test_inline_normalization_keeps_untouched_bytes_and_resolves_same_article_paths():
    row={'id':'ok','url':'https://news.example.com/a','content':"<p>Text</p><img src='https://images.example.com/photo.jpg' alt='Caption'>"}
    original=row['content'];assert repair_inline_images(row)==0 and row['content']==original
    row['content']='<p>First</p><img src="/photo.jpg"><p>Last</p>'
    assert repair_inline_images(row)==0
    assert 'https://news.example.com/photo.jpg' in row['content']
    qa.inventory([row])


def test_am730_literal_filename_space_is_encoded_without_losing_source_image():
    from src.analyse import analysis_input_hash
    raw='https://cdn3.am730.com.hk/processed/2025-10/1058581/block/singleImage/3239326/df/yau lai est_0.jpg?updatedAt=1792038067'
    row={'id':'d852b5bceb7a','url':'https://www.am730.com.hk/article','content':f'<p>新聞正文</p><img src="{raw}"><p>原文圖說</p>'}
    before=analysis_input_hash(row)
    with pytest.raises(ValueError,match='invalid inline image'):
        qa.inventory([row])
    assert repair_inline_images(row)==0
    assert raw.replace(' ','%20') in row['content']
    assert row['content'].count('<img')==1
    assert analysis_input_hash(row)==before
    assert qa.inventory([row])['records']==1
    assert 'omitted_invalid_images' not in row.get('content_quality',{})


@pytest.mark.parametrize('value', ['/_nuxt/default.R189UE3B.jpg', '//player.example.com/a.html?x="<p>', 'javascript:bad()', 'https://u:p@example.com/p.jpg', 'https://127.0.0.1/p.jpg', 'https://private.internal/p.jpg'])
def test_invalid_hero_cannot_become_valid_by_resolving_base(value):
    assert image_url(value, 'https://news.example.com/article') is None


def test_same_article_media_fallback_and_relative_normalization():
    article={'url':'https://news.example.com/a','thumbnail':'/_nuxt/default.bad.jpg','content':'<p>文字</p><img src="/photo.jpg">'}
    assert repair_thumbnail(article)=='https://news.example.com/photo.jpg'
    article['content']='';article['thumbnail']='broken <html>'
    assert repair_thumbnail(article) is None


def test_hk01_quote_table_images_stay_interleaved_and_safe():
    blocks=[{'blockType':'text','htmlTokens':[[{'content':'第一段'}]]},
            {'blockType':'quote','message':'引述<內容>','author':'記者'},
            {'blockType':'image','image':{'cdnUrl':'https://images.example.com/one.jpg','caption':'圖說'}},
            {'blockType':'table','htmlString':'<table onclick="bad()"><caption>表題</caption><tr><td colspan="2">角色</td></tr><script>bad()</script></table>'},
            {'blockType':'image','image':{'cdnUrl':'https://images.example.com/one.jpg'}}]
    html=_build_hk01_content_from_data({'props':{'initialProps':{'pageProps':{'article':{'blocks':blocks}}}}})
    assert html.index('第一段') < html.index('引述&lt;內容&gt;') < html.index('one.jpg') < html.index('表題') < html.rindex('one.jpg')
    assert 'onclick' not in html and '<script' not in html
    assert 'colspan="2"' in html and html.count('one.jpg')==2


def test_hkepc_public_body_order_without_navigation_or_media_loss():
    html='<nav>垃圾</nav><div id="view" class="content"><p>第一段正文</p><img src="/p.jpg"><p>第二段正文</p><img src="/p.jpg"><script>bad()</script></div><aside>其他新聞</aside>'
    body,_=_process_html_sync(html,'https://www.hkepc.com/123/article',False)
    assert body.index('第一段') < body.index('p.jpg') < body.index('第二段') < body.rindex('p.jpg')
    assert body.count('https://www.hkepc.com/p.jpg')==2
    assert '垃圾' not in body and '其他新聞' not in body and 'bad()' not in body


def test_api_fallback_is_not_claimed_as_rss_excerpt_or_full():
    article={'source':'法庭線','url':'https://hkcourtnews.com/a','collection_method':'wordpress-public-posts','rss_content':'<p>公開API正文</p>'}
    _rss_fallback_content(article,fallback='rss-blocked')
    assert article['content_quality']['origin']=='wordpress-public-posts'
    assert article['content_quality']['completeness']=='unverified'


def test_marker_cleanup_requires_complete_sequential_echo():
    raw='<p>[0] 第一段</p><p>[1] 第二段</p><p>[2] 第三段</p>'
    fixed,changed=clean_unit_markers(raw)
    assert changed and '[0]' not in fixed and '第一段' in fixed
    for raw in ['<p>[0] 有意義數字</p>', '<p>[0] a</p><p>[2] b</p><p>[3] c</p>', '<p>[0] a</p><p>[1] b</p><p>caption</p>']:
        assert clean_unit_markers(raw)==(raw,False)


@pytest.mark.parametrize('error',[SourceAccessDenied('403'),UnsafeDestination('blocked'),aiohttp.ClientSSLError(None,OSError()),ssl.SSLError('bad')])
def test_permanent_reads_never_retry(error):
    calls=[]
    async def read():calls.append(1);raise error
    with pytest.raises(type(error)):
        asyncio.run(qa.bounded_read(read,time.monotonic()+10))
    assert len(calls)==1


def test_transient_retry_is_exactly_once_and_zero_budget_does_no_read(monkeypatch):
    calls=[]
    async def no_sleep(_):pass
    monkeypatch.setattr(qa.asyncio,'sleep',no_sleep)
    async def read():
        calls.append(1)
        if len(calls)==1:raise SourceHTTPError(503)
        return 'okay'
    assert asyncio.run(qa.bounded_read(read,time.monotonic()+10))=='okay'
    assert len(calls)==2
    with pytest.raises(TimeoutError):asyncio.run(qa.bounded_read(read,time.monotonic()-1))
    assert len(calls)==2


def test_failure_backoff_and_unverified_never_create_recovery():
    prior={};now=1000
    for minutes in [20,40,80,160,320,360,360]:
        prior=qa.fold(prior,{'status':'failed','problem':'denied','checked_at':'now'},now)
        assert prior['next_eligible']==now+minutes*60
    prior['last_good']={'checked_at':'old'};prior['acknowledged']='failed-ack'
    row=qa.fold(prior,{'status':'unverified','checked_at':'new'},now)
    assert row['last_good']=={'checked_at':'old'} and row['acknowledged']=='failed-ack'


def test_rotation_limits_atomic_checkpoint_and_removed_sources(monkeypatch,tmp_path):
    path=tmp_path/'state.json';calls=[]
    feeds=[{'name':str(n),'url':'https://example.com/feed'}for n in range(10)]
    articles=[{'id':str(n),'source':str(n),'date':'2026-10-13T01:00:00+00:00','content':'<p>text</p>'}for n in range(10)]
    async def observe(session,article,previous,deadline):
        calls.append(article['id']);return {'status':'verified','article_id':article['id'],'body_hash':'hash','checked_at':'now'}
    monkeypatch.setattr(qa,'observe',observe)
    state=asyncio.run(qa.run_media_qa(articles,path=path,feeds=feeds))
    assert calls==list(map(str,range(6))) and state['cursor']==6
    assert json.loads(path.read_text(encoding='utf-8'))==state and not path.with_suffix('.json.tmp').exists()
    calls.clear();state=asyncio.run(qa.run_media_qa(articles,path=path,feeds=feeds))
    assert calls==['6','7','8','9','0','1']
    state=asyncio.run(qa.run_media_qa(articles,path=path,feeds=feeds[:2],budget=0))
    assert set(state['sources'])=={'0','1'} and all(r['status']=='unverified'for r in state['sources'].values())


def test_notifications_acknowledge_only_success_and_dedupe(tmp_path):
    path=tmp_path/'state.json'
    row=qa.fold({}, {'status':'failed','problem':'bad','article_id':'a','body_hash':'h','checked_at':'now'},100)
    qa.atomic_save(path,{'version':1,'sources':{'Source':row}});calls=[]
    async def failed(message):calls.append(message);return False
    assert asyncio.run(qa.notify_media_qa(path=path,sender=failed))==[]
    assert not qa.load_state(path)['sources']['Source'].get('acknowledged')
    async def okay(message):calls.append(message);return True
    assert asyncio.run(qa.notify_media_qa(path=path,sender=okay))==['Source']
    assert asyncio.run(qa.notify_media_qa(path=path,sender=okay))==[]
    state=qa.load_state(path);state['sources']['Source']=qa.fold(state['sources']['Source'],{'status':'unverified','checked_at':'later'},200);qa.atomic_save(path,state)
    assert asyncio.run(qa.notify_media_qa(path=path,sender=okay))==[]
    state['sources']['Source']=qa.fold(state['sources']['Source'],{'status':'verified','article_id':'a','body_hash':'h','checked_at':'later'},200);qa.atomic_save(path,state)
    assert asyncio.run(qa.notify_media_qa(path=path,sender=okay))==['Source']


def test_inventory_reports_repetition_without_deleting_and_rejects_bad_fields():
    article={'id':'a','content':'<p>hello</p><img src="https://img.example.com/a.jpg"><img src="https://img.example.com/a.jpg">'}
    original=copy.deepcopy(article);report=qa.inventory([article])
    assert report['findings'][0]['count']==1 and article==original
    with pytest.raises(ValueError):qa.inventory([article,article])
    with pytest.raises(ValueError):qa.inventory([dict(article,thumbnail='/_nuxt/default.bad.jpg')])


def test_real_image_decode_and_cpu_worker_deadline():
    import io
    from PIL import Image
    raw=io.BytesIO();Image.new('RGB',(3,4)).save(raw,format='PNG')
    assert qa.decode_image(raw.getvalue())=={'status':'verified','width':3,'height':4,'decoded_frames':1}
    assert qa.decode_image(b'not an image')['status']=='failed'
    started=time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(qa.cpu_task('inventory',[],time.monotonic()+.001))
    assert time.monotonic()-started < 2.5


def test_order_difference_and_partial_body_do_not_report_recovery(monkeypatch):
    async def read(*_):return '<p>source updated</p>'
    async def cpu(*_):return {'order_signature':'source','stored_order_signature':'old','image_urls':[]}
    monkeypatch.setattr(qa,'_fetch_html',read);monkeypatch.setattr(qa,'cpu_task',cpu)
    article={'id':'a','url':'https://example.com/a','content':'<p>old</p>','content_quality':{'fallback':'none'}}
    row=asyncio.run(qa.observe(None,article,{'article_id':'a','original_hash':'older'},time.monotonic()+10))
    assert row['status']=='unverified' and row['revision_comparison'].startswith('original-changed')


def test_pending_failed_notification_survives_budget_expiry(tmp_path):
    path=tmp_path/'qa.json'
    old=qa.fold({}, {'status':'failed','problem':'bad','article_id':'a','body_hash':'h','checked_at':'old'},0)
    new=qa.fold(old,{'status':'unverified','problem':'budget','checked_at':'now'},100)
    qa.atomic_save(path,{'version':1,'sources':{'Source':new}})
    async def send(_):return True
    assert asyncio.run(qa.notify_media_qa(path=path,sender=send))==['Source']


def test_unverified_cycle_clears_unsent_recovery(tmp_path):
    path=tmp_path/'qa.json'
    row=qa.fold({'acknowledged':'failed'}, {'status':'verified','article_id':'a','body_hash':'h','checked_at':'old'},0)
    assert row['pending_event']['status']=='verified'
    row=qa.fold(row,{'status':'unverified','checked_at':'now','problem':'partial'},100)
    qa.atomic_save(path,{'version':1,'sources':{'Source':row}})
    async def forbidden(_):raise AssertionError('unverified cannot recover')
    assert asyncio.run(qa.notify_media_qa(path=path,sender=forbidden))==[]
