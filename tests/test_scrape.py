import asyncio
import json

from src import scrape


def _nuxt_html(raw_array: list) -> str:
    """Wrap a devalue-style array as the __NUXT_DATA__ script TVB pages ship."""
    payload = json.dumps(raw_array, ensure_ascii=False)
    return (
        "<html><body>"
        f'<script type="application/json" data-nuxt-data="nuxt-app" data-ssr="true" '
        f'id="__NUXT_DATA__">{payload}</script>'
        "</body></html>"
    )


# Minimal devalue array matching TVB's real shape: index 1 is the SSR state
# ({"data": <idx>}), which points to a dict keyed "article-detail-{id}-tc"
# whose value is the article dict (field name -> index of its resolved value).
def _tvb_article_html(article_fields: dict) -> str:
    pool = [
        ["ShallowReactive", 1],
        None,  # placeholder for index 1, filled below
        ["ShallowReactive", 3],
        None,  # placeholder for index 3
        None,  # placeholder for index 4 (article dict)
    ]
    field_map = {}
    for key, value in article_fields.items():
        pool.append(value)
        field_map[key] = len(pool) - 1
    pool[1] = {"data": 2}
    pool[3] = {"article-detail-123-tc": 4}
    pool[4] = field_map
    return _nuxt_html(pool)


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


def test_remove_relative_images_keeps_protocol_relative_urls():
    # 2026-07-21 audit finding：`//cdn.example.com/x.jpg`（protocol-relative，
    # 常見 CDN pattern）之前會被誤判做 relative path 而刪走，但呢種 URL
    # 喺瀏覽器度完全 resolve 得到，唔理個 page 自己個 origin 係咩。
    content = (
        '<p>a</p><img src="//cdn.example.com/photo.jpg">'
        '<img src="/relative/path.jpg"><img src="https://ok.com/x.jpg">'
    )
    out = scrape._remove_relative_images(content)
    assert "cdn.example.com/photo.jpg" in out
    assert "https://ok.com/x.jpg" in out
    assert "/relative/path.jpg" not in out


def test_expand_stheadline_galleries_escapes_attributes():
    # 2026-07-21 audit finding：src/alt 之前冇 escape 就直接 interpolate
    # 落 <img> 屬性，caption 入面一個雙引號會拆散個 tag。
    html = (
        "<html><body>"
        'const article_galleries = {"gallery-1": '
        '[{"src": "https://x.com/a.jpg\\"onerror=alert(1)", "caption": "有 \\"quote\\" 嘅 caption"}]};\n'
        "<gallery-1></gallery-1>"
        "</body></html>"
    )
    out = scrape._expand_stheadline_galleries(html)
    # src 屬性入面嗰個雙引號一定要被 escape 做 &quot;，唔可以係 bare "
    # ——bare " 會提早結束 src="..." 呢個屬性，令 onerror=alert(1) 走
    # 出咗屬性、變成一個真.嘅 HTML attribute（XSS/markup injection）。
    assert 'src="https://x.com/a.jpg&quot;onerror=alert(1)"' in out
    assert 'alt="有 &quot;quote&quot; 嘅 caption"' in out


def test_cloudscraper_fetch_returns_none_on_http_error(monkeypatch):
    # 2026-07-21 audit finding：cloudscraper（requests-style）唔似
    # _fetch_html/_urllib_fetch 咁會喺 4xx/5xx raise——之前唔 check status
    # 就直接 return r.text，錯誤頁如果冇撞中 _BLOCK_PHRASES 就會被當正文。
    import sys
    import types

    class _FakeResponse:
        status_code = 403
        text = "<html>Cloudflare challenge page</html>"

    class _FakeScraper:
        def get(self, url, timeout=None, headers=None):
            return _FakeResponse()

    fake_module = types.ModuleType("cloudscraper")
    fake_module.create_scraper = lambda **kw: _FakeScraper()
    monkeypatch.setitem(sys.modules, "cloudscraper", fake_module)

    result = asyncio.run(scrape._cloudscraper_fetch("https://example.com/a"))
    assert result is None


def test_cloudscraper_fetch_returns_text_on_success(monkeypatch):
    import sys
    import types

    class _FakeResponse:
        status_code = 200
        text = "<html><body><p>正文</p></body></html>"

    class _FakeScraper:
        def get(self, url, timeout=None, headers=None):
            return _FakeResponse()

    fake_module = types.ModuleType("cloudscraper")
    fake_module.create_scraper = lambda **kw: _FakeScraper()
    monkeypatch.setitem(sys.modules, "cloudscraper", fake_module)

    result = asyncio.run(scrape._cloudscraper_fetch("https://example.com/a"))
    assert result == "<html><body><p>正文</p></body></html>"


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


def test_scrape_one_embeds_og_image_fallback_into_content(monkeypatch):
    # Regression: article["thumbnail"] must be updated from the trafilatura
    # og:image fallback BEFORE _add_featured_image runs, otherwise sources
    # whose RSS carries no thumbnail (RTHK, 9to5Mac, GoTrip, HKEPC, Unwire,
    # WeekendHK …) get a card thumbnail but no image at all in the article
    # body — _add_featured_image would have read the still-empty field.
    class FakeMeta:
        image = "https://example.com/og-fallback.jpg"

    async def fake_fetch_html(session, url):
        return "<html><body><article><p>No inline image here.</p></article></body></html>"

    monkeypatch.setattr(scrape, "_fetch_html", fake_fetch_html)
    monkeypatch.setattr(
        scrape.trafilatura,
        "extract",
        lambda *args, **kwargs: "<body><p>No inline image here.</p></body>",
    )
    monkeypatch.setattr(scrape.trafilatura, "extract_metadata", lambda *args, **kwargs: FakeMeta())

    article = _article(thumbnail=None)
    out = asyncio.run(scrape._scrape_one(None, article, asyncio.Semaphore(1)))

    assert out["thumbnail"] == "https://example.com/og-fallback.jpg"
    assert 'src="https://example.com/og-fallback.jpg"' in out["content"]


def test_restore_intro_from_description_uses_og_description_prefix_for_weekendhk():
    html = """
    <html>
      <head>
        <meta property="og:description" content="\u9996\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002\u7b2c\u4e00\u500b\u6a19\u984c">
      </head>
      <body>
        <h1>\u4e3b\u6a19\u984c</h1>
        <h2>\u7b2c\u4e00\u500b\u6a19\u984c</h2>
        <p>\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002</p>
      </body>
    </html>
    """
    content = "<html><body><h1>\u4e3b\u6a19\u984c</h1><h2>\u7b2c\u4e00\u500b\u6a19\u984c</h2><p>\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002</p></body></html>"

    out = scrape._restore_intro_from_description(html, content, "\u4e3b\u6a19\u984c", "WeekendHK")

    assert "\u9996\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002" in out
    assert out.index("\u9996\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002") < out.index("\u7b2c\u4e00\u500b\u6a19\u984c")
    assert out.count("\u9996\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002") == 1


def test_restore_intro_from_description_uses_og_description_prefix_for_gotrip():
    html = """
    <html>
      <head>
        <meta property="og:description" content="\u7b2c\u4e00\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002\u7b2c\u4e00\u500b\u6a19\u984c">
      </head>
      <body>
        <h1>\u4e3b\u6a19\u984c</h1>
        <h2>\u7b2c\u4e00\u500b\u6a19\u984c</h2>
        <p>\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002</p>
      </body>
    </html>
    """
    content = "<html><body><h1>\u4e3b\u6a19\u984c</h1><h2>\u7b2c\u4e00\u500b\u6a19\u984c</h2><p>\u6b63\u6587\u7b2c\u4e00\u6bb5\u3002</p></body></html>"

    out = scrape._restore_intro_from_description(html, content, "\u4e3b\u6a19\u984c", "GoTrip")

    assert "\u7b2c\u4e00\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002" in out
    assert out.index("\u7b2c\u4e00\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002") < out.index("\u7b2c\u4e00\u500b\u6a19\u984c")
    assert out.count("\u7b2c\u4e00\u6bb5\u524d\u8a00\u3002\u7b2c\u4e8c\u6bb5\u524d\u8a00\u3002") == 1


def test_build_am730_content_extracts_body_and_skips_ads_and_related():
    # Real am730 markup interleaves ad slots (.adbox), a related-listings
    # gallery (.picset), and a related-news aside (.newsflash) inside
    # .article__body alongside the genuine paragraphs/lead photo — generic
    # trafilatura extraction came out thinner than the real text because of
    # this noise. The custom parser targets .article__body directly and
    # skips known junk siblings by class name.
    html = """
    <html><body>
      <article class="article">
        <header class="article__head">頭條 出版時間</header>
        <div class="sharebar">分享：</div>
        <div class="article__body">
          <link>
          <figure class="picsolo picosolo_first_img">
            <img src="https://cdn3.am730.com.hk/photo1.jpg">
            圖片說明文字。
          </figure>
          <div class="adbox"><img src="https://ads.example.com/a.jpg"></div>
          <div>真正嘅新聞內文段落，講述事件經過同細節，並補充多一句背景資料令內容長度足夠通過最低字數門檻。</div>
          <div class="adbox"><img src="https://ads.example.com/b.jpg"></div>
          <figure class="picset picset--w5">
            <img src="https://cdn3.am730.com.hk/related1.jpg">
            <img src="https://cdn3.am730.com.hk/related2.jpg">
          </figure>
          <aside class="newsflash">相關新聞：其他報導標題</aside>
        </div>
        <footer class="article__foot">相關 標籤</footer>
      </article>
    </body></html>
    """

    content = scrape._build_am730_content(html)

    assert content is not None
    assert "photo1.jpg" in content
    assert "真正嘅新聞內文段落" in content
    assert "ads.example.com" not in content
    assert "related1.jpg" not in content
    assert "相關新聞" not in content
    assert "分享" not in content


def test_build_am730_content_returns_none_without_article_body():
    assert scrape._build_am730_content("<html><body><p>no article body here</p></body></html>") is None


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


def test_build_oncc_content_finds_deeply_nested_image_in_root_container():
    # Regression for a real bkn page: the selected root container's id
    # ("centerCTN") matches the text-hint regex via the substring "content"
    # its own class, but none of its DIRECT children are p/img/figure — the
    # real image sits several <div> layers down (div > div > div > img, as
    # seen on-site: photo > photoCTN > photo > img). A recursive=False check
    # for nested blocks wrongly treated this as a flat leaf and flattened it
    # via get_text(), skipping the walk that would have found the image.
    html = """
    <html><body>
      <div id="centerCTN" class="news_content">
        <p>第一段文字。</p>
        <div class="divSect upper">
          <div class="leftSide">
            <div>
              <div class="photo hPhoto">
                <div class="photoCTN">
                  <div class="photo">
                    <img src="/hk/bkn/cnt/news/20260713/photo/deep.jpg">
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <p>第二段文字。</p>
      </div>
    </body></html>
    """

    content = scrape._build_oncc_content(
        html,
        "https://hk.on.cc/hk/bkn/cnt/news/20260713/bkn-20260713204405560-0713_00822_001.html",
    )

    assert content is not None
    assert "deep.jpg" in content
    assert "第一段文字" in content
    assert "第二段文字" in content


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


def test_build_hk01_content_returns_none_on_unexpected_shape():
    # 2026-07-21 audit finding：深層 JSON traverse 之前冇 guard，`props`
    # 明確係 JSON null（唔係 missing key）會令 `.get("props", {})` 都
    # 冧唔到（dict.get 嘅 default 淨係喺 key 唔存在先生效，key 存在但值
    # 係 None 就照樣 return None，跟住 .get() 就 AttributeError）。
    # 而家應該 catch 咗、graceful return None，唔會拋 exception 出去。
    import json as _json
    payload = {"props": None}
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{_json.dumps(payload)}</script></body></html>'
    assert scrape._build_hk01_content(html) is None


def test_build_hk01_content_returns_none_when_blocks_has_non_dict_entry():
    import json as _json
    payload = {
        "props": {"initialProps": {"pageProps": {"article": {
            "description": "",
            "blocks": ["not-a-dict-block"],
        }}}}
    }
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{_json.dumps(payload)}</script></body></html>'
    assert scrape._build_hk01_content(html) is None


def test_build_hk01_content_skips_description_when_it_prefixes_first_block():
    """HK01 sometimes ships a truncated `description` whose text is a strict
    prefix of the first text block (e.g. cut mid-sentence). In that case the
    description must be dropped, otherwise readers see the same lead twice
    (once truncated, once full)."""
    payload = {
        "props": {
            "initialProps": {
                "pageProps": {
                    "article": {
                        "description": "盧惠光曾在泰拳界聲名大振，更在1989年當上龍虎武師，與",
                        "blocks": [
                            {
                                "blockType": "text",
                                "htmlTokens": [[
                                    {"type": "text", "content": "盧惠光曾在泰拳界聲名大振，更在1989年當上龍虎武師，與成龍合作無間。"},
                                ]],
                            },
                        ],
                    }
                }
            }
        }
    }
    import json as _json
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{_json.dumps(payload, ensure_ascii=False)}</script></body></html>'
    out = scrape._build_hk01_content(html)
    assert out is not None
    assert out.count("盧惠光曾在泰拳界聲名大振") == 1
    assert "與成龍合作無間" in out


def test_build_hk01_content_keeps_description_when_distinct_from_blocks():
    payload = {
        "props": {
            "initialProps": {
                "pageProps": {
                    "article": {
                        "description": "獨家專訪，深入剖析事件全貌。",
                        "blocks": [
                            {
                                "blockType": "text",
                                "htmlTokens": [[
                                    {"type": "text", "content": "事件起因要追溯至上週的會議。"},
                                ]],
                            },
                        ],
                    }
                }
            }
        }
    }
    import json as _json
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{_json.dumps(payload, ensure_ascii=False)}</script></body></html>'
    out = scrape._build_hk01_content(html)
    assert "獨家專訪" in out
    assert "事件起因要追溯" in out


def test_build_tvb_content_extracts_nuxt_payload_with_cover_image():
    # TVB migrated to Nuxt in 2026-07; article now lives in a devalue-encoded
    # __NUXT_DATA__ array under state.data["article-detail-{id}-tc"].
    html = _tvb_article_html({
        "content": "<p>正文第一段。</p><p>正文第二段。</p>",
        "cover": [{"url": "https://example.com/cover.jpg", "title": "封面圖"}],
    })
    content = scrape._build_tvb_content(html)
    assert content is not None
    assert 'src="https://example.com/cover.jpg"' in content
    assert "正文第一段" in content
    assert content.index("cover.jpg") < content.index("正文第一段")


def test_build_tvb_content_skips_duplicate_cover_image():
    # If the cover photo is already inline in the body (as TVB's real
    # articles do for their other embedded photos), don't prepend it again.
    html = _tvb_article_html({
        "content": '<p>開首。</p><img src="https://example.com/cover.jpg"><p>結尾。</p>',
        "cover": [{"url": "https://example.com/cover.jpg", "title": "封面圖"}],
    })
    content = scrape._build_tvb_content(html)
    assert content is not None
    assert content.count("cover.jpg") == 1


def test_build_tvb_content_falls_back_to_content_hk_when_content_empty():
    html = _tvb_article_html({"content": "", "content_hk": "<p>備用內文。</p>", "cover": []})
    content = scrape._build_tvb_content(html)
    assert content is not None
    assert "備用內文" in content


def test_build_tvb_content_returns_none_without_nuxt_script():
    assert scrape._build_tvb_content("<html><body><p>no nuxt data here</p></body></html>") is None


def test_fix_lazy_images_promotes_data_placeholder_src():
    # GoTrip/WeekendHK lazy pattern: src is a base64 1px gif, real URL in
    # data-src. The original regex only handled imgs with NO src at all.
    html = '<img src="data:image/gif;base64,R0lGOD" data-src="https://imgs.gotrip.hk/real.jpg" alt="x">'
    out = scrape._fix_lazy_images(html)
    assert 'src="https://imgs.gotrip.hk/real.jpg"' in out
    assert "data:image/gif" not in out.split("src=")[1][:30]


def test_fix_picture_elements_skips_data_uri_placeholder():
    html = (
        "<picture>"
        '<source data-srcset="https://imgs.gotrip.hk/from-source.jpg 1x">'
        '<img src="data:image/gif;base64,R0lGOD" data-src="https://imgs.gotrip.hk/real.jpg">'
        "</picture>"
    )
    out = scrape._fix_picture_elements(html)
    assert "data:image/gif" not in out
    assert 'src="https://imgs.gotrip.hk/real.jpg"' in out


def test_build_lookmedia_content_extracts_paged_listicle_with_lazy_images():
    # Multi-page listicle: text pages + image pages with a real-URL theme
    # placeholder in src and the actual photo in data-src.
    html = """
    <html><body>
      <div class="entry post-content js-post-gallery entry-content">
        <div class="_page_ _page_1 read-full">第一頁導言文字，交代事件背景同人物。</div>
        <div class="read_btn"><a>閱讀全文</a></div>
        <div class="_more_content_">
          <div class="_page_ _page_2"><p>第二頁內文，繼續講述事件發展經過。</p></div>
          <div class="_page_ _page_3"><p>
            <figure class="wp-caption">
              <a><img src="https://www.weekendhk.com/wp-content/themes/bucket/placeholder.png"
                      data-src="https://imgs.weekendhk.com/wp-content/uploads/2026/07/photo1"
                      alt="圖片說明一"></a>
            </figure>
          </p></div>
          <div class="_page_ _page_4"><p>
            <figure class="wp-caption">
              <a><img src="https://www.weekendhk.com/wp-content/themes/bucket/placeholder.png"
                      data-src="https://imgs.weekendhk.com/wp-content/uploads/2026/07/photo2"
                      alt="圖片說明二"></a>
            </figure>
          </p></div>
          <div id="first_lrec_12345"></div>
        </div>
      </div>
    </body></html>
    """
    content = scrape._build_lookmedia_content(html)
    assert content is not None
    assert "第一頁導言文字" in content
    assert "第二頁內文" in content
    assert content.count("photo1") == 1 and content.count("photo2") == 1
    assert "placeholder.png" not in content
    assert "閱讀全文" not in content
    # text before images, image order preserved
    assert content.index("第二頁內文") < content.index("photo1") < content.index("photo2")
    assert "圖片說明一" in content  # caption carried via figcaption


def test_build_lookmedia_content_returns_none_without_container():
    assert scrape._build_lookmedia_content("<html><body><p>plain page</p></body></html>") is None


def test_build_yahoo_content_excludes_recommendation_rail():
    # Yahoo 新聞's "其他人也在看" rail embeds WHOLE recommended articles, not
    # links — trafilatura pulled an unrelated NBA story and a buffet promo
    # into a tech article (reported 2026-07-25). Only div.atoms is the body.
    html = """
    <html><body>
      <div class="article-wrapper">
        <section class="module-article-body">
          <div class="mx-auto">
            <nav>Yahoo新聞 新聞總覽</nav>
            <h1>Anthropic 推出 Claude Opus 5</h1>
            <div class="atoms">
              <p>Anthropic 今日正式發佈最新的大型 AI 模型 Claude Opus 5。</p>
              <img src="https://s.yimg.com/a.jpg">
              <h2>價格與可用性</h2>
              <p>即日起在所有平台上線，定價與 Opus 4.8 相同。</p>
            </div>
          </div>
        </section>
        <section class="mt-module-gap">
          <h2>其他人也在看</h2>
          <div class="atoms">
            <p>LeBron James 將以 2 年 800 萬美元合約加盟 Philadelphia 76ers。</p>
          </div>
        </section>
      </div>
    </body></html>
    """
    out = scrape._build_yahoo_content(html)

    assert "Claude Opus 5" in out
    assert "價格與可用性" in out
    assert 'src="https://s.yimg.com/a.jpg"' in out
    # The rail must not leak in.
    assert "LeBron" not in out
    assert "其他人也在看" not in out


def test_build_yahoo_content_strips_inline_ad_markers():
    html = """
    <section class="module-article-body"><div class="atoms">
      <p>真正的新聞內容，需要夠長先可以通過最短字數門檻檢查，所以呢句刻意寫長啲。</p>
      <p>廣告</p>
      <p>第二段真正的新聞內容，同樣需要有足夠長度先唔會被短內容過濾器擋走。</p>
    </div></section>
    """
    out = scrape._build_yahoo_content(html)

    assert "<p>廣告</p>" not in out
    assert "第二段真正的新聞內容" in out


def test_build_yahoo_content_dedupes_repeated_images():
    html = """
    <section class="module-article-body"><div class="atoms">
      <p>足夠長度的新聞內文，用嚟通過最短字數門檻，所以要寫得長少少先得，唔係會返 None。</p>
      <img src="https://s.yimg.com/dup.jpg">
      <img src="https://s.yimg.com/dup.jpg">
    </div></section>
    """
    out = scrape._build_yahoo_content(html)

    assert out.count('src="https://s.yimg.com/dup.jpg"') == 1


def test_build_yahoo_content_returns_none_without_body_container():
    # No div.atoms → let the caller fall back to trafilatura rather than
    # emitting an empty shell.
    assert scrape._build_yahoo_content("<html><body><p>x</p></body></html>") is None


def test_build_yahoo_content_returns_none_when_too_short():
    html = '<section class="module-article-body"><div class="atoms"><p>短</p></div></section>'
    assert scrape._build_yahoo_content(html) is None


def test_is_yahoo_url_matches_hk_news_yahoo():
    assert scrape._is_yahoo_url("https://hk.news.yahoo.com/a-123456.html") is True
    assert scrape._is_yahoo_url("https://www.hk01.com/a/1") is False
