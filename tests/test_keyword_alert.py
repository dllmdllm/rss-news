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


def test_load_keywords_from_config_falls_back_to_default_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(KA, "CONFIG_PATH", tmp_path / "missing.txt")
    assert KA._load_keywords_from_config() == KA._DEFAULT_KEYWORDS


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
    article = _article(title="樓市成交急升", summary="・重點一\n・重點二", url="https://example.com/hit")
    article["_matched_keyword"] = "樓市"
    text = KA._format_alert_text(article)
    assert text.startswith("🔔 <b>關鍵字提醒</b>：樓市\n樓市成交急升")
    assert "・重點一" in text
    assert "新聞 · RTHK 本地" in text
    assert 'href="https://example.com/hit"' in text
    assert text.rstrip().endswith("</a>")


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

    articles = [_article(id="hit1", title="樓市成交急升")]
    asyncio.run(KA.send_keyword_alerts(articles))
    assert calls["n"] == 1

    # 第二次 build 見到同一篇文（未過 FRESHNESS window）——唔應該再推
    asyncio.run(KA.send_keyword_alerts(articles))
    assert calls["n"] == 1
