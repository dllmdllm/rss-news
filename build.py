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


async def main():
    print("=== rss-news build start ===")
    articles = await fetch_all()
    articles = await scrape_all(articles)
    articles = await analyse_all(articles)
    articles = cluster_articles(articles)
    articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    save_json(articles)
    print("=== done ===")


if __name__ == "__main__":
    asyncio.run(main())
