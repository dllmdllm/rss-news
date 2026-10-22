"""Offline card disclosures: compact by default, warnings and evidence stay honest."""
import copy
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from test_browser_smoke import browser, news_data, _static_server
from test_reader_loop_browser import _route_fixture


QUOTE = '政府今日公布新的交通安排，明日起實施。'
BODY = '<p>政府今日公布<strong>新的交通安排</strong>，明日起實施。</p><p>所有學校將繼續正常運作。</p>'
HASH = hashlib.sha256(BODY.encode()).hexdigest()


def card_data(news_data):
    data = copy.deepcopy(news_data)
    first = data['articles'][0]
    first.update(summary='・政府公布新安排\n・尚未取得依據的說法', content_hash=HASH,
                 fetched_at='2026-10-22T17:00:00Z', content_updated_at='2026-10-22T16:00:00Z',
                 content_quality={'fallback': 'rss-blocked'}, evidence_status='partial',
                 scrape_error='HTTP 403', content_retention={'latest_error': 'HTTP 403'},
                 summary_evidence=[{'point': '政府公布新安排', 'quote': QUOTE, 'article_id': first['id'],
                                    'verified': True, 'content_hash': HASH}])
    data['articles'][1].update(content_quality={'fallback': 'none'}, evidence_status='full')
    data['articles'][2].update(content_quality={'fallback': 'minimal'}, body_status='pending-budget')
    return data


@pytest.mark.parametrize('width', [390, 1280])
def test_card_disclosures_keyboard_repeated_toggle_and_filter_rerender(browser, news_data, width):
    data = card_data(news_data)
    raw = json.dumps({'a0': {'saved': True, 'position': .37, 'updated': 1},
                      'a1': {'read': True, 'saved': True, 'position': .61, 'updated': 2}})
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        external = _route_fixture(context, base, {'payload': data})
        context.add_init_script('localStorage.setItem("rss_reader_state_v1",' + json.dumps(raw) + ')')
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(base + '/index.html')
            page.locator('#leadStory h1').wait_for()
            lead = page.locator('#leadStory')
            metadata = lead.locator('.article-details')
            evidence = lead.locator('.source-point-links')
            assert lead.locator('.summary-point').all_inner_texts() == ['政府公布新安排', '尚未取得依據的說法']
            assert lead.locator('.summary-unverified').all_inner_texts() == ['未核實']
            assert lead.locator('.summary-unverified').is_visible()
            assert lead.locator('.reading-warning').is_visible()
            assert '抓取失敗，正文未完整' in lead.locator('.reading-warning').inner_text()
            assert not metadata.evaluate('(node) => node.open')
            assert not evidence.evaluate('(node) => node.open')
            assert not metadata.locator('.reading-provenance').is_visible()
            assert not evidence.locator('a').is_visible()
            assert metadata.locator('summary').inner_text() == '資料詳情'
            assert evidence.locator('summary').inner_text() == '查看依據'
            assert lead.locator('a details, a summary').count() == 0
            url = page.url
            for details in [metadata, evidence]:
                control = details.locator('summary')
                assert control.bounding_box()['height'] >= 24
                for _ in range(2):
                    control.focus()
                    control.press('Enter')
                    assert details.evaluate('(node) => node.open')
                    assert control.evaluate('(node) => node === document.activeElement')
                    assert control.evaluate('(node) => getComputedStyle(node).outlineStyle') != 'none'
                    control.press('Space')
                    assert not details.evaluate('(node) => node.open')
                    assert lead.locator('.reading-warning').is_visible()
                control.click()
                assert details.evaluate('(node) => node.open')
            assert page.url == url
            assert all(label in metadata.inner_text() for label in ['發布', '抓取', '正文更新', '使用已保存正文'])
            assert '不代表已核實摘要的解讀或事實' in evidence.inner_text()
            assert evidence.locator('blockquote').inner_text() == QUOTE
            assert '尚未取得依據的說法' not in evidence.inner_text()
            assert '未有可核對的原文段落' in evidence.inner_text()
            params = parse_qs(urlsplit(evidence.locator('a').get_attribute('href')).query)
            assert params == {'id': ['a0'], 'quote': [QUOTE], 'quote_hash': [HASH], 'quote_kind': ['summary']}
            pending = page.locator('#feed .reading-card').filter(has=page.locator('a.card[href="article.html?id=a2"]'))
            assert '正文未完整' in pending.locator('.reading-warning').inner_text()
            complete = page.locator('#feed .reading-card').filter(has=page.locator('a.card[href="article.html?id=a1"]'))
            assert complete.locator('.reading-warning').count() == 0
            assert not complete.locator('.article-details').evaluate('(node) => node.open')
            assert not complete.locator('.source-point-links').evaluate('(node) => node.open')
            page.locator('#readingStatus').select_option('read')
            assert lead.locator('h1').inner_text() == data['articles'][1]['title']
            # Freshly rendered cards start collapsed; the old card's open state
            # must not transfer to another article, nor suppress its warnings.
            assert not metadata.evaluate('(node) => node.open')
            assert not evidence.evaluate('(node) => node.open')
            assert lead.locator('.summary-unverified').count() == 2
            page.locator('#readingStatus').select_option('all')
            assert lead.locator('h1').inner_text() == data['articles'][0]['title']
            assert lead.locator('.reading-warning').is_visible()
            assert not metadata.evaluate('(node) => node.open')
            evidence.locator('summary').click()
            assert evidence.locator('blockquote').inner_text() == QUOTE
            assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == raw
            assert page.evaluate('RssReaderState.get("a0").position') == .37
            assert page.evaluate('RssReaderState.get("a1").saved') is True
            assert page.locator('[data-save-later], option[value="saved"]').count() == 0
            assert page.locator('#leadStory .ai-note').first.is_visible()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not external and not errors
        finally:
            context.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_search_rebuilds_card_controls_without_losing_query_or_ai(browser, news_data, width):
    data = card_data(news_data)
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        external = _route_fixture(context, base, {'payload': data})
        page = context.new_page()
        page.set_default_timeout(5000)
        try:
            page.goto(base + '/index.html')
            page.locator('#leadStory h1').wait_for()
            if width == 390:
                page.locator('#mobileTabs [data-view="search"]').click()
            page.locator('#search').fill(data['articles'][0]['title'])
            page.wait_for_function('document.querySelector("#resultCount").textContent.includes("1 篇匹配報道")')
            assert page.locator('#search').input_value() == data['articles'][0]['title']
            assert page.locator('#search').evaluate('(node) => node === document.activeElement')
            assert not page.locator('#leadStory .article-details').evaluate('(node) => node.open')
            assert not page.locator('#leadStory .source-point-links').evaluate('(node) => node.open')
            if width == 390:
                page.locator('#mobileTabs [data-view="home"]').click()
            page.locator('#leadStory .source-point-links summary').click()
            assert page.locator('#leadStory .source-point-links blockquote').inner_text() == QUOTE
            assert page.locator('#leadStory .lead-points').is_visible()
            assert page.locator('#leadStory .summary-unverified').is_visible()
            assert page.locator('#leadStory .reading-warning').is_visible()
            assert (page.locator('#mobileTabs [data-view="ai"]') if width == 390 else page.locator('#summaryPlayTab')).is_visible()
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
            assert not external
        finally:
            context.close()
