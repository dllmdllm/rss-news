import asyncio
import json
from datetime import datetime, timezone

from src import fast_watch as FW


def _article(**overrides):
    base = {
        "id": "a1",
        "title": "測試標題",
        "url": "https://example.com/a1",
        "rss_content": None,
        "source": "am730",
        "date": datetime.now(timezone.utc).isoformat(),
        "thumbnail": "",
    }
    base.update(overrides)
    return base


def test_match_keyword_checks_title_and_rss_content(monkeypatch):
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI", "樓市"])
    assert FW._match_keyword(_article(title="OpenAI 發布新模型")) == "OpenAI"
    assert FW._match_keyword(_article(title="財經", rss_content="樓市成交急升")) == "樓市"
    assert FW._match_keyword(_article(title="ChatGPT 新功能")) is None  # exact-substring, no alias


def test_match_keyword_case_insensitive(monkeypatch):
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    assert FW._match_keyword(_article(title="openai 宣布新進展")) == "OpenAI"


def test_format_text_includes_keyword_source_and_link():
    text = FW._format_text(_article(title="Nvidia 業績勝預期", url="https://x.com/1", source="TVB 新聞"), "Nvidia")
    assert "⚡ <b>快訊關鍵字</b>：Nvidia" in text
    assert "Nvidia 業績勝預期" in text
    assert "TVB 新聞" in text
    assert 'href="https://x.com/1"' in text


def test_seen_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")
    FW._save_seen({"id1", "id2"})
    assert FW._load_seen() == {"id1", "id2"}


def test_load_seen_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "missing.json")
    assert FW._load_seen() == set()


def test_load_seen_corrupt_file_returns_empty(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(FW, "STATE_PATH", state_path)
    assert FW._load_seen() == set()


def test_save_seen_caps_size(tmp_path, monkeypatch):
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(FW, "SEEN_CAP", 3)
    FW._save_seen({f"id{i}" for i in range(10)})
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(data["seen"]) == 3


def test_main_skips_without_token(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    async def fail_fetch(*a, **kw):
        raise AssertionError("should not fetch")
    monkeypatch.setattr(FW, "_fetch_watched", fail_fetch)

    asyncio.run(FW.main())
    assert not (tmp_path / "state.json").exists()


def test_main_skips_without_keywords(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    async def fail_fetch(*a, **kw):
        raise AssertionError("should not fetch")
    monkeypatch.setattr(FW, "_fetch_watched", fail_fetch)

    asyncio.run(FW.main())
    assert not (tmp_path / "state.json").exists()


def test_main_alerts_new_match_and_persists_seen(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    articles = [
        _article(id="hit1", title="OpenAI 發布新模型"),
        _article(id="miss1", title="天氣預告"),
    ]

    async def fake_fetch(session, cutoff):
        return articles
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    sent = []

    async def fake_send(session, text, photo_url=""):
        sent.append(text)
        return 200
    monkeypatch.setattr(FW, "_send_telegram", fake_send)

    asyncio.run(FW.main())

    assert len(sent) == 1
    assert "OpenAI" in sent[0]
    seen = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["seen"]
    assert set(seen) == {"hit1", "miss1"}


def test_main_does_not_realert_seen_article(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"seen": ["hit1"]}), encoding="utf-8")
    monkeypatch.setattr(FW, "STATE_PATH", state_path)

    async def fake_fetch(session, cutoff):
        return [_article(id="hit1", title="OpenAI 發布新模型")]
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    async def fail_send(*a, **kw):
        raise AssertionError("should not re-alert an already-seen article")
    monkeypatch.setattr(FW, "_send_telegram", fail_send)

    asyncio.run(FW.main())


def test_main_respects_max_alerts_per_run(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "MAX_ALERTS_PER_RUN", 2)
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    articles = [_article(id=f"hit{i}", title=f"OpenAI 新聞 {i}") for i in range(5)]

    async def fake_fetch(session, cutoff):
        return articles
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    sent = []

    async def fake_send(session, text, photo_url=""):
        sent.append(text)
        return 200
    monkeypatch.setattr(FW, "_send_telegram", fake_send)

    asyncio.run(FW.main())
    assert len(sent) == 2


def test_main_does_not_mark_seen_on_failed_send(monkeypatch, tmp_path):
    # 2026-07-21 audit finding: send 失敗嘅 article 之前都會被計入 seen，
    # 令個 alert 永久唔會補發。而家改做：淨係成功 send 咗先計入 seen，
    # 送失敗嘅要留低俾下一輪（仲喺 freshness window 內）再試。
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    articles = [_article(id="hit1", title="OpenAI 發布新模型")]

    async def fake_fetch(session, cutoff):
        return articles
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    async def failing_send(session, text, photo_url=""):
        return 500
    monkeypatch.setattr(FW, "_send_telegram", failing_send)

    asyncio.run(FW.main())

    seen = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["seen"]
    assert "hit1" not in seen


def test_main_does_not_mark_seen_when_capped_out(monkeypatch, tmp_path):
    # 撞中 keyword 但因為 MAX_ALERTS_PER_RUN 冇輪到send嘅 article，
    # 都唔應該計入 seen——否則永遠冇機會補送。
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "MAX_ALERTS_PER_RUN", 1)
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    articles = [_article(id=f"hit{i}", title=f"OpenAI 新聞 {i}") for i in range(3)]

    async def fake_fetch(session, cutoff):
        return articles
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    async def fake_send(session, text, photo_url=""):
        return 200
    monkeypatch.setattr(FW, "_send_telegram", fake_send)

    asyncio.run(FW.main())

    seen = set(json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["seen"])
    sent_count = sum(1 for i in range(3) if f"hit{i}" in seen)
    assert sent_count == 1          # 淨係真正 send 咗嗰個入 seen
    assert len(seen) == 1           # 另外 2 個未輪到嘅冇入 seen，下一輪可以再撞返
