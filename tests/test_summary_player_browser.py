"""Fixture-only summary playback regressions: never call AI or live publishers."""
import copy
import os
from pathlib import Path

import pytest

from test_browser_smoke import browser, news_data, _route_data, _static_server


@pytest.fixture
def player_data(news_data):
    data = copy.deepcopy(news_data)
    template = data['articles'][0]
    data['articles'] = [{**template, 'id': f'play{i}', 'title': f'新聞 {i}',
                         'summary': '\n'.join(f'・完整重點 {j}：{i} 呢段摘要保留所有內容，唔截字。' for j in range(8)),
                         'key_sentences': [f'已有關鍵句 {j}：{i} 呢段原有資料保留所有內容，唔截字。' for j in range(8)],
                         'score': i + 1,
                         'source': '本地來源' if i < 5 else '科技來源',
                         'category': '新聞' if i < 5 else '科技'} for i in range(10)]
    return data


@pytest.fixture
def player_page(browser, player_data):
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1280, 'height':900}, service_workers='block')
        page = context.new_page()
        _route_data(page, player_data)
        page.add_init_script("""
          window.frames = new Map(); window.frameId = 0; window.clock = 0;
          window.requestAnimationFrame = fn => { frames.set(++frameId, fn); return frameId; };
          window.cancelAnimationFrame = id => frames.delete(id);
          window.tick = (count, interval=20) => {
            for(let i=0; i<count; i++) { clock += interval; const jobs=[...frames.values()]; frames.clear(); jobs.forEach(fn=>fn(clock)); }
          };
        """)
        page.goto(base + '/index.html', wait_until='domcontentloaded')
        page.locator('#feed .card').first.wait_for()
        page.mouse.move(0, 0)
        yield page
        context.close()


def position(page):
    return page.locator('.summary-viewport').evaluate('(el)=>el.scrollTop')


def ticks(page, count=50, interval=20):
    page.evaluate('([n,ms])=>tick(n,ms)', [count, interval])


def test_full_summary_safe_render_and_fractional_scroll_timing(player_page):
    page = player_page
    page.locator('#summaryPlayTab').click()
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10', polling=20)
    assert page.locator('.summary-play-card').count() == 10
    assert page.locator('.summary-play-card').first.locator('li').count() == 8
    assert '已有關鍵句' in page.locator('.summary-play-card').first.inner_text()
    assert '完整重點' not in page.locator('#summaryPlayer').inner_text()
    assert page.locator('.summary-play-meta').first.inner_text().startswith('本地來源 · ')
    first_y = page.locator('.summary-play-card').first.bounding_box()['y']
    ticks(page, 51)
    assert 23 <= position(page) <= 25  # 24 px/s, including fractional frame increments.
    assert page.locator('.summary-play-card').first.bounding_box()['y'] < first_y
    page.locator('[data-player-toggle]').click()
    at = position(page); ticks(page)
    assert position(page) == at
    page.locator('[data-player-speed]').select_option('fast')
    page.locator('[data-player-toggle]').click()
    ticks(page, 51)
    assert 39 <= position(page) - at <= 41
    # Repeated pause/resume never creates parallel frame loops.
    for _ in range(4): page.locator('[data-player-toggle]').click()
    assert page.evaluate('frames.size') == 1
    page.locator('[data-player-restart]').click()
    assert position(page) == 0
    ticks(page, 51); at = position(page)
    page.evaluate('const now=Date.now(); Date.now=()=>now+120000; window.dispatchEvent(new CustomEvent("rss-reader-state",{detail:{statusChanged:true}}))')
    assert position(page) == at  # Relative-time updates don't reset the queue.
    assert page.locator('[data-player-toggle]').inner_text() == 'Ⅱ 暫停'


def test_slim_metadata_retry_stale_guard_and_sentence_search(browser, player_data):
    index = copy.deepcopy(player_data)
    for row in index['articles']:
        row.pop('key_sentences', None)
        row['content_hash'] = 'current'
    full = copy.deepcopy(player_data)
    for row in full['articles']: row['content_hash'] = row['key_sentences_content_hash'] = 'current'
    full['articles'][0]['content_hash'] = 'stale'
    full['articles'][1]['key_sentences'] = ['唯一可搜尋句', '唯一可搜尋句', '', None]
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1280,'height':900}, service_workers='block')
        page = context.new_page(); _route_data(page, index)
        requests = []
        def metadata(route):
            requests.append(route.request.url)
            if len(requests) == 1: route.fulfill(status=503, body='unavailable')
            else: route.fulfill(json=full)
        page.route('**/data/articles.json', metadata)
        page.goto(base + '/index.html'); page.locator('#feed .card').first.wait_for()
        assert not requests
        page.locator('#summaryPlayTab').click()
        page.locator('#keySentenceRetry').wait_for(state='visible')
        assert '讀取失敗' in page.locator('[data-player-status]').inner_text()
        assert page.locator('.summary-play-card').count() == 0
        page.locator('#newsListTab').click(); page.locator('#summaryPlayTab').click()
        page.locator('#search').fill('新聞'); page.locator('#search').fill('')
        assert len(requests) == 1  # Failed metadata requires explicit retry.
        page.locator('#keySentenceRetry').click()
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===9')
        assert '1 篇未有可用關鍵句' in page.locator('[data-player-count]').inner_text()
        assert '完整重點' not in page.locator('#summaryPlayer').inner_text()
        page.locator('#search').fill('唯一可搜尋句')
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===1')
        assert page.locator('.summary-play-card li').count() == 1
        page.locator('#search').fill('完整重點')
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===0')
        page.locator('#newsListTab').click()
        assert page.locator('#feed .card').count() > 0  # Ordinary list keeps summary search and pagination.
        page.locator('#search').fill(''); page.locator('#summaryPlayTab').click()
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===9')
        assert len(requests) == 2
        context.close()


def test_metadata_timeout_is_bounded_and_tab_switches_share_request(browser, player_data):
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1280,'height':900}, service_workers='block')
        page = context.new_page(); _route_data(page, player_data)
        page.add_init_script("""
          const originalFetch=window.fetch, originalTimeout=window.setTimeout;
          window.metadataCalls=0;
          window.setTimeout=(fn,ms,...args)=>{
            if(ms===15000) window.expireMetadata=fn;
            return originalTimeout(fn,ms,...args);
          };
          window.fetch=(url,options)=>{
            if(url==='data/articles.json' && ++window.metadataCalls===1)
              return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError'))));
            return originalFetch(url,options);
          };
        """)
        page.goto(base + '/index.html'); page.locator('#feed .card').first.wait_for()
        page.locator('#summaryPlayTab').click()
        for _ in range(3):
            page.locator('#newsListTab').click(); page.locator('#summaryPlayTab').click()
        assert page.evaluate('metadataCalls') == 1
        assert '正在讀取' in page.locator('[data-player-status]').inner_text()
        page.evaluate('expireMetadata()')
        page.locator('#keySentenceRetry').wait_for(state='visible')
        page.locator('#keySentenceRetry').click()
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10')
        assert page.evaluate('metadataCalls') == 2
        context.close()


def test_hover_focus_background_and_view_switch_preserve_read_state(player_page):
    page = player_page
    before = page.evaluate('localStorage.getItem("rss_reader_state_v1")')
    page.locator('#summaryPlayTab').click()
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10', polling=20)
    ticks(page)
    page.locator('.summary-viewport').hover()
    at = position(page); ticks(page)
    assert position(page) == at
    page.mouse.move(0, 0); ticks(page)
    assert position(page) > at
    page.locator('.summary-viewport').focus()
    at = position(page); ticks(page)
    assert position(page) == at
    page.locator('[data-player-speed]').focus(); ticks(page)
    assert position(page) > at
    page.evaluate('Object.defineProperty(document,"hidden",{configurable:true,value:true}); document.dispatchEvent(new Event("visibilitychange"))')
    at = position(page); ticks(page)
    page.evaluate('Object.defineProperty(document,"hidden",{configurable:true,value:false}); document.dispatchEvent(new Event("visibilitychange"))')
    ticks(page)
    assert position(page) == at and page.locator('[data-player-toggle]').inner_text() == '▶ 繼續'
    page.locator('[data-player-toggle]').click(); ticks(page)
    page.locator('#newsListTab').click()
    assert not page.locator('#summaryPlayer').is_visible()
    page.locator('#summaryPlayTab').click(); at = position(page); ticks(page)
    assert position(page) == at
    assert page.evaluate('localStorage.getItem("rss_reader_state_v1")') == before


def test_filter_empty_and_queue_loop_boundaries(player_page):
    page = player_page
    page.locator('#summaryPlayTab').click()
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10', polling=20)
    page.locator('#search').fill('科技來源')
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===5', polling=20)
    assert page.locator('.summary-play-card').count() == 5
    assert '已暫停' in page.locator('[data-player-status]').inner_text()
    page.locator('[data-player-toggle]').click()
    page.evaluate('const v=document.querySelector(".summary-viewport"); v.scrollTop=v.scrollHeight-v.clientHeight')
    page.locator('[data-player-speed]').select_option('fast')  # Capture manual bottom position.
    ticks(page, 102)
    assert position(page) < 2  # Two-second last-card hold, then loop, without cloned cards.
    assert page.locator('.summary-play-card').count() == 5
    page.locator('#search').fill('完全冇呢個搜尋')
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===0', polling=20)
    assert page.locator('.summary-play-card').count() == 0
    assert page.locator('[data-player-toggle]').is_disabled()
    ticks(page)
    assert page.evaluate('frames.size') == 0


def test_reduced_motion_keyboard_and_responsive_regression(player_page):
    page = player_page
    page.emulate_media(reduced_motion='reduce')
    page.locator('#newsListTab').focus(); page.keyboard.press('ArrowRight')
    assert page.locator('#summaryPlayTab').get_attribute('aria-selected') == 'true'
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10', polling=20)
    ticks(page)
    assert position(page) == 0 and '減少動態' in page.locator('[data-player-status]').inner_text()
    page.locator('[data-player-toggle]').click(); ticks(page)
    assert position(page) > 0  # Explicit user override only.
    page.set_viewport_size({'width':390,'height':900})
    # CSS responds synchronously, but matchMedia change callbacks are queued.
    # Wait for the real mobile transition before simulating animation frames;
    # two rapid viewport overrides can otherwise coalesce into no transition.
    page.wait_for_function('document.body.classList.contains("mobile-home") && !document.body.classList.contains("summary-mode")', polling=20)
    assert not page.locator('#desktopViewTabs').is_visible()
    assert not page.locator('#summaryPlayer').is_visible()
    assert page.locator('#feed').is_visible()
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.set_viewport_size({'width':1280,'height':900})
    page.wait_for_function('document.body.classList.contains("summary-mode") && document.querySelector("[data-player-toggle]").textContent.includes("繼續")', polling=20)
    at = position(page); ticks(page)
    assert position(page) == at


def test_existing_order_and_reading_filter_are_shared(player_page):
    page = player_page
    assert page.locator('.summary-play-card').count() == 0  # Lazy, no hidden duplicate list.
    page.locator('#summaryPlayTab').click()
    page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10', polling=20)
    assert page.locator('.summary-play-card h2').first.inner_text() == '新聞 0'
    page.locator('#modeNav [data-mode="balanced"]').click()
    assert page.locator('.summary-play-card h2').first.inner_text() == '新聞 9'
    page.locator('#readingStatus').select_option('read')
    assert page.locator('.summary-play-card').count() == 0
    page.locator('#readingStatus').select_option('all')
    assert page.locator('.summary-play-card').count() == 10
    page.locator('#categoryNav [data-tree-category="科技"]').click()
    assert page.locator('.summary-play-card').count() == 5
    page.locator('#categoryNav [data-source="科技來源"]').click()
    assert page.locator('.summary-play-card').count() == 5


def test_missing_summary_injection_and_single_short_card(browser, news_data):
    data = copy.deepcopy(news_data)
    data['articles'] = [
        {**data['articles'][0], 'id':'safe', 'title':'<img src=x onerror=alert(1)>', 'source':'<svg onload=alert(1)>', 'key_sentences':['<script>window.injected=true</script>']},
        {**data['articles'][1], 'key_sentences':[]},
        {**data['articles'][2], 'key_sentences':None},
    ]
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1280,'height':900}, service_workers='block')
        page = context.new_page(); _route_data(page, data)
        page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('denied','SecurityError')}})")
        page.goto(base + '/index.html', wait_until='domcontentloaded')
        page.locator('#feed .card').first.wait_for(); page.locator('#summaryPlayTab').click()
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===1')
        assert page.locator('.summary-play-card').count() == 1
        assert page.locator('.summary-play-card script, .summary-play-card img, .summary-play-card svg').count() == 0
        assert page.evaluate('window.injected') is None
        assert '2 篇未有可用關鍵句' in page.locator('[data-player-count]').inner_text()
        page.wait_for_function('document.querySelector("[data-player-status]").textContent.includes("全部顯示")')
        page.locator('[data-player-toggle]').click(); page.locator('[data-player-restart]').click()
        assert position(page) == 0
        context.close()


def test_summary_player_visual_capture(browser, player_data, tmp_path):
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1440,'height':1000}, service_workers='block', reduced_motion='reduce')
        page = context.new_page(); errors=[]
        page.on('pageerror', lambda error: errors.append(str(error)))
        _route_data(page, player_data)
        page.goto(base + '/index.html', wait_until='domcontentloaded'); page.locator('#feed .card').first.wait_for()
        # List layout must not overlap the new desktop tab row.
        tab = page.locator('#desktopViewTabs').bounding_box(); brief = page.locator('.brief').bounding_box()
        assert brief['y'] >= tab['y'] + tab['height']
        page.locator('#summaryPlayTab').click()
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10')
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors
        out = os.environ.get('RSS_PLAYER_SCREENSHOT')
        page.screenshot(path=out or str(tmp_path / 'summary-player.png'), full_page=False)
        context.close()


def test_thumbnails_left_safe_and_broken_layout_is_stable(browser, player_data):
    data = copy.deepcopy(player_data)
    urls = ['https://images.example.com/good.png', 'https://images.example.com/broken.png', None,
            'javascript:alert(1)', 'data:image/svg+xml,bad', 'http://127.0.0.1/private',
            'https://user:secret@images.example.com/private', 'http://localhost/private',
            'https://private.internal/image', 'https://images.example.com:8443/private']
    for article, url in zip(data['articles'], urls): article['thumbnail'] = url
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1440,'height':1000}, service_workers='block')
        page = context.new_page(); _route_data(page, data)
        pending = []
        def image(route):
            if route.request.url.endswith('good.png'):
                route.fulfill(path=str(Path(__file__).resolve().parents[1] / 'docs/icon-180.png'))
            else:
                pending.append(route)
        def guarded(route):
            if route.request.resource_type == 'image':
                if route.request.url.startswith('https://images.example.com/'):
                    image(route)
                else:
                    route.abort()  # Hostile fixture thumbnails must never contact a real destination.
            else:
                route.fallback()
        page.route('**/*', guarded)
        page.goto(base + '/index.html', wait_until='domcontentloaded')
        page.locator('#feed .card').first.wait_for(); page.locator('#summaryPlayTab').click()
        page.wait_for_function('document.querySelectorAll(".summary-play-card").length===10')
        page.locator('[data-player-toggle]').click()  # Freeze while checking image loads.
        cards = page.locator('.summary-play-card')
        assert cards.locator('img').count() == 2
        img = cards.first.locator('img')
        page.wait_for_function('document.querySelector(".summary-play-media img").naturalWidth>0')
        assert img.get_attribute('loading') == 'lazy'
        assert img.get_attribute('decoding') == 'async'
        assert img.get_attribute('referrerpolicy') == 'no-referrer'
        media = cards.first.locator('.summary-play-media').bounding_box()
        text = cards.first.locator('.summary-play-body').bounding_box()
        assert media['x'] + media['width'] < text['x'] and media['width'] > 100
        broken = cards.nth(1).locator('.summary-play-media')
        before = broken.bounding_box()
        broken.scroll_into_view_if_needed()
        for _ in range(50):
            if pending: break
            page.wait_for_timeout(20)
        assert pending
        for route in pending: route.fulfill(status=404, body='')
        page.wait_for_function('document.querySelectorAll(".summary-play-media")[1].textContent.includes("未能載入")')
        after = broken.bounding_box()
        assert abs(before['width'] - after['width']) < 1 and abs(before['height'] - after['height']) < 1
        assert cards.nth(2).locator('.summary-play-media').inner_text() == '未有圖片'
        assert cards.nth(5).locator('img').count() == 0
        page.set_viewport_size({'width':1024,'height':900})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert cards.first.locator('.summary-play-body').bounding_box()['width'] > 300
        context.close()


def test_mobile_long_headline_wraps_without_overflow(browser, news_data):
    data = copy.deepcopy(news_data)
    data['articles'][1]['title'] = '手機新聞 ' + 'LongUnbrokenPublisherHeadline' * 8
    with _static_server() as base:
        context = browser.new_context(viewport={'width':390,'height':900}, service_workers='block')
        page=context.new_page(); _route_data(page,data)
        page.goto(base+'/index.html',wait_until='domcontentloaded'); page.locator('#feed .card').first.wait_for()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert not page.locator('#summaryPlayer').is_visible()
        context.close()
