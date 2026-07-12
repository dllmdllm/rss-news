from datetime import datetime, timedelta

from src.daily_brief import (
    HKT,
    _parse_brief,
    select_top_articles,
    should_generate,
)


def test_should_generate_once_per_day_after_6am():
    morning = datetime(2026, 7, 13, 7, 0, tzinfo=HKT)
    assert should_generate({}, morning)
    assert should_generate({"date": "2026-07-12"}, morning)
    assert not should_generate({"date": "2026-07-13"}, morning)
    # 06:00 之前唔生成
    early = datetime(2026, 7, 13, 5, 59, tzinfo=HKT)
    assert not should_generate({}, early)


def test_select_top_articles_dedupes_clusters_and_respects_cutoff():
    now = datetime(2026, 7, 13, 7, 0, tzinfo=HKT)
    fresh = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=30)).isoformat()
    articles = [
        {"id": "a", "date": fresh, "score": 9, "cluster_id": "c1"},
        {"id": "b", "date": fresh, "score": 8, "cluster_id": "c1"},   # same cluster → skipped
        {"id": "c", "date": fresh, "score": 7, "cluster_id": "c2"},
        {"id": "d", "date": stale, "score": 10, "cluster_id": "c3"},  # too old → skipped
        {"id": "e", "date": fresh, "score": 6, "cluster_id": None,
         "duplicate_of": "a"},                                        # duplicate → skipped
    ]
    picked = [a["id"] for a in select_top_articles(articles, now)]
    assert picked == ["a", "c"]


def test_parse_brief_strips_fences_and_validates_ids():
    raw = """```json
    {"title": "今日焦點", "text": "早報正文內容。",
     "highlights": [
       {"point": "重點一", "id": "a"},
       {"point": "重點二", "id": "unknown"}
     ]}
    ```"""
    brief = _parse_brief(raw, valid_ids={"a"})
    assert brief["title"] == "今日焦點"
    assert brief["highlights"][0] == {"point": "重點一", "id": "a"}
    # 唔認識嘅 id 會被清空，唔會產生死 link
    assert brief["highlights"][1] == {"point": "重點二", "id": ""}


def test_parse_brief_rejects_empty_body():
    assert _parse_brief('{"title": "x", "text": "", "highlights": []}', set()) is None
    assert _parse_brief("not json at all", set()) is None
