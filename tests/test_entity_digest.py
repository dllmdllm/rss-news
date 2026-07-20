from datetime import datetime, timedelta, timezone

from src import entity_digest as ED


def _article(**overrides):
    base = {
        "id": "a1",
        "date": datetime.now(timezone.utc).isoformat(),
        "duplicate_of": None,
        "entities": {"people": [], "companies": [], "places": []},
    }
    base.update(overrides)
    return base


def test_aggregate_entities_requires_min_articles(monkeypatch):
    monkeypatch.setattr(ED, "ENTITY_MIN_ARTICLES", 2)
    articles = [
        _article(id="a1", entities={"people": ["馬斯克"], "companies": [], "places": []}),
        _article(id="a2", entities={"people": ["馬斯克"], "companies": [], "places": []}),
        _article(id="a3", entities={"people": ["王小明"], "companies": [], "places": []}),
    ]
    out = ED.aggregate_entities(articles)
    names = {e["name"]: e["count"] for e in out}
    assert names == {"馬斯克": 2}


def test_aggregate_entities_merges_simplified_and_traditional_variants(monkeypatch):
    # 2026-07-21 audit finding：AI extract 嘅 entity name 有時會混雜簡體字，
    # 同一個真實實體因為簡繁唔同會拆散做幾個 entry，各自 count 被稀釋。
    # 而家全部轉做 zh-hk 先做 key，簡繁變體應該合併埋一齊。
    monkeypatch.setattr(ED, "ENTITY_MIN_ARTICLES", 2)
    articles = [
        _article(id="a1", entities={"companies": ["谷歌"], "people": [], "places": []}),
        _article(id="a2", entities={"companies": ["谷歌"], "people": [], "places": []}),
        # 簡體「地区」風格嘅字混入 traditional 文本嘅情況，用簡體「腾讯」做例子
        _article(id="a3", entities={"companies": ["腾讯"], "people": [], "places": []}),
        _article(id="a4", entities={"companies": ["騰訊"], "people": [], "places": []}),
    ]
    out = ED.aggregate_entities(articles)
    companies = {e["name"]: e["count"] for e in out}
    assert companies.get("谷歌") == 2
    # 「腾讯」（簡）同「騰訊」（繁）應該 merge 做同一個 entry，count 加埋做 2
    assert len([n for n in companies if "腾讯" in n or "騰訊" in n]) == 1
    merged_name = next(n for n in companies if "腾讯" in n or "騰訊" in n)
    assert companies[merged_name] == 2


def test_aggregate_entities_skips_duplicates_and_old_and_short_names(monkeypatch):
    monkeypatch.setattr(ED, "ENTITY_MIN_ARTICLES", 1)
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(hours=ED.ENTITY_WINDOW_HOURS + 1)).isoformat()
    articles = [
        _article(id="dup", duplicate_of="a1", entities={"people": ["王大文"], "companies": [], "places": []}),
        _article(id="old", date=stale, entities={"people": ["陳小明"], "companies": [], "places": []}),
        _article(id="short", entities={"people": ["A"], "companies": [], "places": []}),  # len < 2, skipped
        _article(id="ok", entities={"people": ["李四"], "companies": [], "places": []}),
    ]
    out = ED.aggregate_entities(articles)
    names = {e["name"] for e in out}
    assert names == {"李四"}


def test_aggregate_entities_caps_per_type(monkeypatch):
    monkeypatch.setattr(ED, "ENTITY_MIN_ARTICLES", 1)
    monkeypatch.setattr(ED, "ENTITY_MAX_PER_TYPE", 2)
    articles = [
        _article(id=f"a{i}", entities={"people": [f"人物{i}"], "companies": [], "places": []})
        for i in range(5)
    ]
    out = ED.aggregate_entities(articles)
    assert len(out) == 2
