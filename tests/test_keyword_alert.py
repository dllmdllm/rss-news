import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src import keyword_alert as KA


@pytest.fixture(autouse=True)
def _no_trending_keywords(monkeypatch):
    """一旦 self-hosted 機真.跑過 sync_trending_keywords()，
    config/trending_keywords.txt 會有真實 Google Trends 熱門字並提交入
    repo，之後呢個 module 嘅 load_trending_keywords() 就會攞到非空清單，
    累到成堆用 curated 小 WATCH_KEYWORDS fixture 嘅 test 意外多撞中一個
    唔相關嘅熱門字（同 2026-07-21 KEYWORD_CONTEXT 嗰個 test-isolation bug
    同一類）。想測試 trending 合併行為嘅 test 自己再 override 呢個 patch。"""
    monkeypatch.setattr(KA, "load_trending_keywords", lambda: [])


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
        "url": "https://example.com/a1",
    }
    base.update(overrides)
    return base


def test_parse_keyword_lines_plain_list():
    text = "OpenAI\nChatGPT\n\nGoogle\n"
    assert KA._parse_keyword_lines(text) == ["OpenAI", "ChatGPT", "Google"]


def test_parse_keyword_lines_skips_frontmatter_and_comments():
    text = "\n".join([
        "---",
        "type: note",
        "tags: [a, b]",
        "---",
        "",
        "# 說明文字，成行 # 開頭先當 comment",
        "OpenAI",
        "# 分類標題",
        "ChatGPT",
    ])
    assert KA._parse_keyword_lines(text) == ["OpenAI", "ChatGPT"]


def test_parse_keyword_lines_only_reads_after_section_marker():
    text = "\n".join([
        "---",
        "type: note",
        "---",
        "",
        "呢段係自由文字，唔係comment，但都要skip，因為未去到標題",
        "仲有第二段explain文字都要skip",
        "",
        "## 關鍵字清單",
        "",
        "# OpenAI",
        "OpenAI",
        "ChatGPT",
    ])
    assert KA._parse_keyword_lines(text) == ["OpenAI", "ChatGPT"]


def test_parse_keyword_lines_strips_bullet_prefixes():
    text = "- OpenAI\n* ChatGPT\nGoogle"
    assert KA._parse_keyword_lines(text) == ["OpenAI", "ChatGPT", "Google"]


def test_parse_vault_keyword_lines_requires_marker():
    text = "---\ntype: note\n---\n\n自由文字，冇標題\nOpenAI\n"
    assert KA._parse_vault_keyword_lines(text) is None


def test_parse_vault_keyword_lines_returns_list_after_marker():
    text = "\n".join([
        "---", "type: note", "---", "",
        "自由文字，唔理", "",
        "## 關鍵字清單", "",
        "# 分類", "OpenAI", "ChatGPT",
    ])
    assert KA._parse_vault_keyword_lines(text) == ["OpenAI", "ChatGPT"]


def test_parse_keyword_rules_applies_context_to_following_keywords():
    raw = ["OpenAI", "context: 港人, 本港, 本地, 香港", "死亡", "自殺", "context:", "交通意外"]
    keywords, context = KA._parse_keyword_rules(raw)
    assert keywords == ["OpenAI", "死亡", "自殺", "交通意外"]
    assert context == {
        "死亡": ["港人", "本港", "本地", "香港"],
        "自殺": ["港人", "本港", "本地", "香港"],
    }
    assert "OpenAI" not in context
    assert "交通意外" not in context  # 一個bare "context:" 清空咗要求


def test_parse_keyword_rules_no_directive_means_no_context():
    keywords, context = KA._parse_keyword_rules(["OpenAI", "ChatGPT"])
    assert keywords == ["OpenAI", "ChatGPT"]
    assert context == {}


def test_first_qualifying_keyword_requires_context_word_present(monkeypatch):
    monkeypatch.setattr(KA, "KEYWORD_CONTEXT", {"死亡": ["港人", "本港", "本地", "香港"]})
    keywords = [("死亡", "死亡")]
    assert KA._first_qualifying_keyword("外國一名男子死亡", keywords) is None  # 冇HK脈絡
    assert KA._first_qualifying_keyword("本港一名男子死亡", keywords) == "死亡"  # 有「本港」


def test_first_qualifying_keyword_unconditional_keyword_unaffected(monkeypatch):
    monkeypatch.setattr(KA, "KEYWORD_CONTEXT", {"死亡": ["港人", "本港", "本地", "香港"]})
    keywords = [("OpenAI", "openai")]
    assert KA._first_qualifying_keyword("openai 發布新模型", keywords) == "OpenAI"


def test_detect_keyword_matches_respects_context_requirement(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["死亡"])
    monkeypatch.setattr(KA, "KEYWORD_CONTEXT", {"死亡": ["港人", "本港", "本地", "香港"]})
    now = datetime.now(timezone.utc)
    articles = [
        _article(id="overseas", title="外國男子離奇死亡", date=now.isoformat()),
        _article(id="local", title="本港男子離奇死亡", date=now.isoformat()),
    ]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    assert {a["id"] for a in out} == {"local"}


def test_load_keywords_from_config_falls_back_to_default_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "CONFIG_PATH", tmp_path / "missing.txt")
    keywords, context = KA._load_keywords_from_config()
    assert keywords == KA._DEFAULT_KEYWORDS
    assert context == {}


def test_sync_watch_keywords_noop_when_vault_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "VAULT_PATH", tmp_path / "no_vault.md")
    config_path = tmp_path / "watch_keywords.txt"
    config_path.write_text("OldKeyword\n", encoding="utf-8")
    monkeypatch.setattr(KA, "CONFIG_PATH", config_path)
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["OldKeyword"])

    KA.sync_watch_keywords_from_vault()

    assert config_path.read_text(encoding="utf-8") == "OldKeyword\n"
    assert KA.WATCH_KEYWORDS == ["OldKeyword"]


def test_sync_watch_keywords_writes_config_and_updates_global(monkeypatch, tmp_path):
    vault_path = tmp_path / "vault.md"
    vault_path.write_text(
        "---\ntype: note\n---\n\n說明文字skip\n\n## 關鍵字清單\n\nOpenAI\nChatGPT\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "watch_keywords.txt"
    monkeypatch.setattr(KA, "VAULT_PATH", vault_path)
    monkeypatch.setattr(KA, "CONFIG_PATH", config_path)
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["Stale"])

    KA.sync_watch_keywords_from_vault()

    assert config_path.read_text(encoding="utf-8") == "OpenAI\nChatGPT\n"
    assert KA.WATCH_KEYWORDS == ["OpenAI", "ChatGPT"]


def test_sync_watch_keywords_keeps_existing_config_when_marker_missing(monkeypatch, tmp_path):
    # 冇 "## 關鍵字清單" 標題 —— 一定要拒絕同步，唔可以將上面自由寫嘅
    # 說明文字當成關鍵字（呢個曾經真係炒過一次：dry-run test 冧咗真
    # config file，跟手先加返呢條 regression test）。
    vault_path = tmp_path / "vault.md"
    vault_path.write_text("---\ntype: note\n---\n\n淨係得說明文字，冇關鍵字段落\n", encoding="utf-8")
    config_path = tmp_path / "watch_keywords.txt"
    config_path.write_text("Existing\n", encoding="utf-8")
    monkeypatch.setattr(KA, "VAULT_PATH", vault_path)
    monkeypatch.setattr(KA, "CONFIG_PATH", config_path)
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["Existing"])

    KA.sync_watch_keywords_from_vault()

    assert config_path.read_text(encoding="utf-8") == "Existing\n"
    assert KA.WATCH_KEYWORDS == ["Existing"]


def test_sync_watch_keywords_keeps_existing_config_when_marker_section_empty(monkeypatch, tmp_path):
    vault_path = tmp_path / "vault.md"
    vault_path.write_text("---\ntype: note\n---\n\n## 關鍵字清單\n\n# 淨係得comment，冇真關鍵字\n", encoding="utf-8")
    config_path = tmp_path / "watch_keywords.txt"
    config_path.write_text("Existing\n", encoding="utf-8")
    monkeypatch.setattr(KA, "VAULT_PATH", vault_path)
    monkeypatch.setattr(KA, "CONFIG_PATH", config_path)
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["Existing"])

    KA.sync_watch_keywords_from_vault()

    assert config_path.read_text(encoding="utf-8") == "Existing\n"
    assert KA.WATCH_KEYWORDS == ["Existing"]


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


def test_detect_keyword_matches_collapses_same_cluster_to_one_alert(monkeypatch):
    # 用戶反映：「交通意外」呢類廣泛 keyword，如果同一單意外俾好多 source
    # 報道，每篇都會獨立 alert，連環彈幾次。而家用 build.py 已經計好嘅
    # cluster_id（AI topic clustering）做 dedup，同一 cluster 淨係揀最新
    # 嗰篇。
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["交通意外"])
    now = datetime.now(timezone.utc)
    articles = [
        _article(id="a1", title="交通意外 A報道", source="A", cluster_id="c1",
                 date=(now - timedelta(minutes=10)).isoformat()),
        _article(id="a2", title="交通意外 B報道", source="B", cluster_id="c1",
                 date=(now - timedelta(minutes=5)).isoformat()),
        _article(id="a3", title="交通意外 C報道", source="C", cluster_id="c1",
                 date=now.isoformat()),
    ]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    assert len(out) == 1
    assert out[0]["id"] == "a3"  # 最新嗰篇


def test_detect_keyword_matches_keeps_different_clusters_separate(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["交通意外"])
    now = datetime.now(timezone.utc)
    articles = [
        _article(id="a1", title="交通意外一", cluster_id="c1", date=now.isoformat()),
        _article(id="a2", title="交通意外二", cluster_id="c2", date=now.isoformat()),
        _article(id="a3", title="交通意外三", cluster_id=None, date=now.isoformat()),  # 冇 cluster
    ]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    assert {a["id"] for a in out} == {"a1", "a2", "a3"}


def test_detect_keyword_matches_skips_already_alerted_cluster(monkeypatch):
    # 上一輪已經 alert 過 c1 呢個 cluster（用另一篇文），今次 c1 有新文章
    # 加入都唔應該再 alert。
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["交通意外"])
    now = datetime.now(timezone.utc)
    articles = [
        _article(id="a_new", title="交通意外 最新跟進", cluster_id="c1", date=now.isoformat()),
    ]
    out = KA.detect_keyword_matches(articles, set(), {"c1"}, now=now)
    assert out == []


def test_detect_keyword_matches_respects_cap(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["新聞"])
    monkeypatch.setattr(KA, "MAX_ALERTS_PER_BUILD", 2)
    now = datetime.now(timezone.utc)
    articles = [_article(id=f"a{i}", title=f"新聞{i}", date=now.isoformat()) for i in range(5)]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    assert len(out) == 2


def test_detect_keyword_matches_logs_when_cap_drops_matches(monkeypatch, capsys):
    # 2026-07-21 audit finding：之前完全冇 log 分辨「因為 cap 被截走」，
    # 而家應該喺 stdout 見到 dropped count。
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["新聞"])
    monkeypatch.setattr(KA, "MAX_ALERTS_PER_BUILD", 2)
    now = datetime.now(timezone.utc)
    articles = [_article(id=f"a{i}", title=f"新聞{i}", date=now.isoformat()) for i in range(5)]
    KA.detect_keyword_matches(articles, set(), now=now)
    out = capsys.readouterr().out
    assert "3 match" in out and "dropped" in out


def test_detect_keyword_matches_empty_watchlist_returns_nothing(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", [])
    now = datetime.now(timezone.utc)
    out = KA.detect_keyword_matches([_article(title="樓市")], set(), now=now)
    assert out == []


def test_format_alert_text_includes_keyword_summary_and_meta():
    article = _article(title="樓市成交急升", summary="・重點一\n・重點二", url="https://example.com/hit")
    article["_matched_keyword"] = "樓市"
    text = KA._format_alert_text(article)
    assert text.startswith("🔔 <b>關鍵字提醒</b>：樓市\n樓市成交急升")
    assert "・重點一" in text
    assert "新聞 · RTHK 本地" in text
    assert 'href="https://example.com/hit"' in text
    assert text.rstrip().endswith("</a>")


def test_format_alert_text_includes_hkt_publish_time():
    article = _article(
        title="樓市成交急升",
        date="2026-07-21T06:32:00+00:00",  # UTC，對應 HKT 14:32
    )
    article["_matched_keyword"] = "樓市"
    text = KA._format_alert_text(article)
    assert "新聞 · RTHK 本地 · 14:32" in text


def test_format_hkt_time_returns_empty_on_bad_date():
    assert KA._format_hkt_time({"date": "not-a-date"}) == ""
    assert KA._format_hkt_time({}) == ""


def test_format_alert_text_omits_link_when_no_url():
    article = _article(title="樓市成交急升", url="")
    article["_matched_keyword"] = "樓市"
    text = KA._format_alert_text(article)
    assert "<a href=" not in text


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

    daytime = datetime.now(timezone.utc).replace(hour=12)  # HKT 20:00, outside quiet hours
    # 而家 send_keyword_alerts 嘅 now 會傳埋落 detect_keyword_matches 做
    # freshness cutoff（之前冇連埋，quiet-hours check 同 freshness check
    # 各自睇唔同嘅 "now"）——article date 一定要同 daytime 一致，否則
    # 如果真實 wall-clock hour 細過 12，人為 cutoff 會將呢篇文當「太舊」。
    articles = [_article(id="hit1", title="樓市成交急升", date=daytime.isoformat())]
    asyncio.run(KA.send_keyword_alerts(articles, now=daytime))
    assert calls["n"] == 1

    # 第二次 build 見到同一篇文（未過 FRESHNESS window）——唔應該再推
    asyncio.run(KA.send_keyword_alerts(articles, now=daytime))
    assert calls["n"] == 1


def test_send_keyword_alerts_tracks_cluster_across_builds(monkeypatch, tmp_path):
    # 第一次 build：c1 呢個 cluster 得一篇文，alert 咗。第二次 build：
    # 同一個 cluster 有第二篇新文章加入（唔同 id，同一 cluster_id）——
    # 唔應該再 alert，因為個 cluster 已經記錄咗喺 alerted_clusters。
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["交通意外"])
    monkeypatch.setattr(KA, "TELEGRAM_BOT_TOKEN", "token")
    state_path = tmp_path / "keyword_alerts.json"
    monkeypatch.setattr(KA, "STATE_PATH", state_path)
    calls = {"n": 0}

    async def fake_send(session, text, photo_url=""):
        calls["n"] += 1
        return 200
    monkeypatch.setattr(KA, "_send_telegram", fake_send)

    daytime = datetime.now(timezone.utc).replace(hour=12)
    first = [_article(id="a1", title="交通意外首報", cluster_id="c1", date=daytime.isoformat())]
    asyncio.run(KA.send_keyword_alerts(first, now=daytime))
    assert calls["n"] == 1

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["alerted_clusters"].get("c1")

    second = [_article(id="a2", title="交通意外跟進", cluster_id="c1", date=daytime.isoformat())]
    asyncio.run(KA.send_keyword_alerts(second, now=daytime))
    assert calls["n"] == 1  # 冇再送


def test_send_keyword_alerts_skips_during_quiet_hours(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["樓市"])
    monkeypatch.setattr(KA, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(KA, "STATE_PATH", tmp_path / "keyword_alerts.json")

    async def fail_send(*a, **kw):
        raise AssertionError("should not call telegram during quiet hours")
    monkeypatch.setattr(KA, "_send_telegram", fail_send)

    night = datetime.now(timezone.utc).replace(hour=18)  # HKT 02:00
    articles = [_article(id="hit1", title="樓市成交急升")]
    asyncio.run(KA.send_keyword_alerts(articles, now=night))

    # 未 mark alerted——quiet hours 完咗之後如果仲喺 freshness window 應該照送
    state = json.loads((tmp_path / "keyword_alerts.json").read_text(encoding="utf-8"))
    assert state["alerted"] == {}


def test_detect_keyword_matches_includes_trending_keyword_hits(monkeypatch):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["OpenAI"])
    monkeypatch.setattr(KA, "load_trending_keywords", lambda: ["陳嘉信"])
    now = datetime.now(timezone.utc)
    articles = [
        _article(id="trend", title="陳嘉信案上訴得直", date=now.isoformat()),
        _article(id="miss", title="天氣預告", date=now.isoformat()),
    ]
    out = KA.detect_keyword_matches(articles, set(), now=now)
    assert {a["id"] for a in out} == {"trend"}
    assert out[0]["_matched_keyword"] == "陳嘉信"


def test_detect_keyword_matches_works_with_only_trending_keywords(monkeypatch):
    # WATCH_KEYWORDS 淨係得 curated 清單先算「冇關鍵字」——連 Google
    # Trends 熱門字都冇嘅話先真.應該完全唔 match。
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(KA, "load_trending_keywords", lambda: ["陳嘉信"])
    now = datetime.now(timezone.utc)
    out = KA.detect_keyword_matches(
        [_article(id="trend", title="陳嘉信案上訴得直", date=now.isoformat())], set(), now=now
    )
    assert {a["id"] for a in out} == {"trend"}


def test_detect_keyword_matches_dedupes_keyword_appearing_in_both_lists(monkeypatch):
    # 同一個字如果啱啱好 WATCH_KEYWORDS 同 trending 都有，唔應該行兩次
    # match（結果一樣，純粹確保 dict.fromkeys 冇整壞正常 match）。
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", ["樓市"])
    monkeypatch.setattr(KA, "load_trending_keywords", lambda: ["樓市"])
    now = datetime.now(timezone.utc)
    out = KA.detect_keyword_matches(
        [_article(id="hit", title="樓市成交急升", date=now.isoformat())], set(), now=now
    )
    assert {a["id"] for a in out} == {"hit"}


def test_send_keyword_alerts_sends_when_only_trending_keywords_present(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "WATCH_KEYWORDS", [])
    monkeypatch.setattr(KA, "load_trending_keywords", lambda: ["陳嘉信"])
    monkeypatch.setattr(KA, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(KA, "STATE_PATH", tmp_path / "keyword_alerts.json")
    calls = {"n": 0}

    async def fake_send(session, text, photo_url=""):
        calls["n"] += 1
        return 200
    monkeypatch.setattr(KA, "_send_telegram", fake_send)

    daytime = datetime.now(timezone.utc).replace(hour=12)
    articles = [_article(id="trend", title="陳嘉信案上訴得直", date=daytime.isoformat())]
    asyncio.run(KA.send_keyword_alerts(articles, now=daytime))
    assert calls["n"] == 1


def test_in_quiet_hours_boundaries():
    assert KA._in_quiet_hours(datetime(2026, 1, 1, 16, 0, tzinfo=timezone.utc))   # HKT 00:00
    assert KA._in_quiet_hours(datetime(2026, 1, 1, 22, 59, tzinfo=timezone.utc))  # HKT 06:59
    assert not KA._in_quiet_hours(datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc))  # HKT 07:00
    assert not KA._in_quiet_hours(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))  # HKT 20:00
