"""Fixture-only summary playback regressions: never call AI or live publishers."""
import copy
import os

import pytest

from test_browser_smoke import browser, news_data, _route_data, _static_server


@pytest.fixture
def player_data(news_data):
    data = copy.deepcopy(news_data)
    template = data['articles'][0]
    data['articles'] = [{**template, 'id': f'play{i}', 'title': f'新聞 {i}',
                         'summary': '\n'.join(f'・完整重點 {j}：{i} 呢段摘要保留所有內容，唔截字。' for j in range(8)),
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
    assert page.locator('.summary-play-card').count() == 10
    assert page.locator('.summary-play-card').first.locator('li').count() == 8
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


def test_hover_focus_background_and_view_switch_preserve_read_state(player_page):
    page = player_page
    before = page.evaluate('localStorage.getItem("rss_reader_state_v1")')
    page.locator('#summaryPlayTab').click(); ticks(page)
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
    ticks(page)
    assert position(page) == 0 and '減少動態' in page.locator('[data-player-status]').inner_text()
    page.locator('[data-player-toggle]').click(); ticks(page)
    assert position(page) > 0  # Explicit user override only.
    page.set_viewport_size({'width':390,'height':900})
    assert not page.locator('#desktopViewTabs').is_visible()
    assert not page.locator('#summaryPlayer').is_visible()
    assert page.locator('#feed').is_visible()
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.set_viewport_size({'width':1280,'height':900})
    at = position(page); ticks(page)
    assert position(page) == at


def test_existing_order_and_reading_filter_are_shared(player_page):
    page = player_page
    assert page.locator('.summary-play-card').count() == 0  # Lazy, no hidden duplicate list.
    page.locator('#summaryPlayTab').click()
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
        {**data['articles'][0], 'id':'safe', 'title':'<img src=x onerror=alert(1)>', 'source':'<svg onload=alert(1)>', 'summary':'・<script>window.injected=true</script>'},
        {**data['articles'][1], 'summary':''},
        {**data['articles'][2], 'summary':data['articles'][2]['title']},
    ]
    with _static_server() as base:
        context = browser.new_context(viewport={'width':1280,'height':900}, service_workers='block')
        page = context.new_page(); _route_data(page, data)
        page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new DOMException('denied','SecurityError')}})")
        page.goto(base + '/index.html', wait_until='domcontentloaded')
        page.locator('#feed .card').first.wait_for(); page.locator('#summaryPlayTab').click()
        assert page.locator('.summary-play-card').count() == 1
        assert page.locator('.summary-play-card script, .summary-play-card img, .summary-play-card svg').count() == 0
        assert page.evaluate('window.injected') is None
        assert '2 篇等候 AI' in page.locator('[data-player-count]').inner_text()
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
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors
        out = os.environ.get('RSS_PLAYER_SCREENSHOT')
        page.screenshot(path=out or str(tmp_path / 'summary-player.png'), full_page=False)
        context.close()
