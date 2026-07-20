import asyncio
import json

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
