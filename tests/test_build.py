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

    monkeypatch.setattr(build, "DATA_DIR", data_dir)
    monkeypatch.setattr(build, "CONTENT_DIR", content_dir)
    monkeypatch.setattr(build, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(build, "scrape_all", fake_scrape_all)
    monkeypatch.setattr(build, "analyse_all", fake_analyse_all)

    asyncio.run(build.main())

    articles = json.loads((data_dir / "articles.json").read_text(encoding="utf-8"))
    content = json.loads((content_dir / "dryrun.json").read_text(encoding="utf-8"))
    feed = (data_dir / "feed.xml").read_text(encoding="utf-8")

    assert articles["articles"][0]["id"] == "dryrun"
    assert articles["sources"]["Test source"]["effective_count"] == 1
    assert content["version"] == build.CONTENT_SCHEMA_VERSION
    assert content["quality"]["fallback"] == "none"
    assert "<guid isPermaLink=\"false\">dryrun</guid>" in feed
