import json
import asyncio
from datetime import datetime, timedelta, timezone

import build


def _article(article_id: str, content=None):
    return {
        "id": article_id,
        "title": "Test article",
        "url": "https://example.com/test",
        "date": datetime.now(timezone.utc).isoformat(),
        "source": "Test source",
        "category": "Test",
        "summary": "summary",
        "content": content,
    }


def _topic_article(article_id: str, topic: str, now: datetime, hours_ago: int, source: str = "Test source"):
    article = _article(article_id, content="<p>full text</p>")
    article.update({
        "date": (now - timedelta(hours=hours_ago)).isoformat(),
        "source": source,
        "topic": topic,
        "score": 8,
    })
    return article


def test_build_trending_topics_groups_recent_articles_only():
    now = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    articles = [
        _topic_article("t1", "特朗普關稅", now, 1, source="A"),
        _topic_article("t2", "特朗普關稅", now, 2, source="B"),
        _topic_article("old", "特朗普關稅", now, 5, source="C"),
        _topic_article("solo", "單篇新聞", now, 1, source="D"),
    ]

    topics = build.build_trending_topics(articles, now=now, hours=4, limit=10)

    assert len(topics) == 1
    assert topics[0]["topic"] == "特朗普關稅"
    assert topics[0]["count"] == 2
    assert topics[0]["source_count"] == 2
    assert topics[0]["article_ids"] == ["t1", "t2"]


def test_topic_grouping_uses_exact_ai_topic():
    now = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    articles = [
        _topic_article("t1", "  伊朗局勢  ", now, 1, source="A"),
        _topic_article("t2", "伊朗局勢", now, 2, source="B"),
        _topic_article("t3", "伊朗局勢 ", now, 3, source="C"),
    ]

    topics = build.build_trending_topics(articles, now=now, hours=4, limit=10)

    assert topics[0]["topic"] == "伊朗局勢"
    assert topics[0]["count"] == 3


def test_topic_grouping_canonicalises_known_aliases():
    now = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    articles = [
        _topic_article("t1", "中東動向", now, 1, source="A"),
        _topic_article("t2", "國際焦點", now, 2, source="B"),
    ]
    articles[0]["title"] = "美伊局勢升溫"
    articles[0]["summary"] = "霍爾木茲航道受關注"
    articles[1]["title"] = "以色列回應美伊緊張"
    articles[1]["summary"] = "伊朗與以色列互相施壓"

    topics = build.build_trending_topics(articles, now=now, hours=4, limit=10)

    assert len(topics) == 1
    assert topics[0]["topic"] == "伊朗局勢"
    assert topics[0]["count"] == 2


def test_cluster_articles_clears_stale_cluster_fields():
    article = _article("solo", content="<p>full text</p>")
    article.update({
        "topic": "single",
        "cluster_id": "deadbeef",
        "cluster_size": 9,
    })

    clustered = build.cluster_articles([article])

    assert "cluster_id" not in clustered[0]
    assert "cluster_size" not in clustered[0]


def test_annotate_ai_features_marks_uncertainty_flags():
    articles = [
        {
            "id": "a",
            "title": "消息指大型事故仍未證實",
            "summary": "據悉事件仍有變數",
            "score": 8,
            "content_quality": {"score": 1, "fallback": "minimal"},
        },
        {
            "id": "b",
            "title": "多來源確認消息",
            "summary": "已有多間傳媒報道",
            "score": 6,
            "cluster_id": "c1",
            "source": "A",
            "content_quality": {"score": 3, "fallback": "scraped"},
        },
        {
            "id": "c",
            "title": "多來源確認消息",
            "summary": "另一來源跟進",
            "score": 6,
            "cluster_id": "c1",
            "source": "B",
            "content_quality": {"score": 3, "fallback": "scraped"},
        },
    ]
    out = build.annotate_ai_features(articles)
    assert out[0]["uncertainty_flags"][:3] == ["消息未證實", "仍有變數", "全文不足"]
    assert "uncertainty_flags" not in out[1]


def test_detect_duplicates_marks_near_duplicate_titles():
    a = _article("aaa")
    a.update({"title": "港股今日收市升300點，恆指突破兩萬五", "score": 7, "date": "2026-04-21T12:00:00+00:00"})
    b = _article("bbb")
    b.update({"title": "港股今日收市升300點，恆指突破兩萬五!", "score": 9, "date": "2026-04-21T12:10:00+00:00"})
    c = _article("ccc")
    c.update({"title": "天文台下午發出黃色暴雨警告", "score": 6})

    result = build.detect_duplicates([a, b, c])

    canonical = next(x for x in result if x["id"] in {"aaa", "bbb"} and "duplicate_of" not in x)
    duped = next(x for x in result if x["id"] in {"aaa", "bbb"} and x is not canonical)

    assert canonical["id"] == "bbb"  # higher score wins
    assert canonical["duplicate_count"] == 2
    assert duped["duplicate_of"] == "bbb"
    assert "duplicate_of" not in next(x for x in result if x["id"] == "ccc")


def test_detect_duplicates_leaves_distinct_titles_untouched():
    items = [
        {**_article("x"), "title": "A 局勢 最新發展"},
        {**_article("y"), "title": "完全無關的娛樂新聞"},
    ]
    result = build.detect_duplicates(items)
    for art in result:
        assert "duplicate_of" not in art
        assert "duplicate_count" not in art


def test_save_json_writes_trending_topics(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    build.save_json([
        _topic_article("t1", "公共交通事故", now, 1, source="A"),
        _topic_article("t2", "公共交通事故", now, 2, source="B"),
    ], {})

    payload = json.loads((data_dir / "articles.json").read_text(encoding="utf-8"))
    assert payload["trending_topics"][0]["topic"] == "公共交通事故"


def test_save_json_writes_minimal_sidecar_when_content_missing(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    articles = [_article("minimal", content=None)]
    build.save_json(articles, {})

    saved = json.loads((content_dir / "minimal.json").read_text(encoding="utf-8"))
    payload = json.loads((data_dir / "articles.json").read_text(encoding="utf-8"))
    assert saved["version"] == build.CONTENT_SCHEMA_VERSION
    assert saved["content"]
    assert saved["quality"]["fallback"] == "minimal"
    assert payload["articles"][0]["content_quality"]["fallback"] == "minimal"


def test_save_json_reuses_existing_content_when_current_scrape_has_none(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"
    content_dir.mkdir(parents=True)
    old_content = "<p>old full text</p>"
    (content_dir / "abc123.json").write_text(
        json.dumps({"content": old_content}),
        encoding="utf-8",
    )

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    articles = [_article("abc123", content=None)]
    build.save_json(articles, {})

    saved = json.loads((content_dir / "abc123.json").read_text(encoding="utf-8"))
    assert saved["version"] == build.CONTENT_SCHEMA_VERSION
    assert saved["content"] == old_content
    assert saved["quality"]["fallback"] == "reused"
    assert saved["quality"]["chars"] == 13
    assert articles[0]["content"] == old_content


def test_save_json_removes_duplicate_leading_thumbnail(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    content = '<html><body><img src="https://example.com/a.jpg"><p>body text</p></body></html>'
    articles = [_article("dupimg", content=content)]
    articles[0]["thumbnail"] = "https://example.com/a.jpg"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "dupimg.json").read_text(encoding="utf-8"))
    assert "<img" not in saved["content"]
    assert saved["quality"]["images"] == 0


def test_save_json_removes_duplicate_thumbnail_figure_wrapper(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    content = (
        '<html><body>'
        '<figure><img src="https://example.com/a.jpg">'
        '<figcaption>caption text</figcaption></figure>'
        '<p>body text</p></body></html>'
    )
    articles = [_article("dupfig", content=content)]
    articles[0]["thumbnail"] = "https://example.com/a.jpg"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "dupfig.json").read_text(encoding="utf-8"))
    assert "<img" not in saved["content"]
    assert "<figure" not in saved["content"]
    assert "caption text" not in saved["content"]


def test_save_json_dedupes_thumbnail_with_different_size_variant(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    content = (
        '<html><body>'
        '<img src="https://image.cdn.example/f/1024p0/0x0/abc/2026/New_Project_456.jpg">'
        '<p>body text</p></body></html>'
    )
    articles = [_article("dupvariant", content=content)]
    articles[0]["thumbnail"] = "https://image.cdn.example/f/1200p0/0x0/xyz/2026/New_Project_456.jpg"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "dupvariant.json").read_text(encoding="utf-8"))
    assert "<img" not in saved["content"]


def test_save_json_keeps_distinct_images_with_different_filenames(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    content = (
        '<html><body>'
        '<img src="https://example.com/genuinely_different.jpg">'
        '<p>body text</p></body></html>'
    )
    articles = [_article("nodupe", content=content)]
    articles[0]["thumbnail"] = "https://example.com/the_thumbnail.jpg"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "nodupe.json").read_text(encoding="utf-8"))
    assert '<img src="https://example.com/genuinely_different.jpg"' in saved["content"]


def test_save_json_dedupes_short_filename_variant(tmp_path, monkeypatch):
    # 星島頭條 / hkhl.hk style: same source image at two CDN variants with a
    # short filename like "0_2.png". The earlier 8-char minimum let these slip
    # through and rendered the hero twice on the article page.
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    content = (
        '<html><body>'
        '<img src="https://image.hkhl.hk/f/1024p0/0x0/abc/2026-05/0_2.png">'
        '<p>body</p></body></html>'
    )
    articles = [_article("hkhlshort", content=content)]
    articles[0]["thumbnail"] = "https://image.hkhl.hk/f/1200p0/0x0/xyz/2026-05/0_2.png"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "hkhlshort.json").read_text(encoding="utf-8"))
    assert "<img" not in saved["content"]


def test_save_json_does_not_dedupe_on_bare_numeric_filename(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    # "1.jpg" appears across many unrelated articles; never treat it as a
    # signal of the-same-image.
    content = (
        '<html><body>'
        '<img src="https://siteA.example/path/1.jpg">'
        '<p>body</p></body></html>'
    )
    articles = [_article("bareindex", content=content)]
    articles[0]["thumbnail"] = "https://siteB.example/other/1.jpg"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "bareindex.json").read_text(encoding="utf-8"))
    assert "<img" in saved["content"]


def test_save_json_does_not_dedupe_on_generic_filenames(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    content = (
        '<html><body>'
        '<img src="https://siteA.example/path/image.jpg">'
        '<p>body</p></body></html>'
    )
    articles = [_article("generic", content=content)]
    articles[0]["thumbnail"] = "https://siteB.example/other/image.jpg"

    build.save_json(articles, {})

    saved = json.loads((content_dir / "generic.json").read_text(encoding="utf-8"))
    assert "<img" in saved["content"]


def test_save_json_prunes_only_articles_missing_from_metadata(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"
    content_dir.mkdir(parents=True)
    (content_dir / "active.json").write_text(
        json.dumps({"content": "<p>keep</p>"}),
        encoding="utf-8",
    )
    (content_dir / "stale.json").write_text(
        json.dumps({"content": "<p>drop</p>"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)

    build.save_json([_article("active", content=None)], {})

    assert (content_dir / "active.json").exists()
    assert not (content_dir / "stale.json").exists()


def test_merge_missing_sources_respects_article_max_age(monkeypatch):
    now = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(build, "ARTICLE_MAX_AGE_HOURS", 30)
    monkeypatch.setattr(build, "datetime", type("FixedDateTime", (datetime,), {
        "now": classmethod(lambda cls, tz=None: now if tz else now.replace(tzinfo=None)),
    }))

    recent = _topic_article("recent", "fallback", now, 29, source="Missing source")
    old = _topic_article("old", "fallback", now, 31, source="Missing source")

    merged = build._merge_missing_sources([], [recent, old], {"Missing source": {"count": 0}})

    assert [a["id"] for a in merged] == ["recent"]


def test_merge_missing_sources_populates_stats_when_source_stats_starts_empty(monkeypatch):
    # 2026-07-21 audit finding: fetch_all() 撞到 outer 150s timeout 時
    # source_stats 會直接變 {}，之前個 `if src in source_stats` gate 令
    # 呢個情況下所有 restored source 都冧唔到落 source_stats，
    # articles_index.json 嘅 "sources" 就會係空 object。
    now = datetime(2026, 4, 21, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(build, "ARTICLE_MAX_AGE_HOURS", 30)
    monkeypatch.setattr(build, "datetime", type("FixedDateTime", (datetime,), {
        "now": classmethod(lambda cls, tz=None: now if tz else now.replace(tzinfo=None)),
    }))

    recent = _topic_article("recent", "fallback", now, 5, source="Missing source")
    source_stats = {}  # 模擬 fetch_all() outer timeout 之後嘅狀態

    merged = build._merge_missing_sources([], [recent], source_stats)

    assert [a["id"] for a in merged] == ["recent"]
    assert "Missing source" in source_stats
    assert source_stats["Missing source"]["restored"] == 1
    assert source_stats["Missing source"]["effective_count"] == 1


def test_build_upcoming_merges_same_event_across_sources():
    today = datetime(2026, 4, 27).date()
    arts = [
        {"id": "a1", "title": "T1", "source": "明報",
         "upcoming_events": [{"date": "2026-05-01", "title": "勞動節活動"}]},
        {"id": "a2", "title": "T2", "source": "RTHK",
         "upcoming_events": [{"date": "2026-05-01", "title": "勞動節活動"}]},
    ]
    out = build.build_upcoming(arts, today=today)
    assert len(out["events"]) == 1
    assert {a["id"] for a in out["events"][0]["articles"]} == {"a1", "a2"}


def test_build_upcoming_filters_past_and_too_distant():
    today = datetime(2026, 4, 27).date()
    arts = [
        {"id": "a1", "title": "T1", "source": "X",
         "upcoming_events": [
             {"date": "2025-01-01", "title": "過去"},
             {"date": "2099-01-01", "title": "太遠未來"},
             {"date": "2026-05-15", "title": "合理範圍"},
         ]},
    ]
    out = build.build_upcoming(arts, today=today)
    assert [e["title"] for e in out["events"]] == ["合理範圍"]


def test_build_upcoming_skips_duplicates_and_invalid_dates():
    today = datetime(2026, 4, 27).date()
    arts = [
        {"id": "a1", "title": "T1", "source": "X", "duplicate_of": "real",
         "upcoming_events": [{"date": "2026-05-01", "title": "活動"}]},
        {"id": "a2", "title": "T2", "source": "X",
         "upcoming_events": [
             {"date": "2026/05/02", "title": "格式錯"},
             {"title": "缺日期"},
         ]},
    ]
    out = build.build_upcoming(arts, today=today)
    assert out["events"] == []


def test_main_dry_run_writes_expected_artifacts(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    content_dir = data_dir / "content"

    async def fake_fetch_all():
        return ([_article("dryrun", content=None)], {"Test source": {"category": "Test", "count": 1}})

    async def fake_scrape_all(articles):
        articles[0]["content"] = "<p>fresh full text</p><img src=\"https://example.com/a.jpg\">"
        articles[0]["content_quality"] = {
            "score": 1,
            "chars": 15,
            "images": 1,
            "source": "Test source",
            "fallback": "none",
        }
        return articles

    async def fake_analyse_all(articles):
        articles[0].update({
            "summary": "summary",
            "score": 5,
            "tags": ["tag"],
            "sentiment": "neutral",
            "topic": "dry",
        })
        return articles

    embed_call = {}

    async def fake_compute_embeddings(articles, data_dir=None):
        embed_call["data_dir"] = data_dir

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(build, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(build, "scrape_all", fake_scrape_all)
    monkeypatch.setattr(build, "analyse_all", fake_analyse_all)
    monkeypatch.setattr(build, "compute_embeddings", fake_compute_embeddings)
    # main() 會行埋 panel digest / entity digest / breaking alert，呢啲 module
    # 有自己嘅絕對輸出路徑（唔跟 build.DATA_DIR）——唔 patch 嘅話 dry run 會
    # 直接改寫真嘅 docs/data/*.json（試過 wipe 咗 panel_digests.json）。
    import src.panel_digest as panel_digest
    import src.entity_digest as entity_digest
    import src.breaking_alert as breaking_alert
    import src.daily_brief as daily_brief
    import src.keyword_alert as keyword_alert
    import src.translate_content as translate_content
    monkeypatch.setattr(panel_digest, "CACHE_PATH", data_dir / "panel_digests.json")
    monkeypatch.setattr(entity_digest, "OUTPUT_PATH", data_dir / "entities.json")
    monkeypatch.setattr(breaking_alert, "STATE_PATH", data_dir / "breaking_alerts.json")
    monkeypatch.setattr(daily_brief, "OUTPUT_PATH", data_dir / "daily_brief.json")
    monkeypatch.setattr(keyword_alert, "STATE_PATH", data_dir / "keyword_alerts.json")
    # sync_watch_keywords_from_vault() reads/writes real machine-specific
    # paths (the Obsidian vault + config/watch_keywords.txt) — same class of
    # bug as the paths above, redirect both so a dry run can't touch them.
    monkeypatch.setattr(keyword_alert, "VAULT_PATH", tmp_path / "no_such_vault_note.md")
    monkeypatch.setattr(keyword_alert, "CONFIG_PATH", data_dir / "watch_keywords.txt")
    # translate_content.CACHE_PATH is the same class of bug as the paths
    # above and was previously missed (2026-07-21 audit finding).
    monkeypatch.setattr(translate_content, "CACHE_PATH", data_dir / "translated_content.json")
    # A real .env with a real TELEGRAM_BOT_TOKEN exists on this machine —
    # `from src.breaking_alert import TELEGRAM_BOT_TOKEN` in keyword_alert.py
    # is a separate binding (not a live reference), so both modules must be
    # patched independently or a fixture with a real keyword/cluster match
    # would POST a real message to the production Telegram channel
    # (2026-07-21 audit finding).
    monkeypatch.setattr(breaking_alert, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(keyword_alert, "TELEGRAM_BOT_TOKEN", "")
    # source_health has the same module-level absolute STATE_PATH + its own
    # TELEGRAM_BOT_TOKEN binding as the modules above — sandbox both or a dry
    # run rewrites the real docs/data/source_health.json and can POST a
    # "來源斷更" alert to the production channel.
    import src.source_health as source_health
    monkeypatch.setattr(source_health, "STATE_PATH", data_dir / "source_health.json")
    monkeypatch.setattr(source_health, "TELEGRAM_BOT_TOKEN", "")

    # daily_brief 會 call MiniMax + 推 Telegram（本機 .env 有齊 key）——dry run
    # 一定要 stub 走，唔係測試會嘥錢兼真係出 message。
    async def fake_daily_brief(_articles):
        return None

    monkeypatch.setattr(build, "generate_daily_brief", fake_daily_brief)

    # sync_trending_keywords() 會真.打 Google Trends RSS endpoint——dry run
    # 唔應該喺跑 test 嗰陣觸發真實網絡請求（同上面 daily_brief/telegram
    # 嘅道理一樣）。
    async def fake_sync_trending_keywords():
        return []
    monkeypatch.setattr(build, "sync_trending_keywords", fake_sync_trending_keywords)

    asyncio.run(build.main())

    articles = json.loads((data_dir / "articles.json").read_text(encoding="utf-8"))
    content = json.loads((content_dir / "dryrun.json").read_text(encoding="utf-8"))
    feed = (data_dir / "feed.xml").read_text(encoding="utf-8")

    assert articles["articles"][0]["id"] == "dryrun"
    assert articles["sources"]["Test source"]["effective_count"] == 1
    assert content["version"] == build.CONTENT_SCHEMA_VERSION
    assert content["quality"]["fallback"] == "none"
    assert "<guid isPermaLink=\"false\">dryrun</guid>" in feed
    assert embed_call["data_dir"] == data_dir
    assert (data_dir / "build_status.json").exists()
    build_status = json.loads((data_dir / "build_status.json").read_text(encoding="utf-8"))
    steps = build_status["steps"]
    # 2026-07-21 audit finding: fetch/retranslate/scrape/analyse 之前完全
    # 冇 mark_step，build_status.json 淨係 track 到 translate 之後嘅 stage。
    for stage in ("fetch", "retranslate", "scrape", "analyse", "translate"):
        assert stage in steps, f"{stage} missing from build_status.json"
        assert steps[stage]["ok"] is True
    # 2026-07-22：慢速通道停用（SLOW_KEYWORD_ALERTS_ENABLED = False），
    # send_keyword_alerts() 唔應該再被 call，呢個 stage 唔應該出現。
    assert "keyword_alert" not in steps


def test_timeout_status_records_build_failure(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "_build_status", {"panel_digest": {"ok": True}})

    build._write_timeout_build_status(saved=True)

    payload = json.loads((data_dir / "build_status.json").read_text(encoding="utf-8"))
    assert payload["steps"]["panel_digest"]["ok"] is True
    assert payload["steps"]["build"]["ok"] is False
    assert "after core save" in payload["steps"]["build"]["error"]
