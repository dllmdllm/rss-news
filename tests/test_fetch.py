from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.fetch import (
    _map_category_for_url,
    _parse_am730_sitemap,
    _parse_date,
    _parse_oncc_index,
    _parse_title_translations,
)


def test_parse_title_translations_accepts_json_array():
    raw = '["蘋果宣布新產品", "AI 晶片需求上升"]'

    assert _parse_title_translations(raw, 2) == ["蘋果宣佈新產品", "AI 晶片需求上升"]


def test_parse_title_translations_rejects_wrong_length():
    raw = '["只有一個標題"]'

    assert _parse_title_translations(raw, 2) is None


def test_parse_date_normalises_naive_rfc_date_to_utc():
    entry = SimpleNamespace(published="Wed, 22 Apr 2026 10:30:00")

    parsed = _parse_date(entry)

    assert parsed == datetime(2026, 4, 22, 10, 30, tzinfo=timezone.utc)
    assert parsed.tzinfo == timezone.utc


def test_parse_date_converts_rfc_date_with_offset_to_utc():
    entry = SimpleNamespace(published="Wed, 22 Apr 2026 18:30:00 +0800")

    parsed = _parse_date(entry)

    assert parsed == datetime(2026, 4, 22, 10, 30, tzinfo=timezone.utc)
    assert parsed.tzinfo == timezone.utc


def test_parse_oncc_index_extracts_recent_section_articles():
    html = """
    <html><body>
      <a href="/hk/bkn/cnt/news/20260422/bkn-20260422093012345-0422_00822_001.html">
        <img src="/img/a.jpg" alt="港聞標題">港聞標題
      </a>
      <a href="/hk/bkn/cnt/intnews/20260422/bkn-20260422094012345-0422_00992_001.html">
        國際標題
      </a>
      <a href="/hk/bkn/cnt/news/20260420/bkn-20260420093012345-0420_00822_001.html">
        舊聞
      </a>
    </body></html>
    """
    feed_info = {
        "name": "東網 本地",
        "url": "https://hk.on.cc/hk/news/index.html",
        "category": "新聞",
        "oncc_section": "news",
    }
    cutoff = datetime(2026, 4, 22, 0, 0, tzinfo=timezone.utc)

    articles = _parse_oncc_index(html, feed_info, cutoff)

    assert len(articles) == 1
    assert articles[0]["title"] == "港聞標題"
    assert articles[0]["source"] == "東網 本地"
    assert articles[0]["category"] == "新聞"
    assert articles[0]["thumbnail"] == "https://hk.on.cc/img/a.jpg"
    assert articles[0]["url"].endswith("_00822_001.html")


def test_parse_date_accepts_iso8601_zulu_string():
    entry = SimpleNamespace(updated="2026-04-22T09:30:00Z")

    parsed = _parse_date(entry)

    assert parsed == datetime(2026, 4, 22, 9, 30, tzinfo=timezone.utc)


def test_parse_date_accepts_iso8601_offset_string():
    entry = SimpleNamespace(updated="2026-04-22T09:30:00+08:00")

    parsed = _parse_date(entry)

    assert parsed == datetime(2026, 4, 22, 1, 30, tzinfo=timezone.utc)


def test_map_category_for_url_supports_percent_encoded_paths():
    feed_info = {
        "category": "新聞",
        "url_category": {
            "/國際/": "國際",
            "/娛樂/": "娛樂",
        },
    }
    encoded = "https://www.am730.com.hk/%E5%9C%8B%E9%9A%9B/%E6%B8%AC%E8%A9%A6/123"

    assert _map_category_for_url(encoded, feed_info) == "國際"


def test_parse_am730_sitemap_extracts_recent_news_entries():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
            xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
            xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
      <url>
        <loc>https://www.am730.com.hk/%E5%9C%8B%E9%9A%9B/foo/100</loc>
        <news:news>
          <news:publication_date>2026-04-22T10:00:00+08:00</news:publication_date>
          <news:title>國際新聞A</news:title>
        </news:news>
        <image:image><image:loc>https://img.am730.com.hk/a.jpg</image:loc></image:image>
      </url>
      <url>
        <loc>https://www.am730.com.hk/%E5%A8%9B%E6%A8%82/bar/200</loc>
        <news:news>
          <news:publication_date>2026-04-20T10:00:00+08:00</news:publication_date>
          <news:title>舊娛樂新聞</news:title>
        </news:news>
      </url>
    </urlset>
    """
    feed_info = {
        "name": "am730",
        "category": "新聞",
        "max_items": 40,
        "url_category": {"/國際/": "國際", "/娛樂/": "娛樂"},
    }
    cutoff = datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc)

    articles = _parse_am730_sitemap(xml, feed_info, cutoff)

    assert len(articles) == 1
    assert articles[0]["title"] == "國際新聞A"
    assert articles[0]["category"] == "國際"
    assert articles[0]["thumbnail"] == "https://img.am730.com.hk/a.jpg"


def test_parse_oncc_dailylist_builds_articles_from_json_feed():
    # 東網娛樂 index 頁係 client-side render 空殼，改用 on.cc dailyList JSON。
    from datetime import datetime, timedelta, timezone
    from src.fetch import _parse_oncc_dailylist

    cutoff = datetime.now(timezone.utc) - timedelta(hours=30)
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d%H%M%S")
    date_part = now_str[:8]
    feed_info = {"name": "東網 娛樂", "category": "娛樂", "oncc_section": "entertainment"}
    data = [
        {
            "articleId": f"bkn-{now_str}909-0715_00862_001",
            "title": "測試娛樂新聞",
            "pubDate": "2026-07-15 09:28:37",
            "thumbnail": f"/cnt/entertainment/{date_part}/photo/a_01p.jpg",
            "content": "測試 teaser 內容",
        },
        {"articleId": "", "title": "冇 id 應該跳過"},
        {
            # 太舊（cutoff 之前）——articleId 日期先係真相
            "articleId": "bkn-20200101000000000-0101_00862_002",
            "title": "舊文",
        },
    ]
    out = _parse_oncc_dailylist(data, feed_info, cutoff)
    assert len(out) == 1
    a = out[0]
    assert a["title"] == "測試娛樂新聞"
    assert a["url"] == f"https://hk.on.cc/hk/bkn/cnt/entertainment/{date_part}/bkn-{now_str}909-0715_00862_001.html"
    assert a["thumbnail"].startswith("https://hk.on.cc/hk/bkn/cnt/entertainment/")
    assert a["category"] == "娛樂"
    assert a["rss_content"] == "測試 teaser 內容"


class _FakeJsonResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False
    async def json(self, content_type=None):
        return self._payload


class _FakeJsonSession:
    def __init__(self, payload):
        self._payload = payload
    def get(self, url, timeout=None, headers=None):
        return _FakeJsonResp(self._payload)


def test_fetch_hk01_skips_malformed_item_without_losing_others():
    # 2026-07-21 audit finding：per-item 處理冇 try/except，一個 item
    # 格式怪（呢度用 mainImage 唔係 dict 嚟模擬）會令成個 function 冇
    # return，連之前已經 append 好嘅 article 都一齊丟失。
    import asyncio
    from src.fetch import _fetch_hk01

    now_ts = int(datetime.now(timezone.utc).timestamp())
    payload = {"items": [
        {"type": 1, "data": {
            "publishUrl": "https://www.hk01.com/a/1", "publishTime": now_ts,
            "title": "正常文章一", "mainImage": {"cdnUrl": "https://img/1.jpg"},
        }},
        {"type": 1, "data": {
            "publishUrl": "https://www.hk01.com/a/2", "publishTime": now_ts,
            "title": "格式怪嘅文章", "mainImage": "not-a-dict",   # 會令 .get() 拋 AttributeError
        }},
        {"type": 1, "data": {
            "publishUrl": "https://www.hk01.com/a/3", "publishTime": now_ts,
            "title": "正常文章三", "mainImage": {"cdnUrl": "https://img/3.jpg"},
        }},
    ]}
    session = _FakeJsonSession(payload)
    feed_info = {"name": "HK01 突發", "category": "新聞", "max_items": 20, "url": "https://www.hk01.com/v2/feed/zone/2"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=30)

    articles, error, _not_modified = asyncio.run(_fetch_hk01(session, feed_info, cutoff))

    assert error is None
    assert [a["title"] for a in articles] == ["正常文章一", "正常文章三"]


def test_fetch_nowtv_skips_malformed_item_without_losing_others():
    import asyncio
    from src.fetch import _fetch_nowtv

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    payload = [
        {"newsId": "1", "title": "正常文章一", "publishDate": now_ms,
         "imageList": [{"imageUrl": "https://img/1.jpg"}]},
        {"newsId": "2", "title": "格式怪嘅文章", "publishDate": now_ms,
         "imageList": "not-a-list"},   # 會令 image_list[0] 拋 TypeError
        {"newsId": "3", "title": "正常文章三", "publishDate": now_ms,
         "imageList": [{"imageUrl": "https://img/3.jpg"}]},
    ]
    session = _FakeJsonSession(payload)
    feed_info = {"name": "Now 國際", "category": "國際", "max_items": 20, "url": "https://news.now.com/home/international"}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=30)

    articles, error, _not_modified = asyncio.run(_fetch_nowtv(session, feed_info, cutoff))

    assert error is None
    assert [a["title"] for a in articles] == ["正常文章一", "正常文章三"]


def test_fetch_oncc_daily_recaps_after_merging_two_days(monkeypatch):
    # 2026-07-21 audit finding：今日/尋日兩個 dailyList 各自內部已經
    # cap 咗 max_items，但 merge 之後冇再 re-cap，實際可以攞到接近雙倍
    # max_items（東網娛樂）。呢個 test 唔理會真實 HTTP/JSON 細節，直接
    # stub `_parse_oncc_dailylist` 每次 call 都返 max_items 個 article，
    # 淨係驗證 merge 完之後嘅結果仲係尊重 max_items。
    import asyncio
    from datetime import timedelta, timezone
    from src import fetch as F

    max_items = 20
    calls = {"n": 0}

    def fake_get(url, timeout=None, headers=None):
        class _Resp:
            status = 200
            async def __aenter__(self_):
                return self_
            async def __aexit__(self_, *exc):
                return False
            async def read(self_):
                return b"[]"
        return _Resp()

    def fake_parse(data, feed_info, cutoff):
        calls["n"] += 1
        base = calls["n"] * 1000
        now = datetime.now(timezone.utc)
        return [
            {
                "id": f"oncc{base + i}",
                "title": f"文章 {base + i}",
                "url": f"https://hk.on.cc/x/{base + i}",
                "date": (now - timedelta(minutes=base + i)).isoformat(),
                "source": feed_info["name"],
                "category": feed_info["category"],
                "content": None,
                "thumbnail": None,
                "rss_content": None,
            }
            for i in range(max_items)
        ]

    monkeypatch.setattr(F, "_parse_oncc_dailylist", fake_parse)
    session = SimpleNamespace(get=fake_get)
    feed_info = {"name": "東網 娛樂", "category": "娛樂", "oncc_section": "entertainment", "max_items": max_items}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=30)

    articles, error, _not_modified = asyncio.run(F._fetch_oncc_daily(session, feed_info, cutoff))

    assert calls["n"] == 2          # 今日 + 尋日各 call 一次
    assert len(articles) == max_items   # 之前呢度會係 max_items*2（40）


def test_nowtv_category_slug_matches_real_site_paths():
    # Regression: category 120 used to map to "world", a slug that returns
    # HTTP 200 with an empty body on news.now.com (silently producing 20
    # empty-content articles for 2 days, caught via
    # test_content_sidecars_are_valid_and_nonempty). Locks the mapping to
    # the slugs confirmed live against the real site.
    from src.fetch import _NOWTV_CATEGORY_SLUG
    assert _NOWTV_CATEGORY_SLUG["119"] == "local"
    assert _NOWTV_CATEGORY_SLUG["120"] == "international"
    assert _NOWTV_CATEGORY_SLUG["122"] == "china"
