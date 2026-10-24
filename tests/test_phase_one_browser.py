"""Truthful source-insufficient states on cards, search and reader."""
import hashlib
import json
import pytest

from test_browser_smoke import browser, news_data, _static_server
from test_reader_loop_browser import _route_fixture
from test_card_details_browser import fixture_data, route_body, REMOVED


@pytest.mark.parametrize('width',[390,1280])
@pytest.mark.parametrize('status,message',[
    ('image-only','目前只取得圖片'),
    ('title-only','目前只有標題或原文連結'),
    ('boilerplate','來源只提供節目或網站介紹'),
    ('withdrawn','來源已撤回這篇內容'),
    ('unavailable','未取得可摘要的來源文字'),
])
def test_empty_source_message_without_pending_promise_preserves_reader_state(browser,news_data,width,status,message):
    data,_,_=fixture_data(news_data)
    body='<p>Source state fixture</p>'
    digest=hashlib.sha256(body.encode()).hexdigest()
    for row in data['articles']:
        row.update(content=body,content_hash=digest,summary='',summary_points=[],summary_evidence=[],
                   summary_format=2,summary_status='limited',analysis_status='pending-source',
                   content_quality={'fallback':'minimal' if status=='title-only' else 'none',
                                    'text_status':status,'completeness':'insufficient'})
    raw=json.dumps({'a0':{'saved':True,'read':False,'position':.43,'updated':1}})
    with _static_server() as base:
        context=browser.new_context(viewport={'width':width,'height':900},service_workers='block')
        _route_fixture(context,base,{'payload':data})
        context.add_init_script('localStorage.setItem("rss_reader_state_v1",'+json.dumps(raw)+')')
        page=context.new_page();page.set_default_timeout(6000)
        route_body(page,data,body)
        try:
            page.goto(base+'/index.html');page.locator('#leadStory h1').wait_for()
            lead=page.locator('#leadStory')
            assert message in lead.inner_text() and '稍後補上' not in lead.inner_text()
            assert page.locator(REMOVED).count()==0
            if width==390: page.locator('#mobileTabs [data-view="search"]').click()
            page.locator('#search').fill(data['articles'][0]['title'])
            assert message in page.locator('#feed').inner_text()
            assert page.evaluate('localStorage.getItem("rss_reader_state_v1")')==raw
            page.goto(base+'/article.html?id=a0')
            expected='unavailable' if status=='title-only' else 'matched'
            page.wait_for_function('(expected)=>document.querySelector("#summaryBox").dataset.bodyCheck === expected',arg=expected)
            assert message in page.locator('#summaryBox').inner_text()
            assert '稍後補上' not in page.locator('#summaryBox').inner_text()
            assert page.locator('#summaryBox h2').inner_text()=='AI 摘要'
            assert page.locator(REMOVED).count()==0
        finally:
            context.close()
