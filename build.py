import asyncio
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

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

DOCS_DIR    = ROOT / "docs"
DATA_DIR    = DOCS_DIR / "data"
CONTENT_DIR = DATA_DIR / "content"

# Fields not needed by the index view — kept out of articles.json to shrink
# the metadata payload. Full content lives at data/content/{id}.json.
_CONTENT_FIELDS = ("content", "rss_content")


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


def save_json(articles: list, source_stats: dict):
    """Write three artefacts:
      - data/articles.json          metadata only (index page)
      - data/content/{id}.json      full HTML content (article page)
      - data/feed.xml               RSS 2.0 feed (external readers)
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. metadata — strip content / rss_content
    meta = [
        {k: v for k, v in a.items() if k not in _CONTENT_FIELDS}
        for a in articles
    ]
    meta_path = DATA_DIR / "articles.json"
    payload = {
        "updated":  datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT"),
        "sources":  source_stats,
        "articles": meta,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    meta_kb = meta_path.stat().st_size // 1024
    print(f"[build] articles.json {meta_kb} KB, {len(articles)} articles")

    # 2. per-article content files
    active_ids = set()
    written = 0
    unchanged = 0
    for a in articles:
        content = a.get("content")
        if not content:
            continue
        active_ids.add(a["id"])
        cpath = CONTENT_DIR / f"{a['id']}.json"
        new_bytes = json.dumps(
            {"content": content}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        # Skip rewriting identical files to keep git diffs minimal and
        # avoid pointless disk churn on every build.
        if cpath.exists() and cpath.read_bytes() == new_bytes:
            unchanged += 1
            continue
        cpath.write_bytes(new_bytes)
        written += 1

    # Prune content files for articles no longer active
    dropped = 0
    for old_file in CONTENT_DIR.glob("*.json"):
        if old_file.stem not in active_ids:
            old_file.unlink()
            dropped += 1
    print(f"[build] content/ {written} written, {unchanged} unchanged, {dropped} pruned")

    # 3. RSS feed
    _write_rss(articles)


def _write_rss(articles: list):
    """Emit data/feed.xml — RSS 2.0 of latest articles."""
    top = articles[:50]  # keep feed small
    now_rfc822 = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    items = []
    for a in top:
        try:
            dt = datetime.fromisoformat(a["date"])
            pub = dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        except Exception:
            pub = now_rfc822
        summary = (a.get("summary") or "").replace("\n", " ").strip()
        desc = xml_escape(f"[{a.get('source','')}] {summary}".strip())
        items.append(
            f"<item>"
            f"<title>{xml_escape(a.get('title',''))}</title>"
            f"<link>{xml_escape(a.get('url',''))}</link>"
            f"<guid isPermaLink=\"false\">{a['id']}</guid>"
            f"<category>{xml_escape(a.get('category',''))}</category>"
            f"<pubDate>{pub}</pubDate>"
            f"<description>{desc}</description>"
            f"</item>"
        )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        '<title>新聞快訊</title>'
        '<link>https://github.com/</link>'
        '<description>Aggregated Hong Kong news</description>'
        f'<lastBuildDate>{now_rfc822}</lastBuildDate>'
        + "".join(items)
        + '</channel></rss>'
    )
    (DATA_DIR / "feed.xml").write_text(rss, encoding="utf-8")


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


def _load_old_content(article_id: str) -> str | None:
    """Read previously saved content/{id}.json if still on disk."""
    path = CONTENT_DIR / f"{article_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("content")
    except Exception:
        return None


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


def _merge_missing_sources(articles: list, old_articles: list, source_stats: dict) -> list:
    """If a source/category produced 0 articles this run (fetch failed),
    fall back to its articles from the previous build to avoid blank categories."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    new_sources = {a["source"] for a in articles}
    old_by_source: dict[str, list] = defaultdict(list)
    for a in old_articles:
        old_by_source[a["source"]].append(a)

    existing_ids = {a["id"] for a in articles}
    added_by_source: dict[str, int] = defaultdict(int)
    for source, old_arts in old_by_source.items():
        if source in new_sources:
            continue
        for a in old_arts:
            if a["id"] in existing_ids:
                continue
            date_str = a.get("date")
            if not date_str:
                continue
            try:
                dt = datetime.fromisoformat(date_str)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= cutoff:
                continue
            # Hydrate content from previous build so scrape_all can skip it
            if not a.get("content"):
                cached = _load_old_content(a["id"])
                if cached:
                    a["content"] = cached
            articles.append(a)
            existing_ids.add(a["id"])
            added_by_source[source] += 1

    if added_by_source:
        total = sum(added_by_source.values())
        print(f"[build] Merged {total} articles from {len(added_by_source)} missing sources")
        for src, n in added_by_source.items():
            if src in source_stats:
                source_stats[src]["restored"] = n
    return articles


async def main():
    print("=== rss-news build start ===")
    t0 = time.monotonic()

    old_articles = _load_old_articles()

    t = time.monotonic()
    articles, source_stats = await fetch_all()
    print(f"[time] fetch   {time.monotonic()-t:.1f}s")

    articles = _merge_missing_sources(articles, old_articles, source_stats)

    t = time.monotonic();  articles = await scrape_all(articles)
    print(f"[time] scrape  {time.monotonic()-t:.1f}s")

    t = time.monotonic();  articles = await analyse_all(articles)
    print(f"[time] analyse {time.monotonic()-t:.1f}s")

    articles = _apply_fallback_summaries(articles, old_articles)
    articles = cluster_articles(articles)
    articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    save_json(articles, source_stats)
    print(f"=== done in {time.monotonic()-t0:.1f}s ===")


if __name__ == "__main__":
    asyncio.run(main())
