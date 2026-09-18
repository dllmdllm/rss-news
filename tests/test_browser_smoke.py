import contextlib
import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


@contextlib.contextmanager
def _static_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def browser():
    import os
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if os.environ.get("RSS_REQUIRE_BROWSER") == "1":
            pytest.fail("CI requires Playwright")
        pytest.skip("playwright is not installed")
    with sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:
            if os.environ.get("RSS_REQUIRE_BROWSER") == "1":
                pytest.fail(f"CI requires Chromium: {exc}")
            pytest.skip(f"Chromium unavailable: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def news_data():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    articles = [{
        "id": f"a{i}", "title": f"測試新聞 {i}", "source": "測試來源", "category": "新聞",
        "date": (now - timedelta(hours=i)).isoformat(), "summary": "・第一個重點\n・第二個重點",
        "score": 8, "tags": ["測試"], "sentiment": "neutral", "topic": f"測試話題{i}",
        "url": "https://example.com/news", "key_sentences": ["今日有重要消息。"],
    } for i in range(3)]
    return {"updated": "測試快照", "sources": {"測試來源": {"count": 3, "effective_count":3}},
            "trending_topics": [], "articles": articles}


def _route_data(page, data):
    from urllib.parse import urlparse
    def respond(route):
        path = urlparse(route.request.url).path
        if path.endswith(('articles.json', 'articles_index.json')):
            route.fulfill(json=data)
        elif '/content/' in path:
            route.fulfill(json={"version":1, "content":"<p>今日有<strong>重要</strong>消息。</p><p>其他內容。</p>"})
        else:
            route.fulfill(status=404, body='')
    page.route('**/data/**', respond)


@pytest.mark.parametrize('width', [390, 1280])
def test_index_and_article_pages_render_in_browser(browser, news_data, width):
    with _static_server() as base_url:
        context = browser.new_context(viewport={"width":width, "height":900}, service_workers='block')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        _route_data(page, news_data)
        page.goto(base_url + '/index.html', wait_until='domcontentloaded')
        page.locator('#feed a.card').first.wait_for()
        page.locator('#feed a.card').first.click()
        page.locator('#content strong').wait_for()
        assert page.locator('#title').inner_text().startswith('測試新聞')
        assert page.locator('#content').inner_text() == '今日有重要消息。\n\n其他內容。'
        assert page.locator('#content mark.key-sentence').all_text_contents() == ['今日有','重要','消息。']
        assert page.locator('#nextArticle').get_attribute('href').startswith('article.html?id=')
        assert page.locator('#sourceLink').get_attribute('href') == 'https://example.com/news'
        page.locator('#highlightToggle').click()
        assert page.locator('#highlightToggle').get_attribute('aria-pressed') == 'false'
        assert page.locator('#content strong').inner_text() == '重要'
        assert not errors
        context.close()


def test_article_tts_start_pause_stop_and_error(browser, news_data):
    with _static_server() as base_url:
        context = browser.new_context(service_workers='block')
        page = context.new_page()
        _route_data(page, news_data)
        page.add_init_script("""
          window.ttsCalls = [];
          window.speechSynthesis.speak = u => { window.currentUtterance = u; ttsCalls.push(u.text); };
          window.speechSynthesis.cancel = () => { window.cancelled = true; };
          window.speechSynthesis.pause = () => { window.didPause = true; };
          window.speechSynthesis.resume = () => { window.didResume = true; };
        """)
        page.goto(base_url + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        page.locator('#articleTts').click()
        assert '今日有重要消息' in page.evaluate('ttsCalls[0]')
        page.locator('#articleTtsPause').click()
        assert page.evaluate('window.didPause')
        page.locator('#articleTtsPause').click()
        assert page.evaluate('window.didResume')
        page.evaluate('window.oldUtterance = window.currentUtterance')
        page.locator('#articleTts').click()
        assert page.evaluate('window.cancelled')
        assert page.locator('#articleTtsPause').is_disabled()
        page.locator('#articleTtsMode').select_option('summary')
        page.locator('#articleTts').click()
        assert '第一個重點' in page.evaluate('ttsCalls[ttsCalls.length - 1]')
        count = page.evaluate('ttsCalls.length')
        page.evaluate('window.oldUtterance.onend()')
        assert page.evaluate('ttsCalls.length') == count
        page.evaluate('window.currentUtterance.onerror()')
        assert page.locator('#articleTtsStatus').inner_text() == '朗讀失敗，請再試'
        assert page.locator('#articleTtsPause').is_disabled()
        context.close()
