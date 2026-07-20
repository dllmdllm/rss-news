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
