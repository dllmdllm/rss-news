from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# ── 別名合併（2026-07-25）──

def test_canonical_entity_merges_known_aliases():
    from src.entity_digest import canonical_entity
    assert canonical_entity("companies", "香港天文台") == "天文台"
    assert canonical_entity("companies", "港鐵公司") == "港鐵"
    assert canonical_entity("companies", "無綫集團") == "TVB"
    assert canonical_entity("companies", "電視廣播有限公司") == "TVB"


def test_canonical_entity_still_normalises_simplified():
    # 簡繁 normalize 要保留——AI 偶爾出簡體，唔轉就同繁體版拆成兩個實體。
    from src.entity_digest import canonical_entity
    assert canonical_entity("people", "李慧琼") == "李慧瓊"


def test_canonical_entity_is_type_scoped():
    # 「香港海關」係 companies、「香港」係 places——alias 一定要連 type 一齊
    # 比對，唔係會跨類撞名。
    from src.entity_digest import canonical_entity
    assert canonical_entity("companies", "海關") == "香港海關"
    assert canonical_entity("places", "海關") == "海關"


def test_canonical_entity_does_not_merge_lookalikes():
    # 呢個係唔可以用 substring 自動合併嘅原因——全部都係真數據入面出現過。
    from src.entity_digest import canonical_entity
    assert canonical_entity("places", "京都") == "京都"          # ⊂ 東京都
    assert canonical_entity("places", "東京都") == "東京都"
    assert canonical_entity("companies", "歐洲南方天文台") == "歐洲南方天文台"  # ⊂ 天文台
    assert canonical_entity("places", "香港會議展覽中心") == "香港會議展覽中心"  # ⊂ 香港


def test_canonical_entity_keeps_place_granularity():
    # 「廣東東部」唔可以 merge 落「廣東」——打風報道講嘅正正係東部沿岸。
    from src.entity_digest import canonical_entity
    assert canonical_entity("places", "廣東東部") == "廣東東部"
    assert canonical_entity("places", "廣東東部沿岸") == "廣東東部"


def test_graph_builder_uses_same_canonicalisation():
    # build.py 個 graph builder 之前用 raw name（連 zhconv 都冇），所以
    # graph.json 同 entities.json 嘅實體名對唔上。
    source = (Path(__file__).resolve().parents[1] / "build.py").read_text(encoding="utf-8")
    assert "canonical_entity(etype, raw)" in source, "graph builder 要用返同一套 canonicalisation"
