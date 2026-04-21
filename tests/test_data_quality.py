import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"


def _load_articles():
    return json.loads((DATA_DIR / "articles.json").read_text(encoding="utf-8"))["articles"]


def test_articles_have_required_analysis_fields():
    bad = []
    for article in _load_articles():
        if not article.get("summary"):
            bad.append((article.get("id"), "summary"))
        if not isinstance(article.get("score"), int):
            bad.append((article.get("id"), "score"))
        if not isinstance(article.get("tags"), list):
            bad.append((article.get("id"), "tags"))
        if article.get("sentiment") not in {"positive", "negative", "neutral"}:
            bad.append((article.get("id"), "sentiment"))
    assert not bad[:20]


def test_content_sidecars_match_active_articles():
    articles = _load_articles()
    article_ids = {a["id"] for a in articles}
    content_ids = {p.stem for p in (DATA_DIR / "content").glob("*.json")}
    missing = sorted(article_ids - content_ids)
    extra = sorted(content_ids - article_ids)

    assert not extra[:20]
    assert len(missing) == 0


def test_content_sidecars_are_valid_and_nonempty():
    bad = []
    for path in (DATA_DIR / "content").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            bad.append((path.name, repr(exc)))
            continue
        if data.get("version") != 1:
            bad.append((path.name, "version"))
        if not data.get("content"):
            bad.append((path.name, "content"))
    assert not bad[:20]
