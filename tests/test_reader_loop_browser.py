"""Actual homepage24h, device revision and explicit refresh integration."""
import copy
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

from test_browser_smoke import browser, news_data, _route_data, _static_server


def _snapshot(news_data, count=80):
    now = datetime.now(timezone.utc)
    data = copy.deepcopy(news_data)
    template = data['articles'][0]
    data['schema_version'] = 2
    data['updated'] = '2026-10-22 21:00 HKT'
    data['articles'] = [dict(copy.deepcopy(template), id=f'a{i}', title=f'香港來源報道 {i}',
                             date=(now-timedelta(hours=1, minutes=i)).isoformat(), topic='')
                        for i in range(count)]
    data['sources']['測試來源']['count'] = data['sources']['測試來源']['effective_count'] = count
    return data


def _route_fixture(context, base, holder):
    """All data mocked; only the local static server can receive GETs."""
    external = []

    def route_request(route):
        request = route.request
        url = urlparse(request.url)
        if not request.url.startswith(base+'/') or request.method != 'GET':
            external.append(request.url)
            route.abort()
        elif '/data/' in url.path:
            if url.path.endswith(('articles.json', 'articles_index.json')):
                route.fulfill(json=holder['payload'])
            else:
                route.fulfill(status=404, body='')
        else:
            route.continue_()

    context.route('**/*', route_request)
    return external


def _visible_feed_ids(page):
    return page.locator('#feed a.card, #leadStory > a').evaluate_all(
        '(nodes)=>nodes.filter(n=>n.getBoundingClientRect().width>0).map(n=>new URL(n.href).searchParams.get("id"))')


def _select_source(page, width):
    if width == 390:
        page.locator('#mobileSubHome [data-time-mode="category"]').click()
        page.locator('#mobileSourceChips [data-mobile-source="測試來源"]').click()
        page.locator('#mobileTabs [data-view="search"]').click()
    else:
        page.locator('#categoryNav [data-tree-category="全部"]').click()
        page.locator('#categoryNav [data-category="全部"][data-source="測試來源"]').click()


@pytest.mark.parametrize('width', [390, 1280])
def test_removed_read_later_controls_preserve_saved_data_and_reader_routes(browser, news_data, width):
    data = _snapshot(news_data, 4)
    data['articles'][0]['id_aliases'] = ['legacy-a0']
    older = dict(copy.deepcopy(data['articles'][0]), id='saved-25h', id_aliases=[], title='香港已收藏舊稿',
                 date=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat())
    data['articles'].append(older)
    raw = json.dumps({'saved-25h': {'saved': True, 'read': False, 'position': 0.43, 'updated': 1},
                      'legacy-a0': {'saved': True, 'read': False, 'position': 0.25, 'updated': 2}}, ensure_ascii=False)
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        external = _route_fixture(context, base, {'payload': data})
        context.add_init_script('if(localStorage.getItem("rss_reader_state_v1")===null)localStorage.setItem("rss_reader_state_v1",'+json.dumps(raw)+')')
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base+'/index.html')
        page.wait_for_function('!!window.rssSnapshotUpdates')
        page.locator('#feed a.card').first.wait_for()
        assert 'saved-25h' not in _visible_feed_ids(page)
        assert page.locator('.revision-label').count() == 0
        assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == raw
        assert page.locator('[data-save-later], .save-later, #readerSaveLater, option[value="saved"]').count() == 0
        assert page.get_by_text('稍後閱讀', exact=False).count() == 0
        assert page.locator('#readingStatus option').evaluate_all('(nodes)=>nodes.map(n=>n.value)') == ['all', 'unread', 'read']
        assert page.evaluate('RssReaderState.get("a0").saved') is True
        if width == 390:
            assert page.locator('#mobileTabs [data-view="ai"]').is_visible()
            page.locator('#mobileTabs [data-view="search"]').click()
        else:
            assert page.locator('#summaryPlayTab').is_visible()
        page.locator('#search').fill('香港')
        page.wait_for_function('document.querySelector("#searchResultTitle").textContent.includes("4 篇匹配報道")')
        assert page.locator('[data-save-later], .save-later, option[value="saved"]').count() == 0
        assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == raw
        assert page.evaluate('RssReaderState.get("saved-25h").position') == 0.43
        # Existing alias URLs and stored saved flags remain readable without
        # a migration, deletion, hidden save control or null-handler crash.
        _route_data(page, data)
        page.goto(base+'/article.html?id=legacy-a0')
        page.locator('#content strong').wait_for()
        assert page.locator('#title').inner_text() == data['articles'][0]['title']
        assert page.locator('#summaryBox').is_visible()
        assert page.locator('#readerRead').is_visible()
        assert page.locator('#readerResume').is_visible()
        assert page.locator('#readerSaveLater, [data-save-later], .save-later').count() == 0
        assert page.get_by_text('稍後閱讀', exact=False).count() == 0
        assert page.evaluate('RssReaderState.get("a0").saved') is True
        # The existing reader records its TTS cursor on initialization. It must
        # retain every prior saved/read/position record through that bookkeeping.
        stored = json.loads(page.evaluate('localStorage.getItem("rss_reader_state_v1")'))
        for aid, record in json.loads(raw).items():
            assert {key: stored[aid][key] for key in ('saved', 'read', 'position')} == {
                key: record[key] for key in ('saved', 'read', 'position')}
        assert not errors
        assert not external
        context.close()


@pytest.mark.parametrize('width', [390, 1280])
@pytest.mark.parametrize('new_count', [1, 12])
def test_homepage_pending_refresh_preserves_deep_anchor_query_source_and_focus(browser, news_data, width, new_count):
    data = _snapshot(news_data)
    for i in range(4):
        data['articles'].append(dict(copy.deepcopy(data['articles'][0]), id=f'other{i}', source='另一來源', title=f'香港另一來源 {i}'))
    data['sources']['另一來源'] = {'count': 4, 'effective_count': 4, 'category': '新聞'}
    next_data = copy.deepcopy(data)
    next_data['updated'] = '2026-10-22 21:01 HKT'
    next_data['articles'][4].update(title='香港來源報道 4（來源修訂）', content_hash='b'*64)
    for i in range(new_count):
        added = dict(copy.deepcopy(data['articles'][0]), id=f'new-arrival-{i}', title=f'香港新增來源報道 {i}',
                     date=(datetime.now(timezone.utc)-timedelta(minutes=5, seconds=i)).isoformat())
        next_data['articles'].insert(0, added)
    holder = {'payload': data}
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        external = _route_fixture(context, base, holder)
        page = context.new_page()
        page.set_default_timeout(5000)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base+'/index.html')
        page.wait_for_function('!!window.rssSnapshotUpdates')
        _select_source(page, width)
        page.locator('#search').fill('香港')
        page.wait_for_function('document.querySelector("#searchResultTitle").textContent.includes("80 篇匹配報道")')
        if width == 390:
            page.locator('[data-search-more]').click()
            page.locator('[data-search-more]').click()
            assert page.locator('#searchResultList > a').count() == 60
            anchor_selector = '#searchResultList > a[href="article.html?id=a45"]'
            shown = page.locator('#searchResultList > a').count()
        else:
            for _ in range(5):
                if page.locator('#feed a.card[href="article.html?id=a45"]').count():
                    break
                page.evaluate('scrollTo(0,document.body.scrollHeight)')
                page.wait_for_timeout(150)
            anchor_selector = '#feed a.card[href="article.html?id=a45"]'
            shown = page.locator('#feed a.card').count()
            assert shown >= 45
        anchor = page.locator(anchor_selector)
        anchor.wait_for()
        anchor.evaluate('(node)=>scrollBy(0,node.getBoundingClientRect().top-110)')
        page.evaluate('document.querySelector("#search").focus({preventScroll:true});document.querySelector("#search").setSelectionRange(0,1)')
        before_top = anchor.bounding_box()['y']
        before_scroll = page.evaluate('scrollY')
        captured = page.evaluate('''() => {
          const node=[...document.querySelectorAll('#feed a.card, #leadStory > a, #searchResultList > a')].find(n=>n.getBoundingClientRect().bottom>0);
          return {href:node?.getAttribute('href'),top:node?.getBoundingClientRect().top};
        }''')
        before_feed = page.locator('#feed').inner_html()
        before_search = page.locator('#searchResultList').inner_html()
        holder['payload'] = next_data
        result = page.evaluate('rssSnapshotUpdates.check()')
        assert result == {'status': 'pending', 'newCount': new_count, 'updatedCount': 1}
        assert f'新增 {new_count} 篇 · 更新 1 篇' in page.locator('#updateMessage').inner_text()
        assert page.locator('#updatePrompt').is_visible()
        assert page.locator('#updatePrompt').evaluate('node=>getComputedStyle(node).position') == 'fixed'
        assert page.locator('#feed').inner_html() == before_feed
        assert page.locator('#searchResultList').inner_html() == before_search
        assert page.evaluate('scrollY') == before_scroll
        assert abs(anchor.bounding_box()['y']-before_top) < 1
        assert page.evaluate('document.activeElement.id') == 'search'
        page.locator('#applySnapshotUpdate').click()
        page.wait_for_function('rssSnapshotUpdates.pending()===null')
        page.wait_for_function('(count)=>document.querySelector("#searchResultTitle").textContent.includes(count+" 篇匹配報道")', arg=80+new_count)
        # Let the browser finish scroll anchoring/clamping after both restore frames.
        page.wait_for_timeout(180)
        assert page.locator('#search').input_value() == '香港'
        assert page.evaluate('document.activeElement.id') == 'search'
        assert page.evaluate('document.querySelector("#search").selectionStart') == 0
        assert page.evaluate('document.querySelector("#search").selectionEnd') == 1
        after_capture = page.evaluate('''view => {
          const node=[...document.querySelectorAll('#feed a.card, #leadStory > a, #searchResultList > a')].find(n=>n.getAttribute('href')===view.href && n.getBoundingClientRect().width>0);
          return {href:node?.getAttribute('href'),top:node?.getBoundingClientRect().top,scrollY,scrollHeight:document.documentElement.scrollHeight};
        }''', captured)
        assert abs(anchor.bounding_box()['y']-before_top) < 2, {'selected_before': before_top, 'captured_before': captured, 'captured_after': after_capture}
        active_source = '#mobileSourceChips .active' if width == 390 else '#categoryNav [data-category="全部"][data-source="測試來源"].active'
        assert '測試來源' in page.locator(active_source).inner_text()
        if width == 390:
            assert page.locator('#searchResultList > a').count() >= shown
            assert page.locator('body').get_attribute('class').find('mobile-search') >= 0
        else:
            assert page.locator('#feed a.card').count() >= 45
        assert page.locator('#updatePrompt').is_hidden()
        assert not errors
        assert not external
        context.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_homepage_revisit_marks_new_and_updated_but_preserves_alias_identity(browser, news_data, width):
    first = _snapshot(news_data, 4)
    next_data = copy.deepcopy(first)
    next_data['updated'] = '2026-10-22 21:01 HKT'
    next_data['articles'][1].update(id='stable-a1', id_aliases=['a1'])
    next_data['articles'][2].update(content_hash='b'*64)
    added = dict(copy.deepcopy(first['articles'][0]), id='new-arrival', title='香港新增報道',
                 date=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat())
    next_data['articles'].insert(0, added)
    holder = {'payload': first}
    raw = json.dumps({'a1': {'saved': True, 'read': False, 'position': 0.43, 'updated': 1}})
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        external = _route_fixture(context, base, holder)
        context.add_init_script('if(localStorage.getItem("rss_reader_state_v1")===null)localStorage.setItem("rss_reader_state_v1",'+json.dumps(raw)+')')
        page = context.new_page()
        page.goto(base+'/index.html')
        page.wait_for_function('!!window.rssSnapshotUpdates')
        page.locator('#feed a.card').first.wait_for()
        assert page.locator('.revision-label').count() == 0
        holder['payload'] = next_data
        page.reload()
        page.wait_for_function('!!window.rssSnapshotUpdates')
        if width == 390:
            page.locator('#mobileTabs [data-view="search"]').click()
        page.locator('#search').fill('香港')
        page.wait_for_function('document.querySelector("#searchResultTitle").textContent.includes("5 篇匹配報道")')
        def badges(aid):
            selector = f'#searchResultList > a[href="article.html?id={aid}"]' if width == 390 else f'#feed a.card[href="article.html?id={aid}"], #leadStory > a[href="article.html?id={aid}"]'
            return page.locator(selector).locator('.revision-label').all_text_contents()
        assert badges('new-arrival') == ['新增']
        assert badges('a2') == ['已更新']
        assert badges('stable-a1') == []
        assert page.evaluate('RssReaderState.get("stable-a1").saved') is True
        assert page.evaluate('RssReaderState.get("stable-a1").position') == 0.43
        assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == raw
        assert not external
        context.close()
