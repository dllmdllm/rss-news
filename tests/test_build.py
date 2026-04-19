import json
import asyncio
from datetime import datetime, timezone

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
