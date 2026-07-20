import asyncio
from datetime import datetime, timedelta, timezone

from src import keyword_alert as KA


def _article(**overrides):
    base = {
        "id": "a1",
        "title": "測試標題",
        "summary": "・重點一\n・重點二",
        "tags": [],
        "category": "新聞",
        "source": "RTHK 本地",
        "date": datetime.now(timezone.utc).isoformat(),
        "thumbnail": "",
    }
    base.update(overrides)
    return base


def test_detect_keyword_matches_case_insensitive_and_substring(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["樓市", "ai"])
    now = datetime.now(timezone.utc)
    articles = [
        _article(id="hit1", title="樓市成交急升", date=now.isoformat()),
        _article(id="hit2", title="AI晶片股價急挫", date=now.isoformat()),
        _article(id="miss", title="天氣預告", date=now.isoformat()),
    ]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    ids = {a["id"] for a in out}
    assert ids == {"hit1", "hit2"}


def test_detect_keyword_matches_skips_stale_and_already_alerted(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["樓市"])
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(hours=5)).isoformat()
    articles = [
        _article(id="old", title="樓市舊聞", date=stale),
        _article(id="seen", title="樓市新聞", date=now.isoformat()),
    ]
    out = KA.detect_keyword_matches(articles, {"seen"}, now=now)
    assert out == []


def test_detect_keyword_matches_respects_cap(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["新聞"])
    monkeypatch.setattr(KA, "MAX_ALERTS_PER_BUILD", 2)
    now = datetime.now(timezone.utc)
    articles = [_article(id=f"a{i}", title=f"新聞{i}", date=now.isoformat()) for i in range(5)]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    assert len(out) == 2


def test_detect_keyword_matches_empty_watchlist_returns_nothing(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", [])
    now = datetime.now(timezone.utc)
    out = KA.detect_keyword_matches([_article(title="樓市")], set(), now=now)
    assert out == []


def test_format_alert_text_includes_keyword_summary_and_meta():
    article = _article(title="樓市成交急升", summary="・重點一\n・重點二")
    article["_matched_keyword"] = "樓市"
    text = KA._format_alert_text(article)
    assert text.startswith("🔔 <b>關鍵字提醒</b>：樓市\n樓市成交急升")
    assert "・重點一" in text
    assert "新聞 · RTHK 本地" in text


def test_send_keyword_alerts_noop_without_keywords(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(KA, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(KA, "STATE_PATH", tmp_path / "keyword_alerts.json")

    async def fail_send(*a, **kw):
        raise AssertionError("should not call telegram")
    monkeypatch.setattr(KA, "_send_telegram", fail_send)

    asyncio.run(KA.send_keyword_alerts([_article(title="樓市")]))
    assert (tmp_path / "keyword_alerts.json").exists()


def test_send_keyword_alerts_dedupes_across_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["樓市"])
    monkeypatch.setattr(KA, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(KA, "STATE_PATH", tmp_path / "keyword_alerts.json")
    calls = {"n": 0}

    async def fake_send(session, text, photo_url=""):
        calls["n"] += 1
        return 200
    monkeypatch.setattr(KA, "_send_telegram", fake_send)

    articles = [_article(id="hit1", title="樓市成交急升")]
    asyncio.run(KA.send_keyword_alerts(articles))
    assert calls["n"] == 1

    # 第二次 build 見到同一篇文（未過 FRESHNESS window）——唔應該再推
    asyncio.run(KA.send_keyword_alerts(articles))
    assert calls["n"] == 1
