import asyncio
import json
import hashlib

import pytest

from src import translate_content as TC


def _article(**overrides):
    base = {
        "id": "eng1",
        "source": "9to5Mac",
        "title": "Test",
        "url": "https://example.com/a",
        "content": "<p>First paragraph.</p><img src=\"https://example.com/x.jpg\"><p>Second paragraph.</p>",
    }
    base.update(overrides)
    return base


def test_confirmed_numbered_cache_never_falls_through_to_paid_refresh(monkeypatch):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'test-key')
    content='<p>[0] 第一段。</p><p>[1] 第二段。</p><p>[2] 第三段。</p>'
    article=_article(id='f7bef5e0ede8',content=content)
    texts=[t.get_text(' ',strip=True)for t in TC._translation_units(content)[1]]
    cache={'f7bef5e0ede8':{'version':TC.TRANSLATE_VERSION,'content':content,'source_hash':'original-english','translated_hash':TC._source_hash(texts)}}
    monkeypatch.setattr(TC,'load_cache',lambda:cache)
    async def forbidden(*args,**kwargs):raise AssertionError('paid marker refresh forbidden')
    monkeypatch.setattr(TC,'_translate_article',forbidden)
    monkeypatch.setattr(TC,'save_cache',lambda *_:None)
    asyncio.run(TC.translate_english_content([article]))
    assert '[0]' not in article['content'] and article['_unit_marker_repair_from']==content


def test_extract_paragraphs_skips_image_bearing_tags():
    content = '<p>Text one.</p><p><img src="a.jpg">Caption text.</p><h2>Heading.</h2>'
    _, tags = TC.extract_paragraphs(content)
    texts = [t.get_text(" ", strip=True) for t in tags]
    assert texts == ["Text one.", "Heading."]


def test_translate_english_content_replaces_text_preserves_images(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "test-key")

    async def fake_post_messages(session, **kwargs):
        assert "First paragraph." in kwargs["user_text"]
        return json.dumps(["第一段。", "第二段。"]), {}, 200

    monkeypatch.setattr(TC, "post_messages", fake_post_messages)
    monkeypatch.setattr(TC, "load_cache", lambda: {})
    saved = {}
    monkeypatch.setattr(TC, "save_cache", lambda cache: saved.update(cache))

    articles = [_article()]
    asyncio.run(TC.translate_english_content(articles))

    content = articles[0]["content"]
    assert "第一段。" in content
    assert "第二段。" in content
    assert "First paragraph." not in content
    assert '<img src="https://example.com/x.jpg"' in content
    # order preserved: para1 -> img -> para2
    assert content.index("第一段") < content.index("x.jpg") < content.index("第二段")
    assert "eng1" in saved


def test_extract_paragraphs_excludes_but_does_not_truncate_long_paragraphs():
    # 2026-07-21 audit finding: 段落太長之前會截到 600 字先送去翻譯，
    # 但翻譯完之後成個 tag 內容被換晒做（短）譯文，600 字之後嘅原文永久消失。
    # 而家改做：太長就整條skip（唔翻譯，保持英文原文），唔會再截斷。
    long_text = "A" * (TC._MAX_PARAGRAPH_CHARS + 500)
    content = f"<p>Short one.</p><p>{long_text}</p>"
    _, tags = TC.extract_paragraphs(content)
    texts = [t.get_text(" ", strip=True) for t in tags]
    assert texts == ["Short one."]  # 太長嗰段冇入 tags，唔會被翻譯/替換


def test_translate_english_content_leaves_overlong_paragraph_untranslated(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "test-key")

    async def fake_post_messages(session, **kwargs):
        return json.dumps(["第一段。"]), {}, 200

    monkeypatch.setattr(TC, "post_messages", fake_post_messages)
    monkeypatch.setattr(TC, "load_cache", lambda: {})
    monkeypatch.setattr(TC, "save_cache", lambda cache: None)

    long_text = "B" * (TC._MAX_PARAGRAPH_CHARS + 100)
    articles = [_article(content=f"<p>First paragraph.</p><p>{long_text}</p>")]
    asyncio.run(TC.translate_english_content(articles))

    content = articles[0]["content"]
    assert "第一段。" in content
    assert long_text in content  # 完整原文保留，冇被截斷或者掉失


def test_translate_article_retries_on_raw_exception(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "test-key")
    monkeypatch.setattr(TC, "TRANSLATE_BACKOFF_BUDGET", 0.0)
    call_count = {"n": 0}

    async def flaky_post_messages(session, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise TimeoutError("simulated network timeout")
        return json.dumps(["第一段。", "第二段。"]), {}, 200

    monkeypatch.setattr(TC, "post_messages", flaky_post_messages)
    monkeypatch.setattr(TC, "load_cache", lambda: {})
    monkeypatch.setattr(TC, "save_cache", lambda cache: None)

    articles = [_article()]
    asyncio.run(TC.translate_english_content(articles))

    assert call_count["n"] == 2  # 第一次 raw exception，第二次先成功——冇因為exception就0 retry
    assert "第一段。" in articles[0]["content"]


def test_translate_english_content_skips_non_english_sources(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "test-key")

    async def fail_post_messages(*args, **kwargs):
        raise AssertionError("should not translate non-English sources")

    monkeypatch.setattr(TC, "post_messages", fail_post_messages)
    monkeypatch.setattr(TC, "load_cache", lambda: {})
    monkeypatch.setattr(TC, "save_cache", lambda cache: None)

    articles = [_article(source="明報 本地")]
    asyncio.run(TC.translate_english_content(articles))

    assert "First paragraph." in articles[0]["content"]


def test_translate_english_content_noop_without_api_key(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "")

    async def fail_post_messages(*args, **kwargs):
        raise AssertionError("should not call API without a key")

    monkeypatch.setattr(TC, "post_messages", fail_post_messages)

    articles = [_article()]
    asyncio.run(TC.translate_english_content(articles))

    assert "First paragraph." in articles[0]["content"]


def test_translate_english_content_uses_cache_on_second_run(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "test-key")
    call_count = {"n": 0}

    async def fake_post_messages(session, **kwargs):
        call_count["n"] += 1
        return json.dumps(["第一段。", "第二段。"]), {}, 200

    monkeypatch.setattr(TC, "post_messages", fake_post_messages)

    store: dict = {}
    monkeypatch.setattr(TC, "load_cache", lambda: dict(store))
    monkeypatch.setattr(TC, "save_cache", lambda cache: store.update(cache) or store.clear() or store.update(cache))

    asyncio.run(TC.translate_english_content([_article()]))
    assert call_count["n"] == 1

    # Second run with identical original content: should hit cache, no new API call.
    articles2 = [_article()]
    asyncio.run(TC.translate_english_content(articles2))
    assert call_count["n"] == 1
    assert "第一段。" in articles2[0]["content"]


def test_translate_english_content_retranslates_when_source_text_changes(monkeypatch):
    monkeypatch.setattr(TC, "MINIMAX_API_KEY", "test-key")
    call_count = {"n": 0}

    async def fake_post_messages(session, **kwargs):
        call_count["n"] += 1
        n = kwargs["user_text"].count("\n") + 1
        return json.dumps([f"譯文{i}" for i in range(n)]), {}, 200

    monkeypatch.setattr(TC, "post_messages", fake_post_messages)
    store: dict = {}
    monkeypatch.setattr(TC, "load_cache", lambda: dict(store))
    monkeypatch.setattr(TC, "save_cache", lambda cache: (store.clear(), store.update(cache)))

    asyncio.run(TC.translate_english_content([_article()]))
    assert call_count["n"] == 1

    changed = [_article(content="<p>Completely different text now.</p>")]
    asyncio.run(TC.translate_english_content(changed))
    assert call_count["n"] == 2


def test_translation_cache_keeps_new_rss_images_without_api_call(monkeypatch):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'fake-key')
    texts = ['First paragraph.', 'Second paragraph.']
    cache = {'eng1':{'version':TC.TRANSLATE_VERSION,'source_hash':TC._source_hash(texts),'content':'<p>第一段。</p><p>第二段。</p>'}}
    monkeypatch.setattr(TC, 'load_cache', lambda: cache)
    monkeypatch.setattr(TC, 'save_cache', lambda cache: None)
    async def forbidden(*args, **kwargs): raise AssertionError('no new paid request for cached text')
    monkeypatch.setattr(TC, 'post_messages', forbidden)
    article = _article()
    asyncio.run(TC.translate_english_content([article]))
    assert '第一段。' in article['content'] and '第二段。' in article['content']
    assert 'https://example.com/x.jpg' in article['content']


def test_nested_legacy_translation_cache_preserves_media_without_request(monkeypatch):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'fake-key')
    content = '<blockquote><p>Nested paragraph.</p></blockquote><img src="https://example.com/new.jpg"><p>Second.</p>'
    _, tags = TC.extract_paragraphs(content)
    cache = {'eng1': {'version': TC.TRANSLATE_VERSION,
        'source_hash': TC._source_hash([t.get_text(' ', strip=True) for t in tags]),
        'content': '<blockquote>引文。</blockquote><p>第二段。</p>'}}
    monkeypatch.setattr(TC, 'load_cache', lambda: cache)
    async def forbidden(*args, **kwargs):
        raise AssertionError('matching legacy cache must not make a paid request')
    monkeypatch.setattr(TC, 'post_messages', forbidden)
    article = _article(content=content)
    asyncio.run(TC.translate_english_content([article]))
    assert '引文。' in article['content'] and '第二段。' in article['content']
    assert 'https://example.com/new.jpg' in article['content']
    assert '<blockquote><p>引文。</p></blockquote>' in article['content']


def test_retained_translation_is_not_translated_again(monkeypatch):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'test-key')
    saved = {}
    monkeypatch.setattr(TC, 'load_cache', lambda: saved)
    monkeypatch.setattr(TC, 'save_cache', lambda cache: saved.update(cache))
    calls = []
    async def fake_post(session, **kwargs):
        calls.append(kwargs['user_text'])
        return json.dumps(['第一段。', '第二段。']), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    article = _article()
    asyncio.run(TC.translate_english_content([article]))
    original_hash = saved['eng1']['source_hash']
    article['content_retention'] = {'scraped_at': '2026-10-01T00:00:00Z'}
    asyncio.run(TC.translate_english_content([article]))
    assert len(calls) == 1
    assert saved['eng1']['source_hash'] == original_hash
    assert saved['eng1']['translated_hash'] != original_hash
    # A real English source correction must invalidate the cached translation.
    article['content'] = '<p>Corrected first paragraph.</p><p>Second paragraph.</p>'
    asyncio.run(TC.translate_english_content([article]))
    assert len(calls) == 2


def test_translation_preserves_nested_blocks_inline_markup_and_cache(monkeypatch):
    from bs4 import BeautifulSoup
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'fake-key')
    store = {}
    monkeypatch.setattr(TC, 'load_cache', lambda: dict(store))
    monkeypatch.setattr(TC, 'save_cache', lambda cache: store.update(cache))
    calls = []
    async def fake_post(session, **kwargs):
        calls.append(kwargs['user_text'])
        return json.dumps(['第一段。', '第二段。', '清單前 ', '重要', ' 後文。']), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    content = ('<blockquote><p>First.</p><p>Second.</p></blockquote>'
               '<ul><li><p>Before <strong>important</strong> after.</p></li></ul>'
               '<img src="https://example.com/old.jpg">')
    for image in ('old', 'new'):
        article = _article(content=content.replace('old.jpg', f'{image}.jpg'))
        asyncio.run(TC.translate_english_content([article]))
        soup = BeautifulSoup(article['content'], 'html.parser')
        assert len(soup.select('blockquote > p')) == 2
        assert soup.select_one('li > p > strong').get_text() == '重要'
        assert soup.select_one('li > p').get_text() == '清單前 重要 後文。'
        assert soup.img['src'].endswith(f'{image}.jpg')
    assert len(calls) == 1
    items = [json.loads(line.split('] ', 1)[1]) for line in calls[0].splitlines()]
    assert [item['text'] for item in items].count('First.') == 1
    inline = items[2:]
    assert [item['text'] for item in inline] == ['Before', 'important', 'after.']
    assert all(item['paragraph_context'] == 'Before important after.' for item in inline)
    assert '只翻譯同一位置嘅 text' in TC.SYSTEM_PROMPT
    assert '唔好將 paragraph_context 翻譯或重複輸出' in TC.SYSTEM_PROMPT


def test_translation_units_include_list_parent_text_once():
    soup, units = TC._translation_units('<ul><li>Parent prose.<ul><li>Child prose.</li></ul>Tail prose.</li></ul>')
    assert [node.get_text(strip=True) for node in units] == ['Parent prose.', 'Child prose.', 'Tail prose.']
    for unit, text in zip(units, ['父項。', '子項。', '結尾。']):
        TC._replace_tag_text(unit, text)
    assert str(soup) == '<ul><li>父項。<ul><li>子項。</li></ul>結尾。</li></ul>'


@pytest.mark.parametrize('completed_count', [1, 4])
def test_cancelled_stage_flushes_completed_tail_and_next_cycle_reuses_it(monkeypatch, tmp_path, completed_count):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    calls = []

    def original(index):
        return _article(id=f'article-{index}', content=f'<p>Story {index}.</p><img src="https://example.com/photo.jpg">')

    async def run():
        blocked = asyncio.Event()
        drained = asyncio.Event()

        async def fake_post(session, **kwargs):
            calls.append(kwargs['user_text'])
            assert kwargs['max_tokens'] <= TC.MAX_TRANSLATION_OUTPUT_TOKENS
            if f'Story {completed_count}.' in kwargs['user_text'] and not drained.is_set():
                blocked.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    drained.set()
            return json.dumps(['已翻譯的段落。']), {}, 200

        monkeypatch.setattr(TC, 'post_messages', fake_post)
        articles = [original(i) for i in range(completed_count+1)]
        pending_original = articles[-1]['content']
        pending_hash = hashlib.sha256(pending_original.encode()).hexdigest()
        task = asyncio.create_task(TC.translate_english_content(articles))
        await asyncio.wait_for(blocked.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set(), 'cancelled provider child must finish before cache flush'
        cache = json.loads(TC.CACHE_PATH.read_text(encoding='utf-8'))
        assert set(cache) == {f'article-{i}' for i in range(completed_count)}
        assert all(articles[i]['translation_status']=='translated' for i in range(completed_count))
        assert articles[-1]['content'] == pending_original
        assert hashlib.sha256(articles[-1]['content'].encode()).hexdigest() == pending_hash
        assert articles[-1]['translation_status']=='deferred'
        assert articles[-1]['translation_reason']=='stage-cancelled'

        second = [original(i) for i in range(completed_count+1)]
        result = await TC.translate_english_content(second)
        assert result['ok'] and result['cached']==completed_count and result['translated']==1
        for i in range(completed_count):
            assert sum(f'Story {i}.' in call for call in calls)==1
        assert sum(f'Story {completed_count}.' in call for call in calls)==2
        assert all('https://example.com/photo.jpg' in a['content'] for a in second)

    asyncio.run(run())


def test_cancelled_stage_never_checkpoints_unfinished_article(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')

    async def run():
        started = asyncio.Event()
        drained = asyncio.Event()
        async def fake_post(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()
        monkeypatch.setattr(TC, 'post_messages', fake_post)
        article = _article()
        original = article['content']
        task = asyncio.create_task(TC.translate_english_content([article]))
        await asyncio.wait_for(started.wait(),timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert drained.is_set()
        assert json.loads(TC.CACHE_PATH.read_text(encoding='utf-8'))=={}
        assert article['content']==original
        assert article['translation_status']=='deferred'

    asyncio.run(run())


def test_individual_failure_reports_failed_without_caching_or_replacing_source(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    async def fake_post(*args, **kwargs):
        return '', {'type':'invalid_request_error'}, 400
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    article = _article()
    original = article['content']
    result = asyncio.run(TC.translate_english_content([article]))
    assert result['status']=='failed' and result['ok'] is False
    assert result['failed']==1 and result['translated']==0
    assert article['content']==original
    assert article['translation_status']=='failed'
    assert article['translation_reason']=='api-error-400'
    assert json.loads(TC.CACHE_PATH.read_text(encoding='utf-8'))=={}


def test_invalid_translation_output_is_failed_instead_of_successful_step(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    monkeypatch.setattr(TC, 'TRANSLATE_MAX_ATTEMPTS', 1)
    async def fake_post(*args, **kwargs):
        return json.dumps(['too few translated units']), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    article = _article()
    original = article['content']
    result = asyncio.run(TC.translate_english_content([article]))
    assert result['failed']==1 and result['ok'] is False
    assert article['content']==original and article['translation_reason']=='invalid-output'


def test_long_article_is_deferred_whole_without_provider_request(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    async def forbidden(*args, **kwargs):
        raise AssertionError('oversized article must not create a paid request')
    monkeypatch.setattr(TC, 'post_messages', forbidden)
    content = ''.join('<p>'+('English text. '*80)+'</p>' for _ in range(10))+'<img src="https://example.com/full.jpg">'
    article = _article(content=content)
    result = asyncio.run(TC.translate_english_content([article]))
    assert result['deferred']==1 and result['ok'] is False
    assert article['translation_status']=='deferred' and article['translation_reason']=='input-budget'
    assert article['content']==content
    assert not TC.CACHE_PATH.exists()


def test_valid_long_translation_cache_precedes_input_and_collection_budget(monkeypatch):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    content = ''.join('<p>'+('English text. '*80)+'</p>' for _ in range(10))+'<img src="https://example.com/new.jpg">'
    texts = [t.get_text(' ',strip=True) for t in TC._translation_units(content)[1]]
    cached = {'eng1':{'version':TC.TRANSLATE_VERSION,'source_hash':TC._source_hash(texts),'content':'<p>已翻譯。</p>'*10}}
    monkeypatch.setattr(TC,'load_cache',lambda:cached)
    async def forbidden(*args,**kwargs): raise AssertionError('valid cache must be reused without paid refresh')
    monkeypatch.setattr(TC,'post_messages',forbidden)
    article = _article(content=content,_paid_enrichment_deferred=True)
    result = asyncio.run(TC.translate_english_content([article]))
    assert result['ok'] and result['cached']==1 and result['deferred']==0
    assert '已翻譯。' in article['content'] and 'https://example.com/new.jpg' in article['content']


def test_collection_budget_preserves_uncached_source_and_reports_deferral(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    async def forbidden(*args, **kwargs): raise AssertionError('collection cannot expand paid translation quota')
    monkeypatch.setattr(TC, 'post_messages', forbidden)
    article = _article(_paid_enrichment_deferred=True)
    original = article['content']
    result = asyncio.run(TC.translate_english_content([article]))
    assert result['deferred']==1 and article['translation_reason']=='collection-budget'
    assert article['content']==original


def test_guarded_untranslated_source_blocks_keep_partial_status(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    async def fake_post(*args, **kwargs): return json.dumps(['已翻譯。']), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    long_text = 'x'*(TC._MAX_PARAGRAPH_CHARS+1)
    article = _article(content='<p>Short.</p><p>'+long_text+'</p>')
    result = asyncio.run(TC.translate_english_content([article]))
    assert result['translated']==1 and result['partial']==1 and result['status']=='partial'
    assert long_text in article['content']
    assert article['translation_status']=='partial' and article['translation_reason']=='unsupported-text-blocks'


def test_source_paid_limit_counts_only_uncached_admitted_jobs_and_backlog_advances(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    monkeypatch.setattr(TC, 'legacy_source_limit', lambda source: 2)
    cached_original = _article(id='cached',content='<p>Previously translated source.</p>')
    cache = {'cached':{'version':TC.TRANSLATE_VERSION,
        'source_hash':TC._source_hash(['Previously translated source.']),
        'content':'<p>已有譯文。</p>'}}
    TC.save_cache(cache)
    calls = []
    async def fake_post(session, **kwargs):
        calls.append(kwargs['user_text'])
        return json.dumps(['新譯文。']), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    oversized = ''.join('<p>'+('Long prose. '*100)+'</p>' for _ in range(10))

    def batch():
        return [dict(cached_original), _article(id='long',content=oversized),
                *[_article(id=f'new-{i}',content=f'<p>New source {i}.</p>') for i in range(3)]]

    first = batch()
    report = asyncio.run(TC.translate_english_content(first))
    assert report['cached']==1 and report['translated']==2 and report['deferred']==2
    assert len(calls)==2
    assert first[1]['translation_reason']=='input-budget'
    assert first[-1]['translation_reason']=='collection-budget'
    assert first[-1]['content']=='<p>New source 2.</p>'

    second = batch()
    report = asyncio.run(TC.translate_english_content(second))
    assert report['cached']==3 and report['translated']==1 and report['deferred']==1
    assert len(calls)==3
    assert 'New source 2.' in calls[-1]


@pytest.mark.parametrize('values', [[None, '第二段。'], ['', '第二段。'], ['第一段。', {'text':'第二段。'}]])
def test_missing_or_nontext_translation_units_never_create_completed_checkpoint(monkeypatch, tmp_path, values):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    monkeypatch.setattr(TC, 'TRANSLATE_MAX_ATTEMPTS', 1)
    async def fake_post(*args, **kwargs): return json.dumps(values), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    article = _article()
    original = article['content']
    report = asyncio.run(TC.translate_english_content([article]))
    assert not report['ok'] and report['failed']==1
    assert article['content']==original
    assert json.loads(TC.CACHE_PATH.read_text(encoding='utf-8'))=={}


def test_partial_provider_failure_does_not_hide_behind_successful_sibling(monkeypatch, tmp_path):
    monkeypatch.setattr(TC, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(TC, 'CACHE_PATH', tmp_path/'translations.json')
    async def fake_post(*args, **kwargs):
        if 'Failure article.' in kwargs['user_text']:
            return '', {'type':'invalid_request_error'}, 400
        return json.dumps(['完成的文章。']), {}, 200
    monkeypatch.setattr(TC, 'post_messages', fake_post)
    good = _article(id='good',content='<p>Success article.</p>')
    bad = _article(id='bad',content='<p>Failure article.</p>')
    report = asyncio.run(TC.translate_english_content([good,bad]))
    assert report['status']=='partial' and not report['ok']
    assert report['translated']==1 and report['failed']==1
    assert good['translation_status']=='translated' and bad['translation_status']=='failed'
    assert bad['content']=='<p>Failure article.</p>'
    assert set(json.loads(TC.CACHE_PATH.read_text(encoding='utf-8')))=={'good'}
