"""No evidence UI; rich summaries, correct clocks and saved reading data remain."""
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest

from src import analyse
from src.summary_contract import normalise_points
from src.article_evidence import validate_summary_evidence
from test_browser_smoke import browser, news_data, _static_server
from test_reader_loop_browser import _route_fixture
from test_summary_contract import SENTENCES


REMOVED = '.source-point-links, .article-details, #source-evidence, .summary-source-link, .key-source-link'


def fixture_data(news_data):
    data = copy.deepcopy(news_data)
    context = [
        '運輸署會在主要路口裝設告示牌，列明受影響道路的方向和實施時段',
        '首日會派員駐守車站，協助乘客辨認臨時站位與替代步行路線',
        '營辦商將調配額外車輛，優先疏導早上上班和上學的人潮',
        '支線目前維持原有服務，乘客可在現有應用程式查閱時間表',
        '教育局已向相關校方發出通知，要求學校檢視校車接送安排',
        '通知會列出受影響的校車站點，讓家長預留額外接送時間',
        '施工範圍包括兩個路口，承建商會在凌晨車流較少時鋪設路面',
        '車輛屆時須使用鄰近道路，緊急車輛通道則會全程保持暢通',
        '網站已上載改道路線圖，並提供巴士站遷移位置和步行示意',
        '如天氣影響工程進度，最新措施將透過原有公告渠道發布',
    ]
    sentences = [[text[:-1] + '，' + context[2*i+j] + '。' for j, text in enumerate(point)] for i, point in enumerate(SENTENCES)]
    text = sum(sentences, [])
    body = '<p>' + ''.join(text[:5]) + '</p><figure><img src="/figure.svg"><figcaption>來源原有圖片說明</figcaption></figure><p>' + ''.join(text[5:]) + '</p>'
    digest = hashlib.sha256(body.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    for i, row in enumerate(data['articles']):
        row.update(content=body, content_hash=digest, content_quality={'fallback': 'none'},
                   content_updated_at=(now-timedelta(hours=2)).isoformat(), fetched_at=now.isoformat(),
                   key_sentences=[text[0]], key_sentences_content_hash=digest)
        limited = i == 2
        raw = [{'sentences': point, 'quotes': point} for point in sentences]
        if limited:
            raw = [{'sentences': sentences[0][:1], 'quotes': sentences[0][:1]}]
            row.update(content_quality={'fallback': 'rss-blocked'}, evidence_status='partial', body_status='partial')
        result = dict(normalise_points(raw, limited), score=7, tags=[], sentiment='neutral', topic='', version=analyse.ANALYSIS_VERSION)
        row.update(result)
        row['summary_evidence'] = validate_summary_evidence(row, row['summary_evidence'])
    data['articles'][1]['content_updated_at'] = None
    analyse._ensure_analysis_defaults(data['articles'])
    return data, sentences, body


def route_body(page, data, body):
    def respond(route):
        aid = urlsplit(route.request.url).path.rsplit('/', 1)[-1].removesuffix('.json')
        row = next(a for a in data['articles'] if a['id'] == aid)
        route.fulfill(json={'version': 1, 'content': body, 'content_hash': row['content_hash'], 'quality': row['content_quality']})
    page.route('**/data/content/*.json*', respond)
    page.route('**/figure.svg', lambda route: route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="30" height="20"/>'))


@pytest.mark.parametrize('width', [390, 1280])
def test_removed_evidence_rich_summary_and_last_updated_preserve_reader(browser, news_data, width):
    data, sentences, body = fixture_data(news_data)
    raw = json.dumps({'a0': {'saved': True, 'read': False, 'position': .43, 'updated': 1}})
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        external = _route_fixture(context, base, {'payload': data})
        context.add_init_script('if(localStorage.getItem("rss_reader_state_v1")===null)localStorage.setItem("rss_reader_state_v1",' + json.dumps(raw) + ')')
        page = context.new_page(); page.set_default_timeout(6000)
        errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
        route_body(page, data, body)
        try:
            page.goto(base+'/index.html')
            page.locator('#leadStory h1').wait_for()
            assert page.locator(REMOVED).count() == 0
            assert page.get_by_text('查看依據', exact=True).count() == 0
            assert page.get_by_text('資料詳情', exact=True).count() == 0
            lead = page.locator('#leadStory')
            assert lead.locator('.summary-point').all_inner_texts() == [''.join(point) for point in sentences]
            assert lead.locator('.lead-points').is_visible()
            assert '最後更新：2 小時前' in lead.inner_text()
            assert '正文更新' not in lead.inner_text() and '發布 ' not in lead.inner_text() and '抓取 ' not in lead.inner_text()
            assert page.locator('#feed .reading-card').filter(has=page.locator('a[href="article.html?id=a1"]')).get_by_text('最後更新：未提供', exact=True).count() == 1
            partial = page.locator('#feed .reading-card').filter(has=page.locator('a[href="article.html?id=a2"]'))
            assert partial.locator('.summary-point').count() == 1
            assert '來源資料不足' in partial.locator('.summary-limits').inner_text()
            assert '正文未完整' in partial.locator('.reading-warning').inner_text()
            page.locator('#readingStatus').select_option('unread')
            assert page.locator(REMOVED).count() == 0
            assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == raw
            page.goto(base+'/article.html?id=a0')
            page.wait_for_function('document.querySelector("#summaryBox").dataset.bodyCheck === "matched"')
            assert page.locator(REMOVED).count() == 0
            assert page.locator('#summaryBox li').all_inner_texts() == [''.join(point) for point in sentences]
            assert page.locator('#summaryBox').is_visible()
            assert '最後更新：2 小時前' in page.locator('#eyebrow').inner_text()
            assert page.locator('#content > *').evaluate_all('(nodes)=>nodes.map(n=>n.tagName)') == ['P', 'FIGURE', 'P']
            assert page.locator('#content figcaption').inner_text() == '來源原有圖片說明'
            state = page.evaluate('RssReaderState.get("a0")')
            assert state['saved'] is True and state['position'] == .43
            assert page.locator('#readerResume').is_visible()
            assert page.locator('#readerSaveLater, [data-save-later]').count() == 0
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not errors and not external
        finally:
            context.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_missing_timestamp_partial_summary_search_and_player_without_evidence(browser, news_data, width):
    data, sentences, body = fixture_data(news_data)
    data['articles'][1]['summary_evidence'] = []
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        _route_fixture(context, base, {'payload': data})
        page = context.new_page(); page.set_default_timeout(6000); route_body(page, data, body)
        try:
            page.goto(base+'/index.html')
            page.locator('#leadStory h1').wait_for()
            if width == 390: page.locator('#mobileTabs [data-view="search"]').click()
            page.locator('#search').fill(data['articles'][1]['title'])
            page.wait_for_function('document.querySelector("#resultCount").textContent.includes("1 篇匹配報道")')
            assert page.locator('#search').evaluate('(node)=>node===document.activeElement')
            assert page.locator(REMOVED).count() == 0
            assert page.locator('#leadStory .summary-unverified').count() == 5
            if width == 1280:
                page.locator('#summaryPlayTab').click()
                page.locator('[data-player-count]').wait_for()
                assert page.locator('#summaryPlayer .summary-source-link').count() == 0
            page.goto(base+'/article.html?id=a1')
            page.wait_for_function('document.querySelector("#summaryBox").dataset.bodyCheck === "matched"')
            assert '最後更新：未提供' in page.locator('#eyebrow').inner_text()
            assert page.locator('#summaryBox .summary-source-status').count() == 5
            assert page.locator(REMOVED).count() == 0
            page.goto(base+'/article.html?id=a2')
            page.wait_for_function('document.querySelector("#summaryBox").dataset.bodyCheck === "matched"')
            assert page.locator('#summaryBox li').count() == 1
            assert page.locator('#summaryBox .summary-limits').is_visible()
            assert '來源資料不足' in page.locator('#summaryBox').inner_text()
            assert page.locator('#contentNotice').is_visible()
            assert page.locator(REMOVED).count() == 0
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        finally:
            context.close()
