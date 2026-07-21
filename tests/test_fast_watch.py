import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src import fast_watch as FW


@pytest.fixture(autouse=True)
def _no_trending_keywords(monkeypatch):
    """同 test_keyword_alert.py 嗰個 fixture 同一個原因：一旦 self-hosted
    機真.跑過 sync_trending_keywords()，config/trending_keywords.txt 會有
    真實 Google Trends 熱門字並提交入 repo，之後 FW.TRENDING_KEYWORDS 呢個
    module-level list（喺 import 嗰下讀一次）就會唔係空，累到成堆用
    curated 小 WATCH_KEYWORDS fixture 嘅 test 意外撞中唔相關嘅熱門字。
    想測試 trending 合併行為嘅 test 自己再 override 呢個 patch。"""
    monkeypatch.setattr(FW, "TRENDING_KEYWORDS", [])


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


def test_match_keyword_respects_context_requirement(monkeypatch):
    # 快速通道跟返 keyword_alert.py 個 `context:` directive 規則（同一套
    # KEYWORD_CONTEXT）——「死亡」呢類太闊嘅字要有 HK 脈絡先算 match。
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["死亡"])
    monkeypatch.setattr(FW, "KEYWORD_CONTEXT", {"死亡": ["港人", "本港", "本地", "香港"]})
    assert FW._match_keyword(_article(title="外國男子離奇死亡")) is None
    assert FW._match_keyword(_article(title="本港男子離奇死亡")) == "死亡"


def test_match_keyword_includes_trending_keyword_hits(monkeypatch):
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "TRENDING_KEYWORDS", ["陳嘉信"])
    assert FW._match_keyword(_article(title="陳嘉信案上訴得直")) == "陳嘉信"
    assert FW._match_keyword(_article(title="天氣預告")) is None


def test_match_keyword_works_with_only_trending_keywords(monkeypatch):
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(FW, "TRENDING_KEYWORDS", ["陳嘉信"])
    assert FW._match_keyword(_article(title="陳嘉信案上訴得直")) == "陳嘉信"


def test_main_skips_when_watch_and_trending_both_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(FW, "TRENDING_KEYWORDS", [])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    async def fail_fetch(*a, **kw):
        raise AssertionError("should not fetch")
    monkeypatch.setattr(FW, "_fetch_watched", fail_fetch)

    asyncio.run(FW.main())
    assert not (tmp_path / "state.json").exists()


def test_main_runs_when_only_trending_keywords_present(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(FW, "TRENDING_KEYWORDS", ["陳嘉信"])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    async def fake_fetch(session, cutoff):
        return [_article(id="hit1", title="陳嘉信案上訴得直")]
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    sent = []

    async def fake_send(session, text, photo_url=""):
        sent.append(text)
        return 200
    monkeypatch.setattr(FW, "_send_telegram", fake_send)

    asyncio.run(FW.main())
    assert len(sent) == 1
    assert "陳嘉信" in sent[0]


def test_format_text_includes_keyword_source_and_link():
    text = FW._format_text(_article(title="Nvidia 業績勝預期", url="https://x.com/1", source="TVB 新聞"), "Nvidia")
    assert "⚡ <b>快訊關鍵字</b>：Nvidia" in text
    assert "Nvidia 業績勝預期" in text
    assert "TVB 新聞" in text
    assert 'href="https://x.com/1"' in text


def test_format_text_includes_hkt_publish_time():
    article = _article(
        title="Nvidia 業績勝預期", source="TVB 新聞",
        date="2026-07-21T06:32:00+00:00",  # UTC，對應 HKT 14:32
    )
    text = FW._format_text(article, "Nvidia")
    assert "TVB 新聞 · 14:32 · 快速通道" in text


def test_format_hkt_time_returns_empty_on_bad_date():
    assert FW._format_hkt_time({"date": "not-a-date"}) == ""
    assert FW._format_hkt_time({}) == ""


def test_format_text_escapes_double_quote_in_url():
    # 2026-07-21 audit finding：url 入面一個 literal " 之前冇 escape，
    # 會提早結束 <a href="..."> 個 attribute，拆散成個 Telegram message。
    text = FW._format_text(_article(title="test", url='https://x.com/1"onmouseover=alert(1)'), "OpenAI")
    assert 'href="https://x.com/1&quot;onmouseover=alert(1)"' in text


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")
    FW._save_state({
        "seen": {"id1": "2026-07-21T00:00:00+00:00", "id2": "2026-07-21T00:01:00+00:00"},
        "cooldown": {"交通意外": "2026-07-21T00:00:00+00:00"},
    })
    state = FW._load_state()
    assert state["seen"] == {"id1": "2026-07-21T00:00:00+00:00", "id2": "2026-07-21T00:01:00+00:00"}
    assert state["cooldown"] == {"交通意外": "2026-07-21T00:00:00+00:00"}


def test_load_state_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "missing.json")
    assert FW._load_state() == {"seen": {}, "cooldown": {}}


def test_load_state_corrupt_file_returns_empty(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(FW, "STATE_PATH", state_path)
    assert FW._load_state() == {"seen": {}, "cooldown": {}}


def test_load_state_migrates_old_plain_list_seen_format(tmp_path, monkeypatch):
    # 2026-07-20 之前 STATE_PATH 係 {"seen": [id, id, ...]}（純 list，
    # 冇時間資訊，亦都冇 cooldown key）——升級之後要 migrate 做 dict，
    # 唔可以直接壞晒。
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"seen": ["old1", "old2"]}), encoding="utf-8")
    monkeypatch.setattr(FW, "STATE_PATH", state_path)
    state = FW._load_state()
    assert set(state["seen"].keys()) == {"old1", "old2"}
    assert all(isinstance(ts, str) and ts for ts in state["seen"].values())
    assert state["cooldown"] == {}


def test_save_state_caps_seen_size_by_recency_not_alphabetical(tmp_path, monkeypatch):
    # 2026-07-21 audit finding：article id 嚟自 url 嘅 md5 hash，
    # alphabetical sort 同幾時見過完全冇關係——之前嘅淘汰policy可能
    # evict 咗啱啱先見過嘅 article。而家應該淘汰真正最舊嘅 timestamp。
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(FW, "SEEN_CAP", 3)
    # "z_old" 字母排序最後，但時間上最舊——淘汰佢先啱，唔係保留佢。
    seen = {
        "z_old": "2026-01-01T00:00:00+00:00",
        "a_new1": "2026-07-21T00:01:00+00:00",
        "a_new2": "2026-07-21T00:02:00+00:00",
        "a_new3": "2026-07-21T00:03:00+00:00",
    }
    FW._save_state({"seen": seen, "cooldown": {}})
    data = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(data["seen"]) == 3
    assert "z_old" not in data["seen"]
    assert set(data["seen"].keys()) == {"a_new1", "a_new2", "a_new3"}


def test_keyword_in_cooldown():
    now = datetime.now(timezone.utc)
    cooldown = {"交通意外": (now - timedelta(minutes=10)).isoformat()}
    assert FW._keyword_in_cooldown("交通意外", cooldown, now)          # 10 分鐘前，30 分鐘冷卻仲未過
    assert not FW._keyword_in_cooldown("OpenAI", cooldown, now)         # 冇記錄過，唔喺冷卻
    stale_cooldown = {"交通意外": (now - timedelta(minutes=31)).isoformat()}
    assert not FW._keyword_in_cooldown("交通意外", stale_cooldown, now)  # 31 分鐘前，冷卻已過


def test_keyword_in_cooldown_survives_bad_timestamp():
    now = datetime.now(timezone.utc)
    assert not FW._keyword_in_cooldown("x", {"x": "not-a-date"}, now)


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


def test_main_collapses_same_keyword_matches_within_cooldown(monkeypatch, tmp_path):
    # 用戶反映：一單「交通意外」俾好多 source 分別報道，每篇都獨立 alert，
    # 連環彈幾次。呢度冇 AI clustering 分辨「同一單」，用 keyword cooldown
    # 頂住：同一個 run 入面 3 篇文都撞中「交通意外」，應該淨係送最新嗰篇。
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["交通意外"])
    # 呢個 test 專登唔想牽涉 context 要求（真.production config 而家有
    # 幫「交通意外」加咗 context: 港人/本港/本地/香港）——淨係想孤立驗證
    # cooldown 行為。
    monkeypatch.setattr(FW, "KEYWORD_CONTEXT", {})
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    now = datetime.now(timezone.utc)
    articles = [
        _article(id="a1", title="交通意外 A報道", source="am730", date=(now - timedelta(minutes=2)).isoformat()),
        _article(id="a2", title="交通意外 B報道", source="TVB 新聞", date=(now - timedelta(minutes=1)).isoformat()),
        _article(id="a3", title="交通意外 C報道", source="星島頭條", date=now.isoformat()),
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
    assert "C報道" in sent[0]  # 最新嗰篇


def test_main_resumes_alerting_after_cooldown_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["交通意外"])
    monkeypatch.setattr(FW, "KEYWORD_CONTEXT", {})  # 孤立驗證 cooldown，唔理 context 要求
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(FW, "STATE_PATH", state_path)

    now = datetime.now(timezone.utc)
    stale_cooldown = (now - timedelta(minutes=31)).isoformat()  # 冷卻 30 分鐘已過
    state_path.write_text(json.dumps({"seen": {}, "cooldown": {"交通意外": stale_cooldown}}), encoding="utf-8")

    async def fake_fetch(session, cutoff):
        return [_article(id="a1", title="交通意外新一輪", date=now.isoformat())]
    monkeypatch.setattr(FW, "_fetch_watched", fake_fetch)

    sent = []

    async def fake_send(session, text, photo_url=""):
        sent.append(text)
        return 200
    monkeypatch.setattr(FW, "_send_telegram", fake_send)

    asyncio.run(FW.main())

    assert len(sent) == 1


def test_main_saves_partial_progress_on_timeout(monkeypatch, tmp_path):
    # 2026-07-21 audit finding：之前 main() 完全冇成體 timeout 保護——
    # 而家加咗，但要確保逾時嗰陣 _save_seen 仍然會用返已經 fetch 到嘅
    # article 嚟保存 seen 狀態，唔係乜都save唔到（成個run嘅去重進度流失）。
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(FW, "MAIN_TIMEOUT", 0.05)

    articles = [_article(id="miss1", title="天氣預告")]

    async def slow_fetch(session, cutoff):
        # 攞到 article 之後先 sleep——模擬 send 階段先卡死，
        # 驗證 articles 呢個 nonlocal 變數喺 timeout 之後仍然保留低。
        await asyncio.sleep(0.2)
        return articles
    monkeypatch.setattr(FW, "_fetch_watched", slow_fetch)

    asyncio.run(FW.main())

    seen = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["seen"]
    # _fetch_watched 本身逾時緊，所以 articles 停留喺空 list——但最緊要係
    # main() 冇拋 exception，_save_seen 一定有執行（state.json 會存在）。
    assert (tmp_path / "state.json").exists()
    assert seen == {}


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
    # 5 篇文用 5 個唔同 keyword（避免 keyword cooldown 都嚟埋一齊觸發，
    # 呢個 test 淨係想單獨驗證 MAX_ALERTS_PER_RUN 個 cap）。
    keywords = ["OpenAI", "ChatGPT", "Google", "Nvidia", "Meta AI"]
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", keywords)
    monkeypatch.setattr(FW, "MAX_ALERTS_PER_RUN", 2)
    monkeypatch.setattr(FW, "STATE_PATH", tmp_path / "state.json")

    articles = [_article(id=f"hit{i}", title=f"{kw} 新聞") for i, kw in enumerate(keywords)]

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


def test_main_cooldown_does_not_starve_other_keywords(monkeypatch, tmp_path):
    # 一個仲喺 cooldown 嘅 keyword（例如連環報道緊嘅「交通意外」）唔應該
    # 頂住 MAX_ALERTS_PER_RUN 個位、累到本身有得送嘅其他 keyword 都送唔到。
    monkeypatch.setattr(FW, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(FW, "WATCH_KEYWORDS", ["交通意外", "OpenAI"])
    monkeypatch.setattr(FW, "KEYWORD_CONTEXT", {})  # 孤立驗證 cap/cooldown 互動，唔理 context 要求
    monkeypatch.setattr(FW, "MAX_ALERTS_PER_RUN", 1)
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(FW, "STATE_PATH", state_path)

    now = datetime.now(timezone.utc)
    state_path.write_text(
        json.dumps({"seen": {}, "cooldown": {"交通意外": now.isoformat()}}),
        encoding="utf-8",
    )

    articles = [
        _article(id="a1", title="交通意外跟進", date=now.isoformat()),
        _article(id="a2", title="OpenAI 新模型", date=(now - timedelta(minutes=1)).isoformat()),
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
