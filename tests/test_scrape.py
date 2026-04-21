import asyncio

from src import scrape


def _article(**overrides):
    base = {
        "id": "abc123",
        "title": "Test",
        "url": "https://example.com/a",
        "source": "Test source",
        "thumbnail": "https://example.com/thumb.jpg",
        "rss_content": "<p>RSS fallback text</p>",
    }
    base.update(overrides)
    return base


def test_rss_fallback_content_uses_rss_and_thumbnail():
    article = _article()

    content = scrape._rss_fallback_content(article, fallback="rss-empty")

    assert content is not None
    assert 'src="https://example.com/thumb.jpg"' in content
    assert "RSS fallback text" in content
    assert article["content"] == content
    assert article["content_quality"]["fallback"] == "rss-empty"
    assert article["content_quality"]["images"] == 1


def test_rss_fallback_content_splits_bullet_text_into_paragraphs():
    article = _article(rss_content="・第一點・第二點・第三點")

    content = scrape._rss_fallback_content(article, fallback="rss-empty")

    assert content.count("<p>") == 3
    assert "・第一點" in content


def test_rss_fallback_content_splits_long_sentence_text_into_paragraphs():
    article = _article(rss_content="第一句。第二句！第三句？")

    content = scrape._rss_fallback_content(article, fallback="rss-empty")

    assert content.count("<p>") == 3
    assert "<p>第一句。</p>" in content


def test_rss_fallback_content_returns_none_without_rss_or_thumbnail():
    article = _article(rss_content=None, thumbnail=None)

    assert scrape._rss_fallback_content(article, fallback="rss-empty") is None
    assert "content" not in article


def test_rss_fallback_content_can_emit_minimal_article():
    article = _article(
        title="Fallback title",
        url="https://example.com/original",
        rss_content=None,
        thumbnail=None,
    )

    content = scrape._rss_fallback_content(
        article,
        fallback="rss-empty",
        allow_minimal=True,
    )

    assert content is not None
    assert "Fallback title" in content
    assert "閱讀原文" in content
    assert article["content_quality"]["fallback"] == "minimal"


def test_scrape_one_falls_back_to_rss_when_extraction_is_empty(monkeypatch):
    async def fake_fetch_html(session, url):
        return "<html><body><main></main></body></html>"

    monkeypatch.setattr(scrape, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(scrape.trafilatura, "extract", lambda *args, **kwargs: None)
    monkeypatch.setattr(scrape.trafilatura, "extract_metadata", lambda *args, **kwargs: None)

    article = _article()
    out = asyncio.run(scrape._scrape_one(None, article, asyncio.Semaphore(1)))

    assert out["content"]
    assert "RSS fallback text" in out["content"]
    assert out["content_quality"]["fallback"] == "rss-empty"


def test_scrape_one_retries_mingpao_empty_response_with_urllib(monkeypatch):
    async def fake_fetch_html(session, url):
        return ""

    async def fake_urllib_fetch(url, extra_headers=None):
        return "<html><body><article><p>明報完整內文。</p></article></body></html>"

    async def fake_cloudscraper_fetch(url, extra_headers=None):
        raise AssertionError("cloudscraper should not run after urllib succeeds")

    monkeypatch.setattr(scrape, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(scrape, "_urllib_fetch", fake_urllib_fetch)
    monkeypatch.setattr(scrape, "_cloudscraper_fetch", fake_cloudscraper_fetch)
    monkeypatch.setattr(
        scrape.trafilatura,
        "extract",
        lambda *args, **kwargs: "<body><p>明報完整內文。</p></body>",
    )
    monkeypatch.setattr(scrape.trafilatura, "extract_metadata", lambda *args, **kwargs: None)

    article = _article(source="明報 本地", url="https://news.mingpao.com/ins/test")
    out = asyncio.run(scrape._scrape_one(None, article, asyncio.Semaphore(1)))

    assert "明報完整內文" in out["content"]
    assert out["content_quality"]["fallback"] == "none"


def test_scrape_one_emits_minimal_content_when_no_rss(monkeypatch):
    async def fake_fetch_html(session, url):
        return "<html><body><main></main></body></html>"

    monkeypatch.setattr(scrape, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(scrape.trafilatura, "extract", lambda *args, **kwargs: None)
    monkeypatch.setattr(scrape.trafilatura, "extract_metadata", lambda *args, **kwargs: None)

    article = _article(rss_content=None, thumbnail=None)
    out = asyncio.run(scrape._scrape_one(None, article, asyncio.Semaphore(1)))

    assert out["content"]
    assert out["content_quality"]["fallback"] == "minimal"


def test_scrape_one_keeps_english_content_untranslated(monkeypatch):
    async def fake_fetch_html(session, url):
        return "<html><body><article><p>Hello world from source.</p></article></body></html>"

    monkeypatch.setattr(scrape, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(
        scrape.trafilatura,
        "extract",
        lambda *args, **kwargs: "<body><p>Hello world from source.</p></body>",
    )
    monkeypatch.setattr(scrape.trafilatura, "extract_metadata", lambda *args, **kwargs: None)

    article = _article(source="9to5Mac")
    out = asyncio.run(scrape._scrape_one(None, article, asyncio.Semaphore(1)))

    assert "Hello world from source." in out["content"]
