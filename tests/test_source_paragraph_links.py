"""Exact source links require a current body and one real paragraph."""
import hashlib
import copy
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from test_browser_smoke import browser, news_data, _static_server
from test_evidence_browser import BODY, load

QUOTE = '政府今日公布新的交通安排，明日起實施。'
HASH = hashlib.sha256(BODY.encode()).hexdigest()


def supported(page, **changes):
    article = {'id':'a','content':BODY,'content_hash':HASH,
               'content_quality':{'fallback':'none'}, 'key_sentences':[QUOTE],
               'key_sentences_content_hash':HASH,
               'summary_evidence':[{'point':'政府公布新安排','quote':QUOTE,'article_id':'a','verified':True}]}
    article.update(changes)
    page.evaluate('(article) => RssArticleEvidence.init(article)', article)
    return article


@pytest.mark.parametrize('width', [390,1280])
def test_source_link_verifies_current_body_and_unique_cross_inline_paragraph(browser, width):
    context,page = load(browser,width,query='&quote='+quote(QUOTE)+'&quote_hash='+HASH+'&quote_kind=summary')
    try:
        before = page.locator('#content').inner_text()
        supported(page)
        assert page.locator('#content > p').first.get_attribute('id') == 'source-p1'
        assert page.locator('#content strong mark').inner_text() == '新的交通安排'
        assert page.locator('.summary-integrity-warning').count() == 0
        assert page.locator('.summary-source-link, #source-evidence').count() == 0
        assert page.locator('#content').inner_text() == before
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    finally: context.close()


@pytest.mark.parametrize('mode', ['two-paragraphs','twice-in-one'])
def test_repeated_literal_quote_is_explicitly_ambiguous(browser, mode):
    context,page = load(browser,query='&quote='+quote(QUOTE)+'&quote_hash='+HASH)
    try:
        content = '<p>'+QUOTE+'</p><p>'+QUOTE+'</p>' if mode=='two-paragraphs' else '<p>'+QUOTE+' '+QUOTE+'</p>'
        page.locator('#content').evaluate('(node,html) => {node.innerHTML=html}',content)
        page.evaluate("history.replaceState(null,'','?id=a&quote='+encodeURIComponent("+repr(QUOTE)+"))")
        supported(page,content=content,content_hash=hashlib.sha256(content.encode()).hexdigest())
        assert page.locator('.summary-source-link').count() == 0
        assert '未核實' in page.locator('.summary-source-status').first.inner_text()
        assert '未能核對' in page.locator('.summary-integrity-warning').inner_text()
        assert page.locator('mark.source-evidence-match').count() == 0
    finally: context.close()


def test_a_stale_hash_link_does_not_scroll_to_a_still_present_quote(browser):
    context,page=load(browser,query='&quote='+quote(QUOTE)+'&quote_hash='+'0'*64)
    try:
        supported(page)
        assert '版本未能核對' in page.locator('.summary-integrity-warning').inner_text()
        assert page.locator('mark.source-evidence-match').count()==0
    finally: context.close()


def test_a_forged_metadata_hash_and_paraphrase_do_not_create_source_links(browser):
    context,page=load(browser)
    try:
        supported(page,content_hash='0'*64)
        assert page.locator('.summary-source-link').count()==0
        supported(page,summary_evidence=[{'point':'政府公布新安排','quote':'政府已經落實全部交通方案，所有學校已停課。','article_id':'a','verified':True}])
        assert page.locator('.summary-source-link').count()==0
        assert page.locator('.summary-source-status').count()==2
    finally: context.close()


def test_visible_text_is_not_evidence_for_a_different_valid_hashed_source_body(browser):
    context,page=load(browser)
    try:
        # Even an unchanged visible DOM cannot stand in for another revision.
        source='<p>這篇有效的來源正文並沒有提及交通安排。</p>'
        supported(page,content=source,content_hash=hashlib.sha256(source.encode()).hexdigest())
        assert page.locator('.summary-source-link').count()==0
        assert page.locator('mark.source-evidence-match').count()==0
    finally: context.close()


def test_partial_source_keeps_a_verifiable_quote_but_discloses_scope(browser):
    context,page=load(browser)
    try:
        supported(page,content_quality={'fallback':'rss-blocked'},evidence_status='partial',scrape_error='HTTP 403')
        assert page.locator('.summary-source-link, #source-evidence').count()==0
        assert page.locator('.summary-source-status').count()==1
        supported(page,content_quality={'fallback':'minimal'})
        assert page.locator('.summary-source-link').count()==0
    finally: context.close()


def test_key_sentence_link_never_substitutes_for_summary_evidence(browser):
    context,page=load(browser)
    try:
        page.evaluate('(quote)=>{const list=document.createElement("ul");list.id="facts";const li=document.createElement("li");li.textContent=quote;list.append(li);document.body.append(list)}',QUOTE)
        supported(page,summary_evidence=[])
        assert page.locator('.summary-source-link').count()==0
        assert page.locator('.key-source-link').count()==0
        assert page.locator('.key-source-status').count()==0
        supported(page,summary_evidence=[],key_sentences_content_hash='0'*64)
        assert page.locator('.key-source-link').count()==0
        assert page.locator('.key-source-status').inner_text()=='未核實'
    finally: context.close()


def test_homepage_href_is_encoded_hash_bound_and_fixed_to_article_page(browser):
    context,page=load(browser)
    try:
        quote_text='這段來源文字包含 <img src=x onerror=alert(1)> 及 "引號"。'
        result=page.evaluate('''({hash,quote})=>({
          good:RssSourceEvidence.href({id:'a',content_hash:hash},quote,'summary'),
          badId:RssSourceEvidence.href({id:'javascript:alert(1)',content_hash:hash},quote),
          badHash:RssSourceEvidence.href({id:'a',content_hash:'old'},quote),
          tooLong:RssSourceEvidence.href({id:'a',content_hash:hash},'x'.repeat(2001)),
          badEntry:RssSourceEvidence.summarySupport({id:'a',content_hash:hash,summary_evidence:[{point:'重點',quote,article_id:'a',verified:true,content_hash:'0'.repeat(64)}]},'重點')
        })''',{'hash':HASH,'quote':quote_text})
        parsed=urlsplit(result['good']); params=parse_qs(parsed.query)
        assert parsed.path=='article.html' and not parsed.netloc and not parsed.scheme
        assert params['quote']==[quote_text] and params['quote_hash']==[HASH] and params['quote_kind']==['summary']
        assert '<img' not in result['good']
        assert all(result[key] is None for key in ('badId','badHash','tooLong','badEntry'))
        assert page.locator('img').count()==0
    finally: context.close()


@pytest.mark.parametrize('width', [390,1280])
def test_actual_homepage_summary_link_opens_verified_reader_paragraph(browser, news_data, width):
    from src.index_projection import project_article
    data=copy.deepcopy(news_data)
    row=data['articles'][0]
    row.update(summary='・政府公布新安排\n・學校繼續運作',content_hash=HASH,
               key_sentences=[QUOTE],key_sentences_content_hash=HASH,
               content_quality={'fallback':'none'},summary_evidence_status='available',
               summary_evidence=[{'point':'政府公布新安排','quote':QUOTE,'article_id':row['id'],
                                  'content_hash':HASH,'verified':True}])
    data.update(schema_version=2,display_window_hours=24,retention_window_hours=30,articles=[row])
    data['sources']['測試來源'].update(count=1,effective_count=1)
    index={**data,'articles':[project_article(row)]}
    body={'version':1,'content':BODY,'content_hash':HASH,'quality':row['content_quality']}
    with _static_server() as base:
        context=browser.new_context(viewport={'width':width,'height':900},service_workers='block')
        page=context.new_page()
        def respond(route):
            path=urlsplit(route.request.url).path
            if path.endswith('/articles_index.json'): route.fulfill(json=index)
            elif path.endswith('/articles.json'): route.fulfill(json=data)
            elif '/content/' in path: route.fulfill(json=body)
            else: route.fulfill(status=404,body='')
        page.route('**/data/**',respond)
        page.add_init_script('window.quoteLinkSpeech=0; if(window.speechSynthesis) window.speechSynthesis.speak=()=>{window.quoteLinkSpeech++}')
        try:
            page.goto(base+'/index.html')
            assert page.locator('.source-point-links, .article-details, .summary-source-link').count()==0
            assert page.locator('#leadStory .summary-unverified').count()==1
            assert page.locator('#leadStory .ai-note').first.is_visible()
            assert (page.locator('#mobileTabs [data-view="ai"]') if width==390 else page.locator('#summaryPlayTab')).is_visible()
            # Old saved hash-bound paragraph URLs remain checked internally,
            # without recreating any user-facing evidence control or panel.
            page.goto(base+'/article.html?id='+row['id']+'&quote='+quote(QUOTE)+'&quote_hash='+HASH+'&quote_kind=summary')
            page.locator('#source-p1 mark.source-evidence-match').first.wait_for()
            assert page.locator('#source-p1 strong mark.source-evidence-match').inner_text()=='新的交通安排'
            assert page.locator('.summary-integrity-warning').count() == 0
            assert page.locator('#summaryBox h2').inner_text()=='AI 摘要'
            assert page.locator('#summaryBox h2').is_visible()
            assert page.locator('#keyFacts h2').inner_text()=='原文關鍵句'
            assert page.locator('.summary-source-status').count()==1
            assert page.evaluate('window.quoteLinkSpeech')==0
            assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        finally: context.close()
