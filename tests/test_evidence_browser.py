"""Offline DOM evidence regressions; all browser requests are intercepted."""
from pathlib import Path
from urllib.parse import quote

import pytest
from test_browser_smoke import _body_record, browser, news_data, _route_data, _static_server


SCRIPT = Path(__file__).resolve().parents[1] / 'docs/js/article-evidence.js'
HTML = '''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>證據測試</title></head><body>
<section id="summaryBox"><h2>AI 摘要</h2><ul><li>政府公布新安排</li><li>學校繼續運作</li></ul></section>
<main id="content"><p>政府今日公布<strong>新的交通安排</strong>，明日起實施。</p><p>所有學校將繼續正常運作。</p></main>
</body></html>'''


def load(browser, width=390, query=''):
    context = browser.new_context(viewport={'width': width, 'height': 700}, service_workers='block')
    page = context.new_page()
    page.route('**/*', lambda route: route.fulfill(status=200, body=HTML, content_type='text/html'))
    page.goto('https://fixture.invalid/article.html?id=a' + query)
    page.add_script_tag(path=str(SCRIPT))
    return context, page


@pytest.mark.parametrize('width', [390, 1280])
def test_verified_summary_quote_jumps_across_inline_markup(browser, width):
    context, page = load(browser, width)
    try:
        page.evaluate('''() => RssArticleEvidence.init({id:'a',date:'2026-10-12T00:00:00Z',
          content_quality:{fallback:'none'}, summary_evidence:[
            {point:'政府公布新安排',quote:'政府今日公布新的交通安排，明日起實施。',article_id:'a',verified:true}
          ]})''')
        assert page.locator('.summary-source-link').count() == 1
        assert page.locator('.summary-source-status').inner_text() == '原文依據未提供'
        before = page.locator('#content').inner_text()
        page.locator('.summary-source-link').click()
        assert page.locator('#content strong mark.source-evidence-match').inner_text() == '新的交通安排'
        assert page.locator('#content').inner_text() == before
        assert '已標示' in page.locator('.source-evidence-status').inner_text()
        assert page.locator('#source-evidence dd').all_inner_texts()[1:] == ['時間未提供', '時間未提供']
    finally:
        context.close()


def test_unsupported_old_summary_never_matches_key_sentence_by_position(browser):
    context, page = load(browser)
    try:
        page.evaluate('''() => RssArticleEvidence.init({id:'a',key_sentences:['政府今日公布新的交通安排，明日起實施。'],
          summary_evidence:[{point:'政府公布新安排',quote:'這段原文根本不存在，不能當依據。',verified:true}],
          content_quality:{fallback:'rss-blocked'},scrape_error:'HTTP 403'})''')
        assert page.locator('.summary-source-link').count() == 0
        assert page.locator('.summary-source-status').count() == 2
        assert 'RSS 摘要' in page.locator('#source-evidence').inner_text()
        assert 'HTTP 403' in page.locator('#source-evidence').inner_text()
    finally:
        context.close()


def test_source_link_query_quote_is_verified_and_unavailable_when_absent(browser):
    context, page = load(browser, query='&quote=' + quote('政府今日公布新的交通安排，明日起實施。'))
    try:
        page.evaluate("() => RssArticleEvidence.init({id:'a'})")
        assert page.locator('mark.source-evidence-match').count() > 0
        assert '跨媒體比較' in page.locator('.source-evidence-status').inner_text()
        page.evaluate("() => history.replaceState({}, '', '?id=a&quote=' + encodeURIComponent('<img src=x onerror=alert(1)>'))")
        page.evaluate("() => RssArticleEvidence.init({id:'a'})")
        assert page.locator('.source-evidence-status').inner_text().endswith('依據不可用。')
        assert page.locator('img').count() == 0
    finally:
        context.close()


def test_repeated_init_does_not_duplicate_links_or_provenance(browser):
    context, page = load(browser)
    try:
        page.evaluate("() => { RssArticleEvidence.init({id:'a'}); RssArticleEvidence.init({id:'a'}); }")
        assert page.locator('#source-evidence').count() == 1
        assert page.locator('.summary-source-status').count() == 2
    finally:
        context.close()


@pytest.mark.parametrize('width', [390, 1188, 2200])
def test_real_article_source_panel_aligns_and_wraps(browser, news_data, width):
    with _static_server() as base:
        context = browser.new_context(viewport={'width':width, 'height':920}, service_workers='block')
        page = context.new_page(); _route_data(page, news_data)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        page.evaluate("() => { const a={id:'a0',scrape_error:'long-token-'.repeat(80),content_quality:{fallback:'rss-blocked'}}; RssArticleEvidence.init(a); RssArticleEvidence.init(a); }")
        panel = page.locator('#source-evidence').bounding_box()
        summary = page.locator('#summaryBox').bounding_box()
        assert abs(panel['x'] - summary['x']) < 1
        assert abs(panel['width'] - summary['width']) < 1
        assert panel['width'] <= 920
        assert page.locator('#source-evidence').count() == 1
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        context.close()


def test_inline_hero_after_text_and_editorial_table_survive(browser, news_data):
    import copy
    data=copy.deepcopy(news_data)
    data['articles'][0]['thumbnail']='https://images.example.com/cover.jpg'
    data['articles'][0]['content_quality']={'fallback':'rss-blocked','origin':'wordpress-public-posts','omitted_invalid_images':2}
    body='<p>原文導言先出現。</p><img src="https://images.example.com/cover.jpg"><blockquote><p>來源引述</p></blockquote><table><caption>演員表</caption><tr><td colspan="2">角色名稱</td></tr></table>'
    record = _body_record(data, {'content':body,'quality':data['articles'][0]['content_quality']})
    with _static_server() as base:
        context=browser.new_context(viewport={'width':390,'height':900},service_workers='block')
        page=context.new_page();_route_data(page,data)
        page.route('**/data/content/a0.json*',lambda route:route.fulfill(json=record))
        page.route('https://images.example.com/**',lambda route:route.abort())
        page.goto(base+'/article.html?id=a0');page.locator('#content table').wait_for()
        assert page.locator('#content img').count()==1
        assert page.locator('#content table caption').inner_text()=='演員表'
        assert page.locator('#content td').get_attribute('colspan')=='2'
        assert '公開 WordPress API' in page.locator('#contentNotice').inner_text()
        assert '2 張圖片網址無效，已略過' in page.locator('#contentNotice').inner_text()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        context.close()
