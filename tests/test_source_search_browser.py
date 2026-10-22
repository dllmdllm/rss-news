"""Desktop/mobile result and source-order behavior with deterministic fixtures."""
import copy
import hashlib
import json

import pytest
from test_browser_smoke import browser, news_data, _route_data, _static_server


def large_snapshot(data):
    template = data['articles'][0]
    data['articles'] = [dict(copy.deepcopy(template), id=f'a{i}', title=f'蘋果 iPhone 報道 {i}',
                             summary=f'目前收錄的消息 {i}', topic='共同話題') for i in range(79)]
    data['sources']['測試來源']['count'] = data['sources']['測試來源']['effective_count'] = 79
    return data


@pytest.mark.parametrize('width', [390,1280])
def test_all_search_matches_are_reachable_and_same_token_rules_apply(browser, news_data, width):
    data = large_snapshot(news_data)
    with _static_server() as base:
        page = browser.new_page(viewport={'width':width,'height':900}, service_workers='block')
        _route_data(page, data)
        page.goto(base+'/index.html')
        page.locator('#feed a.card').first.wait_for()
        if width == 390: page.locator('#mobileTabs [data-view="search"]').click()
        page.locator('#search').fill('蘋果 ＩＰＨＯＮＥ')
        page.wait_for_function("document.querySelector('#searchResultTitle').textContent.includes('79 篇匹配報道')")
        assert page.evaluate("document.activeElement.id") == 'search'
        if width == 390:
            assert page.locator('#searchResultList > a').count() == 20
            while page.locator('[data-search-more]').count(): page.locator('[data-search-more]').click()
            assert page.locator('#searchResultList > a').count() == 79
            assert '已顯示 79 張' in page.locator('#searchResultTitle').inner_text()
        else:
            for _ in range(5):
                page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
                page.wait_for_timeout(150)
            assert page.locator('#feed a.card').count() + page.locator('#leadStory a').count() == 79
        # A single CJK character has no hidden two-character fuzzy threshold.
        page.locator('#search').fill('蘋')
        page.wait_for_function("document.querySelector('#searchResultTitle').textContent.includes('79 篇匹配報道')")
        page.close()


def test_mobile_filters_before_pagination_and_never_loses_late_unread_match(browser, news_data):
    data = large_snapshot(news_data)
    with _static_server() as base:
        page = browser.new_page(viewport={'width':390,'height':900}, service_workers='block')
        _route_data(page, data)
        records = {f'a{i}': {'saved': i == 78, 'read': i < 78, 'updated': 1} for i in range(79)}
        raw = json.dumps(records)
        page.add_init_script('localStorage.setItem("rss_reader_state_v1",'+json.dumps(raw)+')')
        page.goto(base+'/index.html')
        page.locator('#feed a.card').first.wait_for()
        # The existing reading filter is on the latest-news view. Carry the
        # selection into search; do not add a new layout to this bug repair.
        page.locator('#readingStatus').select_option('unread')
        page.locator('#mobileTabs [data-view="search"]').click()
        page.locator('#search').fill('蘋果')
        page.wait_for_function("document.querySelector('#searchResultTitle').textContent.includes('1 篇匹配報道')")
        assert page.locator('#searchResultList > a').count() == 1
        assert page.locator('#searchResultList > a').get_attribute('href') == 'article.html?id=a78'
        assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == raw
        assert page.evaluate('RssReaderState.get("a78").saved') is True
        page.close()


def test_source_filter_and_query_can_find_a_globally_duplicate_member(browser, news_data):
    data = news_data
    data['articles'][1].update(title='獨有詞的國際版本',source='東網 國際',category='國際',duplicate_of='a0')
    data['sources']['東網 國際'] = {'count':1,'effective_count':1,'category':'國際'}
    with _static_server() as base:
        page = browser.new_page(viewport={'width':1280,'height':900}, service_workers='block')
        _route_data(page,data)
        page.goto(base+'/index.html')
        page.locator('#feed a.card').first.wait_for()
        page.locator('#search').fill('獨有詞')
        page.wait_for_function("document.querySelector('#resultCount').textContent.includes('1 篇匹配報道')")
        assert '獨有詞的國際版本' in page.locator('#leadStory > a').inner_text()
        assert '1 篇匹配報道' in page.locator('#resultCount').inner_text()
        page.locator('#categoryNav [data-tree-category="全部"]').click()
        page.locator('#categoryNav [data-category="全部"][data-source="東網 國際"]').click()
        assert '獨有詞的國際版本' in page.locator('#leadStory').inner_text()
        page.close()


def test_composition_keeps_input_and_does_not_submit_half_composed_query(browser, news_data):
    with _static_server() as base:
        page = browser.new_page(viewport={'width':1280,'height':900},service_workers='block')
        _route_data(page,news_data)
        page.goto(base+'/index.html')
        page.locator('#feed a.card').first.wait_for()
        before = page.locator('#resultCount').inner_text()
        page.locator('#search').focus()
        page.locator('#search').dispatch_event('compositionstart')
        page.locator('#search').fill('沒有匹配的組字')
        page.wait_for_timeout(180)
        assert page.locator('#resultCount').inner_text() == before
        assert page.evaluate('document.activeElement.id') == 'search'
        page.locator('#search').dispatch_event('compositionend')
        page.wait_for_function("document.querySelector('#resultCount').textContent.includes('0 篇匹配報道')")
        page.close()


def test_pending_index_does_not_appear_as_a_confirmed_search_zero(browser, news_data):
    with _static_server() as base:
        page=browser.new_page(viewport={'width':1280,'height':900},service_workers='block')
        _route_data(page,news_data)
        pending=[]
        page.route('**/data/articles_index.json',lambda route:pending.append(route))
        page.goto(base+'/index.html',wait_until='domcontentloaded')
        page.locator('#search').fill('未收錄詞')
        page.wait_for_timeout(180)
        assert page.locator('#searchResultTitle').inner_text() == '正在準備搜尋'
        assert '0 篇' not in page.locator('#resultCount').inner_text()
        pending[0].fulfill(json=news_data)
        page.wait_for_function("document.querySelector('#searchResultTitle').textContent.includes('0 篇匹配報道')")
        page.close()


@pytest.mark.parametrize('width', [390,1280])
def test_captioned_hero_stays_with_figure_and_caption_visible_in_text_mode(browser, news_data, width):
    body = '<figure><img src="https://example.com/photo.jpg" alt="0"><figcaption>圖片來源：官方圖片</figcaption></figure><p>圖後正文。</p><img src="https://example.com/image?id=one"><p>中間段落。</p><img src="https://example.com/image?id=two">'
    digest = hashlib.sha256(body.encode()).hexdigest()
    news_data['articles'][0].update(thumbnail='https://example.com/photo.jpg', content_hash=digest, id_aliases=['old-slug-id'])
    with _static_server() as base:
        page = browser.new_page(viewport={'width':width,'height':900}, service_workers='block')
        _route_data(page,news_data)
        page.route('**/data/content/**', lambda route: route.fulfill(json={'version':1,'content':body,'content_hash':digest}))
        page.goto(base+'/article.html?id=old-slug-id')
        page.locator('#content figure img').wait_for(state='attached')
        assert page.locator('#hero img').count() == 0
        assert page.locator('#content img').count() == 3
        assert page.locator('#content figure').inner_text() == '圖片來源：官方圖片'
        page.evaluate("document.body.classList.add('text-only')")
        assert page.locator('#content figcaption').is_visible()
        assert not page.locator('#content figure img').is_visible()
        page.close()
