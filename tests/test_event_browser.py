from test_browser_smoke import browser, news_data, _static_server
import pytest


@pytest.mark.parametrize('width', [390, 1280])
def test_event_reading_and_local_status_filters(browser, news_data, width):
    news_data['trending_topics'] = [{'topic':'共同話題','cluster_id':'c','article_ids':['a0','a1']}]
    for article in news_data['articles']:
        article['cluster_id'] = 'c'
        article['content_hash'] = 'a' * 64
    news_data['articles'][1]['source'] = '第二來源'
    news_data['sources']['故障來源'] = {'fetch_status':'failed','error':'503 <img src=x onerror=alert(1)>','checked_at':'2026-10-12T00:00:00Z','effective_count':2}
    news_data['sources']['安靜來源'] = {'fetch_status':'ok','fresh_count':0,'checked_at':'2026-10-12T00:00:00Z'}
    with _static_server() as base:
        context = browser.new_context(viewport={'width':width,'height':900},service_workers='block')
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        support_hash = 'a' * 64
        def route(r):
            if r.request.url.split('?')[0].endswith(('articles_index.json','articles.json')):
                r.fulfill(json=news_data)
            elif 'panel_digests.json' in r.request.url:
                r.fulfill(json={'c':{'digest':{'shared_facts':[{'text':'共同事实','evidence':[{'article_id':'a0','quote':'共同事实','verified':True,'content_hash':support_hash},{'article_id':'a1','quote':'共同事实補充','verified':True,'content_hash':support_hash}]}]}}})
            else:
                r.fulfill(status=404,body='')
        page.route('**/data/**',route)
        page.goto(base+'/index.html',wait_until='domcontentloaded')
        page.locator('.event-reading').wait_for(state='attached')
        page.locator('.event-reading').first.evaluate('(el) => el.open = true')
        assert '共同事实' in page.locator('#eventReadingList').inner_text()
        assert '暫定' in page.locator('#eventReadingList').inner_text()
        health = page.locator('#sideSourceHealth').text_content()
        assert '故障來源：觀察已過期；最近一次抓取失敗' in health and '原因：503 <img' in health
        assert '安靜來源：觀察已過期；最近一次抓取 0 篇' in health
        assert '檢查時間' in health
        assert page.locator('#sideSourceHealth img').count() == 0
        assert page.locator('#eventReadingList a[href^="article.html?id=a0&quote="]').count() == 1
        page.evaluate("RssReaderState.saveLater('a1',true);RssReaderState.markRead('a0',true)")
        page.locator('#readingStatus').select_option('saved')
        assert page.locator('#resultCount').inner_text() == '1 篇匹配報道 · 1 張卡'
        assert page.locator('#feed a.card').count() == 0
        page.locator('#readingStatus').select_option('unread')
        assert page.locator('#resultCount').inner_text() == '2 篇匹配報道 · 2 張卡'
        assert page.locator('#leadStory a').first.get_attribute('href') == 'article.html?id=a1'
        assert page.locator('#feed a.card').count() == 1
        page.locator('#readingStatus').select_option('read')
        assert page.locator('#resultCount').inner_text() == '1 篇匹配報道 · 1 張卡'
        page.reload(wait_until='domcontentloaded')
        page.locator('#readingStatus').select_option('saved')
        assert page.locator('#resultCount').inner_text() == '1 篇匹配報道 · 1 張卡'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        support_hash = 'b' * 64
        page.reload(wait_until='domcontentloaded')
        page.locator('.event-reading').wait_for(state='attached')
        assert '共同事实' not in page.locator('#eventReadingList').text_content()
        assert '尚無可核對的來源分析' in page.locator('#eventReadingList').text_content()
        assert not errors
        context.close()
