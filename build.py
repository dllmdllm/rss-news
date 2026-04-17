import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.fetch   import fetch_all
from src.scrape  import scrape_all
from src.analyse import analyse_all

DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"


def cluster_articles(articles: list) -> list:
    """Group articles sharing the same AI-assigned topic into clusters."""
    topic_groups: dict[str, list[str]] = defaultdict(list)
    for a in articles:
        topic = (a.get("topic") or "").strip()
        if topic:
            topic_groups[topic].append(a["id"])

    id_to_cluster: dict[str, tuple[str, int]] = {}
    for topic, ids in topic_groups.items():
        if len(ids) > 1:
            cid = hashlib.md5(topic.encode()).hexdigest()[:8]
            for aid in ids:
                id_to_cluster[aid] = (cid, len(ids))

    for a in articles:
        if a["id"] in id_to_cluster:
            cid, size = id_to_cluster[a["id"]]
            a["cluster_id"]   = cid
            a["cluster_size"] = size

    clusters_found = len({v[0] for v in id_to_cluster.values()})
    print(f"[cluster] {clusters_found} topic clusters found")
    return articles


def save_json(articles: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "articles.json"
    payload = {
        "updated":  datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT"),
        "articles": articles,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = path.stat().st_size // 1024
    print(f"[build] Saved: {path} ({size_kb} KB, {len(articles)} articles)")


def _load_old_articles() -> list:
    """Load previous articles.json as a full list fallback."""
    path = DATA_DIR / "articles.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("articles", [])
    except Exception:
        return []


def _apply_fallback_summaries(articles: list, old_articles: list) -> list:
    """For articles that failed AI analysis this run, restore from previous build."""
    old = {a["id"]: a for a in old_articles if a.get("summary")}
    restored = 0
    for a in articles:
        if not a.get("summary") and a["id"] in old:
            src = old[a["id"]]
            for field in ("summary", "score", "tags", "sentiment", "topic"):
                if src.get(field) is not None:
                    a[field] = src[field]
            restored += 1
    if restored:
        print(f"[build] Restored {restored} summaries from previous articles.json")
    return articles


def _merge_missing_sources(articles: list, old_articles: list) -> list:
    """If a source/category produced 0 articles this run (fetch failed),
    fall back to its articles from the previous build to avoid blank categories."""
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    new_sources = {a["source"] for a in articles}
    old_by_source = defaultdict(list)
    for a in old_articles:
        old_by_source[a["source"]].append(a)

    existing_ids = {a["id"] for a in articles}
    added = 0
    for source, old_arts in old_by_source.items():
        if source not in new_sources:
            # Filter to still-recent articles only
            recent = []
            for a in old_arts:
                try:
                    dt = datetime.fromisoformat(a.get("date", "")).replace(tzinfo=timezone.utc) \
                         if a.get("date") else None
                    if dt and dt > cutoff:
                        recent.append(a)
                except Exception:
                    pass
            for a in recent:
                if a["id"] not in existing_ids:
                    articles.append(a)
                    existing_ids.add(a["id"])
                    added += 1
    if added:
        print(f"[build] Merged {added} articles from {len(old_by_source)-len(new_sources)} missing sources")
    return articles


async def main():
    print("=== rss-news build start ===")
    old_articles = _load_old_articles()
    articles = await fetch_all()
    articles = _merge_missing_sources(articles, old_articles)
    articles = await scrape_all(articles)
    articles = await analyse_all(articles)
    articles = _apply_fallback_summaries(articles, old_articles)
    articles = cluster_articles(articles)
    articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    save_json(articles)
    print("=== done ===")


if __name__ == "__main__":
    asyncio.run(main())
