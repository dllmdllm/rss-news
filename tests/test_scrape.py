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


def test_build_oncc_content_preserves_text_image_order():
    html = """
    <html><body>
      <div id="articleContent">
        <p>第一段文字，介紹新聞背景。</p>
        <div class="photo">
          <img data-src="/hk/bkn/cnt/news/20260422/photo1.jpg" alt="第一張圖">
          <div class="caption">第一張圖說明。</div>
        </div>
        <p>第二段文字，接續圖片之後。</p>
        <figure>
          <img src="https://hk.on.cc/hk/bkn/cnt/news/20260422/photo2.jpg" alt="第二張圖">
          <figcaption>第二張圖說明。</figcaption>
        </figure>
      </div>
    </body></html>
    """

    content = scrape._build_oncc_content(
        html,
        "https://hk.on.cc/hk/bkn/cnt/news/20260422/bkn-20260422093012345-0422_00822_001.html",
    )

    assert content is not None
    first = content.index("第一段文字")
    photo1 = content.index("photo1.jpg")
    second = content.index("第二段文字")
    photo2 = content.index("photo2.jpg")
    assert first < photo1 < second < photo2
    assert "第一張圖說明。" in content
    assert "第二張圖說明。" in content


def test_build_oncc_content_splits_blob_text_into_paragraphs():
    html = """
    <html><body>
      <main>
        <div class="photo"><img src="/photo.jpg" alt="主圖"></div>
        <div class="content">
          新聞標題 2026年04月22日 10:03 Tweet 東網電視 更多新聞短片
          主圖圖說。
          第一段內容，交代事件起因。
          第二段內容，交代最新進展。
          第三段內容，補充背景資料。
          上一則 下一則 on.cc東網
        </div>
      </main>
    </body></html>
    """

    content = scrape._build_oncc_content(
        html,
        "https://hk.on.cc/hk/bkn/cnt/news/20260422/bkn-20260422100352979-0422_00822_001.html",
    )

    assert content is not None
    assert content.count("<p>") >= 3
    assert "Tweet" not in content
    assert "上一則" not in content
    assert "photo.jpg" in content


def test_scrape_one_uses_oncc_parser_before_trafilatura(monkeypatch):
    async def fake_fetch_html(session, url):
        return """
        <html><body><div id="articleContent">
          <p>東網第一段完整內文。</p>
          <img src="https://hk.on.cc/a.jpg" alt="現場圖片">
          <p>東網第二段完整內文。</p>
        </div></body></html>
        """

    monkeypatch.setattr(scrape, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(
        scrape.trafilatura,
        "extract",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("trafilatura should not run")),
    )
    monkeypatch.setattr(scrape.trafilatura, "extract_metadata", lambda *args, **kwargs: None)

    article = _article(
        url="https://hk.on.cc/hk/bkn/cnt/news/20260422/bkn-20260422093012345-0422_00822_001.html",
        source="東網 本地",
    )
    out = asyncio.run(scrape._scrape_one(None, article, asyncio.Semaphore(1)))

    assert "東網第一段完整內文" in out["content"]
    assert 'src="https://hk.on.cc/a.jpg"' in out["content"]
    assert out["content_quality"]["fallback"] == "none"
