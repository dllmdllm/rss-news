"""Local fixtures, mocked browser speech; no network TTS or production data."""
import pytest
from test_browser_smoke import _body_record, browser, news_data, _route_data, _static_server


def _long_body(page, news_data):
    record = _body_record(news_data, {
        "content": "<p>今日有<strong>重要</strong>消息。</p>" + "".join(f"<p>第{i}段。" + "長文章內容。" * 30 + "</p>" for i in range(40))})
    page.route('**/data/content/**', lambda route: route.fulfill(json=record))


def _speech(page):
    page.add_init_script('''
      window.ttsCalls=[];
      window.speechSynthesis.getVoices=()=>[];
      window.speechSynthesis.speak=u=>{window.currentUtterance=u;ttsCalls.push(u.text)};
      window.speechSynthesis.cancel=()=>{window.didCancel=true};
      window.speechSynthesis.pause=()=>{};window.speechSynthesis.resume=()=>{};
    ''')


@pytest.mark.parametrize('width', [390, 1280])
def test_local_status_preferences_and_manual_resume_without_autoplay(browser, news_data, width):
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        page = context.new_page()
        _route_data(page, news_data)
        _long_body(page, news_data)
        _speech(page)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        page.locator('#readerSaveLater').click()
        page.locator('#readerRead').click()
        page.locator('#fontSmall').click()
        page.locator('#highlightToggle').click()
        page.locator('#articleTtsRate').select_option('1.2')
        page.evaluate("scrollTo(0, document.querySelector('#content').offsetTop + 1500)")
        page.wait_for_function("RssReaderState.articleState('a0').position > 0")
        position = page.evaluate("RssReaderState.articleState('a0').position")
        page.reload()
        page.locator('#readerResume').wait_for()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'true'
        assert page.locator('#readerRead').get_attribute('aria-pressed') == 'true'
        assert page.locator('#fontSmall').get_attribute('aria-pressed') == 'true'
        assert page.locator('#highlightToggle').get_attribute('aria-pressed') == 'false'
        assert page.locator('#articleTtsRate').input_value() == '1.2'
        assert page.evaluate('ttsCalls.length') == 0
        assert page.evaluate("RssReaderState.articleState('a0').position") == position
        page.locator('#readerResume').click()
        assert page.evaluate('scrollY') > 1000
        page.locator('#readerSaveLater').click()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'false'
        context.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_chunk_seek_pause_navigation_and_saved_queue_are_silent_on_reload(browser, news_data, width):
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        page = context.new_page()
        _route_data(page, news_data)
        _long_body(page, news_data)
        _speech(page)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        assert int(page.locator('#articleTtsSeek').get_attribute('max')) > 5
        page.locator('#articleTtsNext').click()
        assert page.locator('#articleTtsSeek').input_value() == '1'
        assert page.evaluate('ttsCalls.length') == 0
        page.locator('#articleTts').click()
        assert page.evaluate('ttsCalls.length') == 1
        first = page.evaluate('ttsCalls[0]')
        page.evaluate('window.oldUtterance=currentUtterance')
        page.locator('#articleTtsNext').click()
        assert page.evaluate('ttsCalls.length') == 2
        assert page.evaluate('ttsCalls[1]') != first
        page.evaluate('oldUtterance.onend()')
        assert page.evaluate('ttsCalls.length') == 2
        page.locator('#articleTtsPause').click()
        page.locator('#articleTtsPrevious').click()
        assert page.evaluate('ttsCalls.length') == 2
        assert page.locator('#articleTtsPause').is_disabled()
        index = page.locator('#articleTtsSeek').input_value()
        page.reload()
        page.locator('#content strong').wait_for()
        assert page.locator('#articleTtsSeek').input_value() == index
        assert page.evaluate('ttsCalls.length') == 0
        page.locator('#articleTts').click()
        page.evaluate("dispatchEvent(new Event('pagehide'))")
        assert page.evaluate('didCancel')
        assert page.locator('#articleTts').get_attribute('aria-pressed') == 'false'
        context.close()


def test_denied_storage_keeps_current_page_state_and_discloses_limit(browser, news_data):
    with _static_server() as base:
        context = browser.new_context(viewport={'width': 390, 'height': 900}, service_workers='block')
        page = context.new_page()
        _route_data(page, news_data)
        _speech(page)
        page.add_init_script("Storage.prototype.getItem=Storage.prototype.setItem=()=>{throw new DOMException('blocked','SecurityError')}")
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        page.locator('#readerSaveLater').click()
        page.locator('#readerRead').click()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'true'
        assert page.evaluate("RssReaderState.articleState('a0').read")
        assert '本次頁面' in page.locator('#readerLocalStatus').inner_text()
        page.reload()
        page.locator('#content strong').wait_for()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'false'
        assert page.evaluate('ttsCalls.length') == 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        context.close()


def test_loaded_reader_keeps_local_actions_offline_and_isolates_articles(browser, news_data):
    with _static_server() as base:
        context = browser.new_context(viewport={'width': 390, 'height': 900}, service_workers='block')
        page = context.new_page()
        _route_data(page, news_data)
        _speech(page)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        context.set_offline(True)
        page.locator('#readerSaveLater').click()
        page.locator('#readerRead').click()
        page.locator('#articleTts').click()
        page.evaluate('window.oldUtterance = currentUtterance')
        assert page.evaluate('ttsCalls.length') == 1
        page.locator('#articleTts').click()
        page.evaluate('oldUtterance.onend()')
        assert page.evaluate('ttsCalls.length') == 1
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'true'
        context.set_offline(False)
        page.goto(base + '/article.html?id=a1')
        page.locator('#content strong').wait_for()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'false'
        assert page.locator('#readerRead').get_attribute('aria-pressed') == 'false'
        assert page.evaluate('ttsCalls.length') == 0
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        assert page.locator('#readerRead').get_attribute('aria-pressed') == 'true'
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'true'
        assert page.evaluate('ttsCalls.length') == 0
        context.close()


def test_completed_queue_shows_last_segment_then_replays_only_on_click(browser, news_data):
    with _static_server() as base:
        context = browser.new_context(service_workers='block')
        page = context.new_page()
        _route_data(page, news_data)
        _long_body(page, news_data)
        _speech(page)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        total = int(page.locator('#articleTtsSeek').get_attribute('max')) + 1
        page.locator('#articleTts').click()
        page.evaluate('(total) => { for(let i=0;i<total;i++) currentUtterance.onend(); }', total)
        assert page.locator('#articleTtsStatus').inner_text() == '朗讀完畢'
        assert page.locator('#articleTtsProgress').inner_text() == f'{total} / {total} 段'
        assert page.evaluate("RssReaderState.articleState('a0').tts.index") == 0
        count = page.evaluate('ttsCalls.length')
        page.locator('#articleTts').click()
        assert page.locator('#articleTtsProgress').inner_text() == f'1 / {total} 段'
        assert page.evaluate('ttsCalls.length') == count + 1
        assert page.evaluate('ttsCalls[0] === ttsCalls[ttsCalls.length - 1]')
        page.reload()
        page.locator('#content strong').wait_for()
        assert page.evaluate('ttsCalls.length') == 0
        page.locator('#articleTts').click()
        assert page.locator('#articleTtsProgress').inner_text() == f'1 / {total} 段'
        assert page.evaluate('ttsCalls.length') == 1
        context.close()


def test_two_tabs_sync_read_saved_labels_without_resetting_position_or_speech(browser, news_data):
    with _static_server() as base:
        context = browser.new_context(service_workers='block')
        first, second = context.new_page(), context.new_page()
        for page in (first, second):
            _route_data(page, news_data)
            _long_body(page, news_data)
            _speech(page)
            page.goto(base + '/article.html?id=a0')
            page.locator('#content strong').wait_for()
        first.locator('#articleTtsNext').click()
        first.evaluate("scrollTo(0, document.querySelector('#content').offsetTop + 1500)")
        first.wait_for_function("RssReaderState.articleState('a0').position > 0")
        first.evaluate('window.didCancel=false')
        scroll = first.evaluate('scrollY')
        queue = first.locator('#articleTtsSeek').input_value()

        second.locator('#readerRead').click()
        first.wait_for_function("document.querySelector('#readerRead').getAttribute('aria-pressed') === 'true'")
        assert first.locator('#readerRead').inner_text() == '標記未讀'
        second.locator('#readerSaveLater').click()
        first.wait_for_function("document.querySelector('#readerSaveLater').getAttribute('aria-pressed') === 'true'")
        assert first.locator('#readerSaveLater').inner_text() == '已加入稍後閱讀'
        assert first.evaluate('scrollY') == scroll
        assert first.locator('#articleTtsSeek').input_value() == queue
        assert first.evaluate('ttsCalls.length') == 0
        assert not first.evaluate('didCancel')

        first.locator('#readerRead').click()
        second.wait_for_function("document.querySelector('#readerRead').getAttribute('aria-pressed') === 'false'")
        assert second.locator('#readerRead').inner_text() == '標記已讀'
        first.locator('#readerSaveLater').click()
        second.wait_for_function("document.querySelector('#readerSaveLater').getAttribute('aria-pressed') === 'false'")
        assert second.locator('#readerSaveLater').inner_text() == '稍後閱讀'
        assert not second.evaluate("RssReaderState.articleState('a0').read")
        assert not second.evaluate("RssReaderState.articleState('a0').saved")
        context.close()
