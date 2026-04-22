from datetime import datetime, timezone

from src.fetch import _parse_oncc_index, _parse_title_translations


def test_parse_title_translations_accepts_json_array():
    raw = '["蘋果宣布新產品", "AI 晶片需求上升"]'

    assert _parse_title_translations(raw, 2) == ["蘋果宣佈新產品", "AI 晶片需求上升"]


def test_parse_title_translations_rejects_wrong_length():
    raw = '["只有一個標題"]'

    assert _parse_title_translations(raw, 2) is None


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
