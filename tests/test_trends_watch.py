import asyncio
from datetime import datetime, timedelta, timezone

from src import trends_watch as TW

_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
<channel>
<title>Daily Search Trends</title>
<item>
<title>陈嘉信</title>
<ht:approx_traffic>100+</ht:approx_traffic>
<ht:news_item>
<ht:news_item_title>呢個唔應該當做 top-level title 攞到</ht:news_item_title>
</ht:news_item>
</item>
<item>
<title>金</title>
</item>
<item>
<title>屯門公路</title>
</item>
</channel>
</rss>
"""


class _FakeTextResp:
    def __init__(self, text, status=200):
        self._text = text
        self.status = status
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False
    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")
    async def text(self):
        return self._text


class _FakeTextSession:
    def __init__(self, text, status=200):
        self._text = text
        self._status = status
    def get(self, url, headers=None, timeout=None):
        return _FakeTextResp(self._text, self._status)


def test_parse_rss_titles_reads_top_level_item_title_only():
    titles = TW._parse_rss_titles(_SAMPLE_RSS)
    assert titles == ["陈嘉信", "金", "屯門公路"]


def test_parse_rss_titles_returns_empty_on_malformed_xml():
    assert TW._parse_rss_titles("not xml at all") == []


def test_clean_keywords_converts_simplified_to_hk_traditional():
    assert TW._clean_keywords(["陈嘉信"]) == ["陳嘉信"]


def test_clean_keywords_drops_single_char_terms():
    # 「金」單字撞正太多常見詞（金融/現金/基金……），過濾走。
    assert TW._clean_keywords(["金", "屯門公路"]) == ["屯門公路"]


def test_clean_keywords_dedupes_preserving_order():
    assert TW._clean_keywords(["屯門公路", "陳嘉信", "屯門公路"]) == ["屯門公路", "陳嘉信"]


def test_clean_keywords_respects_max_cap(monkeypatch):
    monkeypatch.setattr(TW, "MAX_KEYWORDS", 2)
    out = TW._clean_keywords(["一二", "三四", "五六", "七八"])
    assert out == ["一二", "三四"]


def test_load_synced_date_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(TW, "CONFIG_PATH", tmp_path / "missing.txt")
    assert TW._load_synced_date() == ""


def test_write_config_then_load_roundtrip(tmp_path, monkeypatch):
    config_path = tmp_path / "trending_keywords.txt"
    monkeypatch.setattr(TW, "CONFIG_PATH", config_path)
    TW._write_config(["陳嘉信", "屯門公路"], "2026-07-21")
    assert TW._load_synced_date() == "2026-07-21"
    assert TW.load_trending_keywords() == ["陳嘉信", "屯門公路"]


def test_load_trending_keywords_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(TW, "CONFIG_PATH", tmp_path / "missing.txt")
    assert TW.load_trending_keywords() == []


def test_should_sync_true_when_never_synced(tmp_path, monkeypatch):
    monkeypatch.setattr(TW, "CONFIG_PATH", tmp_path / "missing.txt")
    assert TW.should_sync(datetime.now(TW.HKT)) is True


def test_should_sync_false_when_already_synced_today(tmp_path, monkeypatch):
    config_path = tmp_path / "trending_keywords.txt"
    monkeypatch.setattr(TW, "CONFIG_PATH", config_path)
    now = datetime(2026, 7, 21, 10, 0, tzinfo=TW.HKT)
    TW._write_config(["陳嘉信"], now.strftime("%Y-%m-%d"))
    assert TW.should_sync(now) is False


def test_should_sync_true_on_new_calendar_day(tmp_path, monkeypatch):
    config_path = tmp_path / "trending_keywords.txt"
    monkeypatch.setattr(TW, "CONFIG_PATH", config_path)
    TW._write_config(["陳嘉信"], "2026-07-20")
    assert TW.should_sync(datetime(2026, 7, 21, 0, 5, tzinfo=TW.HKT)) is True


def test_fetch_trending_keywords_parses_and_cleans():
    session = _FakeTextSession(_SAMPLE_RSS)
    out = asyncio.run(TW.fetch_trending_keywords(session))
    assert out == ["陳嘉信", "屯門公路"]  # 「金」單字被過濾走，簡轉繁


def test_sync_trending_keywords_skips_fetch_when_already_synced_today(tmp_path, monkeypatch):
    config_path = tmp_path / "trending_keywords.txt"
    monkeypatch.setattr(TW, "CONFIG_PATH", config_path)
    now = datetime(2026, 7, 21, 10, 0, tzinfo=TW.HKT)
    TW._write_config(["舊清單"], now.strftime("%Y-%m-%d"))

    async def fail_fetch(*a, **kw):
        raise AssertionError("should not fetch — already synced today")
    monkeypatch.setattr(TW, "fetch_trending_keywords", fail_fetch)

    out = asyncio.run(TW.sync_trending_keywords(now))
    assert out == ["舊清單"]


def test_sync_trending_keywords_fetches_and_overwrites_on_new_day(tmp_path, monkeypatch):
    config_path = tmp_path / "trending_keywords.txt"
    monkeypatch.setattr(TW, "CONFIG_PATH", config_path)
    TW._write_config(["舊清單"], "2026-07-20")

    async def fake_fetch(session):
        return ["新清單一", "新清單二"]
    monkeypatch.setattr(TW, "fetch_trending_keywords", fake_fetch)

    now = datetime(2026, 7, 21, 0, 5, tzinfo=TW.HKT)
    out = asyncio.run(TW.sync_trending_keywords(now))
    assert out == ["新清單一", "新清單二"]
    assert TW.load_trending_keywords() == ["新清單一", "新清單二"]  # 完全覆寫，冇同舊清單合併
    assert TW._load_synced_date() == "2026-07-21"


def test_sync_trending_keywords_keeps_old_config_on_fetch_failure(tmp_path, monkeypatch):
    config_path = tmp_path / "trending_keywords.txt"
    monkeypatch.setattr(TW, "CONFIG_PATH", config_path)
    TW._write_config(["舊清單"], "2026-07-20")

    async def failing_fetch(session):
        raise RuntimeError("network error")
    monkeypatch.setattr(TW, "fetch_trending_keywords", failing_fetch)

    now = datetime(2026, 7, 21, 0, 5, tzinfo=TW.HKT)
    out = asyncio.run(TW.sync_trending_keywords(now))
    assert out == ["舊清單"]
    assert TW._load_synced_date() == "2026-07-20"  # 冇被覆寫做今日日期
