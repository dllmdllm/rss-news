"""Browser lifecycle/state regressions; local fixtures, no physical audio claim."""
import functools
import http.server
import json
from pathlib import Path
import re
import threading

import pytest
from test_browser_smoke import browser, news_data, _route_data, _static_server


@pytest.mark.parametrize('action', ['stop', 'seek', 'mode', 'rate'])
def test_paused_global_engine_restarts_only_on_play(browser, news_data, action):
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        _route_data(page, news_data)
        page.add_init_script('''
          window.enginePaused=false; window.starts=0;
          Object.defineProperty(speechSynthesis,'paused',{get:()=>enginePaused});
          speechSynthesis.getVoices=()=>[];
          speechSynthesis.pause=()=>{enginePaused=true};
          speechSynthesis.resume=()=>{enginePaused=false};
          speechSynthesis.cancel=()=>{};
          speechSynthesis.speak=u=>{window.currentUtterance=u;if(!enginePaused){starts++;u.onstart?.()}};
        ''')
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        assert page.evaluate('starts') == 0
        page.locator('#articleTts').click()
        page.locator('#articleTtsPause').click()
        if action == 'stop': page.locator('#articleTts').click()
        elif action == 'seek': page.locator('#articleTtsSeek').dispatch_event('change')
        else: page.locator('#articleTtsMode' if action == 'mode' else '#articleTtsRate').select_option('summary' if action == 'mode' else '1.2')
        assert page.evaluate('enginePaused && starts === 1')
        page.locator('#articleTts').click()
        assert page.evaluate('!enginePaused && starts === 2')
        assert page.locator('#articleTtsStatus').inner_text() == '朗讀中'
        page.close()


def test_no_speech_start_has_bounded_honest_status(browser, news_data):
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        page.clock.install()
        _route_data(page, news_data)
        page.add_init_script('speechSynthesis.speak=()=>{};speechSynthesis.cancel=()=>{};')
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        page.locator('#articleTts').click()
        assert page.locator('#articleTtsStatus').inner_text() == '正在啟動朗讀'
        page.clock.fast_forward(5100)
        assert '未能開始' in page.locator('#articleTtsStatus').inner_text()
        assert page.locator('#articleTts').get_attribute('aria-pressed') == 'false'
        page.close()


def test_stalled_optional_requests_never_gate_primary_body(browser, news_data):
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        _route_data(page, news_data)
        for name in ('panel_digests', 'similar'):
            page.route(f'**/data/{name}.json', lambda route: None)
        page.goto(base + '/article.html?id=a0', wait_until='domcontentloaded')
        page.locator('#content strong').wait_for(timeout=2500)
        assert page.locator('#title').inner_text() == '測試新聞 0'
        page.close()


@pytest.mark.parametrize('failure', ['mismatch', 'missing_version', 'forged_body', 'invalid_json', 'offline'])
def test_bad_sidecar_preserves_metadata_with_clear_body_failure(browser, news_data, failure):
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        _route_data(page, news_data)
        record = dict(version=1, content_hash=news_data['articles'][0]['content_hash'], content='<p>Wrong body.</p>')
        if failure == 'mismatch': record['content_hash'] = 'b'*64
        if failure == 'missing_version': record.pop('version')
        def respond(route):
            if failure == 'invalid_json': route.fulfill(status=200, body='{broken')
            elif failure == 'offline': route.abort()
            else: route.fulfill(json=record)
        page.route('**/data/content/**', respond)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content .error').wait_for()
        assert page.locator('#title').inner_text() == '測試新聞 0'
        assert '第一個重點' in page.locator('#summaryBox').inner_text()
        assert 'Wrong body' not in page.locator('#content').inner_text()
        assert page.locator('#sourceLink').get_attribute('href') == 'https://example.com/news'
        page.locator('#readerSaveLater').click()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'true'
        page.close()


def test_stalled_body_times_out_without_losing_summary(browser, news_data):
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        page.clock.install()
        _route_data(page, news_data)
        page.route('**/data/content/**', lambda route: None)
        page.goto(base + '/article.html?id=a0', wait_until='domcontentloaded')
        page.get_by_text('測試新聞 0', exact=True).wait_for()
        assert '第一個重點' in page.locator('#summaryBox').inner_text()
        page.clock.fast_forward(8100)
        page.locator('#content .error').wait_for()
        page.close()


def test_unsafe_source_link_is_inert(browser, news_data):
    news_data['articles'][0]['url'] = 'javascript:alert(1)'
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        _route_data(page, news_data)
        page.goto(base + '/article.html?id=a0')
        page.locator('#content strong').wait_for()
        assert page.locator('#sourceLink').get_attribute('href') is None
        page.close()


@pytest.mark.parametrize('missing', [True, False])
def test_crypto_verification_failure_preserves_metadata_fail_closed(browser, news_data, missing):
    with _static_server() as base:
        page = browser.new_page(service_workers='block')
        _route_data(page, news_data)
        page.add_init_script("Object.defineProperty(crypto,'subtle',{value:undefined})" if missing else
                             "crypto.subtle.digest=async()=>{throw new Error('unavailable')}")
        page.goto(base + '/article.html?id=a0')
        page.locator('#content .error').wait_for()
        assert page.locator('#title').inner_text() == '測試新聞 0'
        assert page.locator('#sourceLink').get_attribute('href') == 'https://example.com/news'
        page.locator('#readerSaveLater').click()
        assert page.locator('#readerSaveLater').get_attribute('aria-pressed') == 'true'
        page.close()


def test_real_sw_failed_upgrade_preserves_old_offline_shell_and_other_caches(browser, tmp_path):
    worker = (Path(__file__).parents[1] / 'docs/sw.js').read_text(encoding='utf-8')
    shell = re.findall(r'"(\./[^"\n]*)"', worker.split('];', 1)[0])
    for item in shell:
        path = tmp_path / (item[2:] or 'index.html')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<title>Offline shell</title><body>Working shell</body>' if path.suffix == '.html' else '/* fixture */', encoding='utf-8')
    (tmp_path / 'sw.js').write_text(worker.replace('v287', 'v286'), encoding='utf-8')
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            # Same-size versions written within one second must not be hidden
            # by this fixture server's second-resolution Last-Modified check.
            if self.path == '/sw.js' and 'If-Modified-Since' in self.headers:
                del self.headers['If-Modified-Since']
            super().do_GET()
    handler = functools.partial(Handler, directory=str(tmp_path))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    context = browser.new_context(service_workers='allow'); page = context.new_page()
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
        page.evaluate("async()=>{await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready}")
        page.reload()
        page.wait_for_function('navigator.serviceWorker.controller !== null')
        page.evaluate("async()=>{const c=await caches.open('other-app-v1');await c.put('/other',new Response('preserve'))}")
        style = tmp_path / 'css/categories.css'; style.unlink()
        (tmp_path / 'sw.js').write_text(worker, encoding='utf-8')
        page.evaluate('''async()=>{
          const r=await navigator.serviceWorker.getRegistration();
          await new Promise(async(resolve,reject)=>{
            const timer=setTimeout(()=>reject(new Error('no failed install')),10000);
            r.addEventListener('updatefound',()=>{const w=r.installing;w.addEventListener('statechange',()=>{if(w.state==='redundant'){clearTimeout(timer);resolve()}})},{once:true});
            await r.update();
          });
        }''')
        assert page.evaluate("async()=> (await caches.keys()).includes('rss-news-v286')")
        context.set_offline(True); page.reload()
        assert 'Working shell' in page.locator('body').inner_text()
        context.set_offline(False)
        style.write_text('/* restored */', encoding='utf-8')
        (tmp_path / 'sw.js').write_text(worker.replace('v287', 'v288'), encoding='utf-8')
        page.evaluate('async()=>{await (await navigator.serviceWorker.getRegistration()).update()}')
        page.wait_for_function("async()=> (await caches.keys()).includes('rss-news-v288') && !(await caches.keys()).includes('rss-news-v286')")
        assert page.evaluate("async()=> (await caches.keys()).includes('other-app-v1')")
    finally:
        context.close(); server.shutdown(); server.server_close(); thread.join(timeout=3)
