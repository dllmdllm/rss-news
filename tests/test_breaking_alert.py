import asyncio

from src.breaking_alert import _send_telegram, detect_breaking_clusters


class _FakeResp:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return ""


class _FakeSession:
    """Records each session.post(...) call and returns statuses in order."""

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        status = self._statuses.pop(0) if self._statuses else 200
        return _FakeResp(status)


def test_send_telegram_uses_sendphoto_when_photo_url_given():
    session = _FakeSession([200])
    status = asyncio.run(_send_telegram(session, "caption text", photo_url="https://example.com/a.jpg"))
    assert status == 200
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"].endswith("/sendPhoto")
    assert call["json"]["photo"] == "https://example.com/a.jpg"
    assert call["json"]["caption"] == "caption text"


def test_send_telegram_omits_photo_field_when_no_photo_url():
    session = _FakeSession([200])
    asyncio.run(_send_telegram(session, "text only"))
    call = session.calls[0]
    assert call["url"].endswith("/sendMessage")
    assert call["json"]["text"] == "text only"
    assert "photo" not in call["json"]


def test_send_telegram_falls_back_to_sendmessage_when_sendphoto_fails():
    # Bad/dead thumbnail URL → Telegram rejects sendPhoto (400) → caller must
    # still get the notification via plain sendMessage, not silently drop it.
    session = _FakeSession([400, 200])
    status = asyncio.run(_send_telegram(session, "caption text", photo_url="https://example.com/dead.jpg"))
    assert status == 200
    assert len(session.calls) == 2
    assert session.calls[0]["url"].endswith("/sendPhoto")
    assert session.calls[1]["url"].endswith("/sendMessage")
    assert session.calls[1]["json"]["text"] == "caption text"


def test_detect_breaking_clusters_includes_thumbnail_of_best_article():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    articles = [
        {"id": "a", "cluster_id": "c1", "source": "A", "date": now, "score": 5,
         "title": "低分", "thumbnail": "https://example.com/low.jpg", "url": "https://example.com/low"},
        {"id": "b", "cluster_id": "c1", "source": "B", "date": now, "score": 9,
         "title": "高分", "thumbnail": "https://example.com/high.jpg", "url": "https://example.com/high"},
        {"id": "c", "cluster_id": "c1", "source": "C", "date": now, "score": 3,
         "title": "都係呢單", "thumbnail": ""},
    ]
    breaking = detect_breaking_clusters(articles)
    assert len(breaking) == 1
    assert breaking[0]["thumbnail"] == "https://example.com/high.jpg"
    assert breaking[0]["url"] == "https://example.com/high"


def test_detect_breaking_clusters_picks_best_from_recent_not_full_history():
    # 2026-07-21 audit finding：之前 `best` 揀自成個 cluster 嘅全部歷史
    # （可達 ~30h），唔係真正令個 cluster 變 breaking 嘅 fresh member——
    # 一篇舊、高分文章可以蓋過真正觸發 breaking 嘅新報導。
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=15)).isoformat()
    fresh = now.isoformat()
    articles = [
        # 15 小時前嘅高分舊文——唔喺 BREAKING_WINDOW_HOURS(2h) 之內，
        # 唔應該被揀做 best，就算分數最高。
        {"id": "old", "cluster_id": "c1", "source": "A", "date": old, "score": 10,
         "title": "舊文高分", "url": "https://example.com/old"},
        # 3 個新 source 先真正觸發 breaking。
        {"id": "b", "cluster_id": "c1", "source": "B", "date": fresh, "score": 5,
         "title": "新報導B", "url": "https://example.com/b"},
        {"id": "c", "cluster_id": "c1", "source": "C", "date": fresh, "score": 6,
         "title": "新報導C", "url": "https://example.com/c"},
        {"id": "d", "cluster_id": "c1", "source": "D", "date": fresh, "score": 4,
         "title": "新報導D", "url": "https://example.com/d"},
    ]
    breaking = detect_breaking_clusters(articles)
    assert len(breaking) == 1
    assert breaking[0]["article_id"] == "c"   # fresh 入面分數最高嗰篇（6），唔係「old」
    assert breaking[0]["headline"] == "新報導C"


def test_format_alert_text_includes_summary_bullets_and_sources_last():
    from src.breaking_alert import _format_alert_text

    text = _format_alert_text({
        "headline": "測試突發標題 <b>",
        "summary": "・重點一\n・重點二\n・重點三\n・重點四\n・重點五\n・重點六",
        "sources": ["明報", "東網", "RTHK", "星島", "HK01", "am730"],
    })
    lines = text.split("\n")
    assert lines[0] == "🔴 <b>突發</b>：測試突發標題 &lt;b&gt;"
    # 最多 5 點
    assert [l for l in lines if l.startswith("・")] == [
        "・重點一", "・重點二", "・重點三", "・重點四", "・重點五"]
    # 來源最尾，最多 5 個
    assert lines[-1] == "來源：明報、東網、RTHK、星島、HK01"


def test_format_alert_text_without_summary_keeps_headline_and_sources():
    from src.breaking_alert import _format_alert_text

    text = _format_alert_text({"headline": "冇摘要", "summary": "", "sources": ["明報"]})
    assert text == "🔴 <b>突發</b>：冇摘要\n來源：明報"


def test_format_alert_text_appends_link_when_url_present():
    from src.breaking_alert import _format_alert_text

    text = _format_alert_text({
        "headline": "有連結", "summary": "", "sources": ["明報"], "url": "https://example.com/x",
    })
    assert text.endswith('<a href="https://example.com/x">睇原文</a>')
