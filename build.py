import asyncio
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
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

from src.fetch   import ARTICLE_MAX_AGE_HOURS, fetch_all, retranslate_english_titles
from src.scrape  import content_quality, scrape_all
from src.analyse import analyse_all, looks_like_prompt_schema_summary

DOCS_DIR    = ROOT / "docs"
DATA_DIR    = DOCS_DIR / "data"
CONTENT_DIR = DATA_DIR / "content"
CONTENT_SCHEMA_VERSION = 1
TRENDING_WINDOW_HOURS = 4
TRENDING_LIMIT = 10
GRAPH_WINDOW_HOURS = 168     # 7-day knowledge graph window
GRAPH_MIN_NODE_COUNT = 2     # drop entities mentioned in only 1 article
GRAPH_MIN_EDGE_WEIGHT = 2    # drop pairs that co-occur in only 1 article
GRAPH_MAX_NODES = 150        # browser ceiling — cytoscape gets sluggish above this
GRAPH_MAX_EDGES = 300
GRAPH_ENTITY_TYPES = ("people", "companies", "places")

TOPIC_ALIASES = [
    (("伊朗", "美伊", "霍爾木茲", "以色列", "黎巴嫩"), "伊朗局勢"),
    (("宏福苑", "宏新閣", "火警聽證", "居民上樓"), "宏福苑跟進"),
    (("高市早苗", "靖國", "日本首相"), "日本政局"),
    (("蘋果", "庫克", "特努斯", "Ternus", "Apple"), "蘋果CEO交接"),
    (("機械人", "機器人", "人形機械", "半馬"), "機械人發展"),
    (("港股", "恆指", "新股", "IPO"), "港股市場"),
    (("天氣", "天文台", "雷暴", "驟雨"), "香港天氣"),
]

# Fields not needed by the index view — kept out of articles.json to shrink
# the metadata payload. Full content lives at data/content/{id}.json.
_CONTENT_FIELDS = ("content", "rss_content")

def _title_bigrams(title: str) -> set[str]:
    """Character bigrams of normalized title — for Jaccard similarity."""
    norm = "".join(
        ch for ch in str(title or "").lower()
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )
    if len(norm) < 2:
        return set()
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


def detect_duplicates(articles: list, *, threshold: float = 0.82) -> list:
    """Mark near-duplicate articles by title similarity.

    Uses Jaccard similarity on character bigrams of the normalized title.
    Pairs above ``threshold`` are grouped (union-find); within each group
    we pick a canonical article (highest score, newest date) and stamp
    the rest with ``duplicate_of``. The canonical gets ``duplicate_count``
    so the front-end can collapse or badge the group.

    Jaccard-on-bigrams is more reliable than SimHash for short strings
    like news headlines — SimHash needs long text to be stable.
    """
    if not articles:
        return articles

    bigrams: list[set[str]] = []
    for a in articles:
        a.pop("duplicate_of", None)
        a.pop("duplicate_count", None)
        bigrams.append(_title_bigrams(a.get("title", "")))

    parent = list(range(len(articles)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    n = len(articles)
    for i in range(n):
        bi = bigrams[i]
        if not bi:
            continue
        for j in range(i + 1, n):
            bj = bigrams[j]
            if not bj:
                continue
            inter = len(bi & bj)
            if not inter:
                continue
            uni = len(bi | bj)
            if uni and inter / uni >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        if bigrams[i]:
            groups[find(i)].append(i)

    marked = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda idx: (
                articles[idx].get("score") or 0,
                articles[idx].get("date") or "",
            ),
            reverse=True,
        )
        canonical = articles[members[0]]
        canonical["duplicate_count"] = len(members)
        for idx in members[1:]:
            articles[idx]["duplicate_of"] = canonical["id"]
            marked += 1

    if marked:
        print(f"[dedup] {marked} near-duplicate articles marked")
    return articles


def cluster_articles(articles: list) -> list:
    """Group articles sharing the same AI-assigned topic into clusters."""
    for a in articles:
        # Restored articles can carry cluster fields from a previous build.
        # Clear them before recalculating so stale clusters cannot leak through.
        a.pop("cluster_id", None)
        a.pop("cluster_size", None)

    topic_groups: dict[str, list[str]] = defaultdict(list)
    for a in articles:
        # Skip near-duplicates so cluster_size reflects distinct reports.
        if a.get("duplicate_of"):
            continue
        topic = normalise_topic(a)
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


def normalise_topic(article: dict) -> str:
    raw = (article.get("topic") or "").strip()
    haystack = " ".join(
        str(article.get(field, ""))
        for field in ("topic", "title", "summary")
        if article.get(field)
    )
    for keywords, canonical in TOPIC_ALIASES:
        if any(keyword in haystack for keyword in keywords):
            return canonical
    return raw


def _parse_article_datetime(value: str):
    """Parse an ISO-8601 date string into an aware UTC datetime, or None."""
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_trending_topics(
    articles: list,
    *,
    now: datetime | None = None,
    hours: int = TRENDING_WINDOW_HOURS,
    limit: int = TRENDING_LIMIT,
) -> list:
    """Return hot AI topics with at least two recent articles."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    groups: dict[str, list[dict]] = defaultdict(list)

    for article in articles:
        topic = normalise_topic(article)
        if not topic:
            continue
        dt = _parse_article_datetime(article.get("date", ""))
        if not dt or dt < cutoff:
            continue
        groups[topic].append(article)

    trending = []
    for topic, rows in groups.items():
        if len(rows) < 2:
            continue

        sources = sorted({row.get("source", "") for row in rows if row.get("source")})
        scores = [row.get("score") for row in rows if isinstance(row.get("score"), int)]
        dates = [
            dt for dt in (_parse_article_datetime(row.get("date", "")) for row in rows)
            if dt
        ]
        latest_dt = max(dates) if dates else None
        avg_score = round(sum(scores) / len(scores), 1) if scores else 5.0
        age_hours = ((now - latest_dt).total_seconds() / 3600) if latest_dt else hours
        recency = max(0.0, hours - min(max(age_hours, 0), hours))
        heat = round(len(rows) * 8 + len(sources) * 5 + avg_score * 3 + recency * 2, 2)
        sorted_rows = sorted(rows, key=lambda row: row.get("date", ""), reverse=True)

        trending.append({
            "topic": topic,
            "count": len(rows),
            "sources": sources[:5],
            "source_count": len(sources),
            "avg_score": avg_score,
            "latest_date": latest_dt.isoformat() if latest_dt else "",
            "article_ids": [row["id"] for row in sorted_rows],
            "heat": heat,
        })

    trending.sort(key=lambda item: (item["heat"], item["count"], item["latest_date"]), reverse=True)
    return trending[:limit]


def build_knowledge_graph(
    articles: list,
    *,
    now: datetime | None = None,
    hours: int = GRAPH_WINDOW_HOURS,
) -> dict:
    """Aggregate entity co-occurrence over the past `hours` window.

    Pure aggregation, no LLM cost. Each entity becomes a node keyed by
    `type:label`; each unordered pair of entities sharing an article
    becomes an edge. Pruned by GRAPH_MIN_* thresholds and capped to
    GRAPH_MAX_* so the cytoscape view stays responsive.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    node_counts: dict[str, int] = defaultdict(int)
    node_labels: dict[str, str] = {}
    node_types: dict[str, str] = {}
    node_articles: dict[str, set[str]] = defaultdict(set)
    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    edge_articles: dict[tuple[str, str], set[str]] = defaultdict(set)

    for article in articles:
        if article.get("duplicate_of"):
            continue
        dt = _parse_article_datetime(article.get("date", ""))
        if not dt or dt < cutoff:
            continue
        aid = article.get("id")
        if not aid:
            continue
        entities = article.get("entities") or {}
        keys: list[str] = []
        for etype in GRAPH_ENTITY_TYPES:
            for raw in entities.get(etype) or []:
                label = str(raw or "").strip()
                if not label or len(label) < 2:
                    continue
                key = f"{etype}:{label}"
                if key not in node_labels:
                    node_labels[key] = label
                    node_types[key] = etype
                node_counts[key] += 1
                node_articles[key].add(aid)
                keys.append(key)

        # Each unordered pair within this article gets an edge bump.
        unique_keys = list(dict.fromkeys(keys))
        for i in range(len(unique_keys)):
            for j in range(i + 1, len(unique_keys)):
                a, b = sorted((unique_keys[i], unique_keys[j]))
                edge_weights[(a, b)] += 1
                edge_articles[(a, b)].add(aid)

    # Prune low-frequency nodes first; edges referencing pruned nodes drop too.
    kept_nodes = {
        key for key, count in node_counts.items()
        if count >= GRAPH_MIN_NODE_COUNT
    }
    nodes_sorted = sorted(
        kept_nodes,
        key=lambda k: (node_counts[k], node_labels[k]),
        reverse=True,
    )[:GRAPH_MAX_NODES]
    kept_nodes = set(nodes_sorted)

    edges_filtered = [
        (pair, weight) for pair, weight in edge_weights.items()
        if weight >= GRAPH_MIN_EDGE_WEIGHT
        and pair[0] in kept_nodes and pair[1] in kept_nodes
    ]
    edges_filtered.sort(key=lambda x: x[1], reverse=True)
    edges_filtered = edges_filtered[:GRAPH_MAX_EDGES]

    nodes_payload = [
        {
            "id": key,
            "label": node_labels[key],
            "type": node_types[key],
            "count": node_counts[key],
            "articles": sorted(node_articles[key])[:20],
        }
        for key in nodes_sorted
    ]
    edges_payload = [
        {
            "source": a,
            "target": b,
            "weight": w,
            "articles": sorted(edge_articles[(a, b)])[:10],
        }
        for (a, b), w in edges_filtered
    ]

    return {
        "updated": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT"),
        "window_hours": hours,
        "nodes": nodes_payload,
        "edges": edges_payload,
    }


def _write_graph(articles: list) -> None:
    graph = build_knowledge_graph(articles)
    path = DATA_DIR / "graph.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    print(f"[graph] {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")


def save_json(articles: list, source_stats: dict):
    """Write three artefacts:
      - data/articles.json          metadata only (index page)
      - data/content/{id}.json      full HTML content (article page)
      - data/feed.xml               RSS 2.0 feed (external readers)
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    # Write sidecars before publishing metadata, so clients never see a fresh
    # articles.json that points at missing data/content/{id}.json files.
    content_stats = _write_content_sidecars(articles)
    _write_rss(articles)
    _write_graph(articles)

    meta = [
        {k: v for k, v in a.items() if k not in _CONTENT_FIELDS}
        for a in articles
    ]
    meta_path = DATA_DIR / "articles.json"
    payload = {
        "updated":  datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT"),
        "sources":  source_stats,
        "trending_topics": build_trending_topics(articles),
        "articles": meta,
    }
    # Atomic write: tmp + rename so a crash mid-write cannot leave the
    # index half-written and break every client that polls it.
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, meta_path)
    meta_kb = meta_path.stat().st_size // 1024
    print(f"[build] articles.json {meta_kb} KB, {len(articles)} articles")

    dropped = _prune_stale_content(active_ids={a["id"] for a in articles})
    print(
        f"[build] content/ {content_stats['written']} written, "
        f"{content_stats['unchanged']} unchanged, {content_stats['reused']} reused, "
        f"{content_stats['minimal']} minimal, {dropped} pruned"
    )


def _minimal_content(article: dict) -> str:
    """Last-resort readable content so every active article has a sidecar."""
    title = html_escape(article.get("title") or "未能擷取全文")
    url = html_escape(article.get("url") or "#", quote=True)
    source = html_escape(article.get("source") or "")
    summary = html_escape(article.get("summary") or "").replace("\n", "<br>")
    summary_html = f"<p>{summary}</p>" if summary else ""
    return (
        f"<p><strong>{title}</strong></p>"
        f"{summary_html}"
        f"<p>暫時未能從{source or '來源'}擷取全文。</p>"
        f'<p><a href="{url}" target="_blank" rel="noopener">閱讀原文</a></p>'
    )


def _write_content_sidecars(articles: list) -> dict[str, int]:
    written = 0
    unchanged = 0
    reused = 0
    minimal = 0
    for a in articles:
        content = a.get("content")
        old_record = None
        fallback = "unknown"
        if not content:
            old_record = _load_old_content_record(a["id"])
            content = old_record.get("content") if old_record else None
            if not content:
                content = _minimal_content(a)
                minimal += 1
                fallback = "minimal"
            else:
                reused += 1
                fallback = "reused"
            a["content"] = content
        cpath = CONTENT_DIR / f"{a['id']}.json"
        quality = a.get("content_quality") or {}
        if not quality and old_record:
            quality = old_record.get("quality") or {}
        if not quality:
            quality = content_quality(
                content,
                source=a.get("source", ""),
                fallback=fallback,
            )
        a["content_quality"] = quality
        # Skip rewriting identical files to keep git diffs minimal and
        # avoid pointless disk churn on every build.
        if cpath.exists():
            try:
                old = json.loads(cpath.read_text(encoding="utf-8"))
            except Exception:
                old = None
            if (
                isinstance(old, dict)
                and old.get("version") == CONTENT_SCHEMA_VERSION
                and old.get("content") == content
                and old.get("quality", {}) == quality
            ):
                unchanged += 1
                continue
        new_bytes = json.dumps(
            {
                "version": CONTENT_SCHEMA_VERSION,
                "content": content,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "quality": quality,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        tmp = cpath.with_suffix(cpath.suffix + ".tmp")
        tmp.write_bytes(new_bytes)
        os.replace(tmp, cpath)
        written += 1

    return {
        "written": written,
        "unchanged": unchanged,
        "reused": reused,
        "minimal": minimal,
    }


def _prune_stale_content(*, active_ids: set[str]) -> int:
    dropped = 0
    for old_file in CONTENT_DIR.glob("*.json"):
        if old_file.stem not in active_ids:
            old_file.unlink()
            dropped += 1
    return dropped


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
        '<title>News Pulse</title>'
        '<link>https://github.com/</link>'
        '<description>Aggregated Hong Kong news</description>'
        f'<lastBuildDate>{now_rfc822}</lastBuildDate>'
        + "".join(items)
        + '</channel></rss>'
    )
    feed_path = DATA_DIR / "feed.xml"
    tmp = feed_path.with_suffix(feed_path.suffix + ".tmp")
    tmp.write_text(rss, encoding="utf-8")
    os.replace(tmp, feed_path)


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


def _load_old_content_record(article_id: str) -> dict | None:
    """Read previously saved content/{id}.json as a dict if still on disk."""
    path = CONTENT_DIR / f"{article_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _apply_fallback_summaries(articles: list, old_articles: list) -> list:
    """For articles that failed AI analysis this run, restore from previous build."""
    old = {
        a["id"]: a
        for a in old_articles
        if a.get("summary") and not looks_like_prompt_schema_summary(a.get("summary", ""))
    }
    restored = 0
    for a in articles:
        if not a.get("summary") and a["id"] in old:
            src = old[a["id"]]
            for field in ("summary", "score", "tags", "sentiment", "topic", "event_type", "entities", "key_sentences", "upcoming_events"):
                if src.get(field) is not None:
                    a[field] = src[field]
            restored += 1
    if restored:
        print(f"[build] Restored {restored} summaries from previous articles.json")
    return articles


def _merge_missing_sources(articles: list, old_articles: list, source_stats: dict) -> list:
    """If a source/category produced 0 articles this run (fetch failed),
    fall back to its articles from the previous build to avoid blank categories."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ARTICLE_MAX_AGE_HOURS)
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
            # Hydrate content from previous build so scrape_all can skip it.
            # articles.json no longer stores `content` inline (split into
            # docs/data/content/*.json), so the key is usually absent here —
            # ensure it's explicitly present (None) even if hydration misses,
            # so downstream code that does `a["content"]` doesn't KeyError.
            if not a.get("content"):
                record = _load_old_content_record(a["id"])
                if record and record.get("content"):
                    a["content"] = record["content"]
                else:
                    a["content"] = None
            articles.append(a)
            existing_ids.add(a["id"])
            added_by_source[source] += 1

    if added_by_source:
        total = sum(added_by_source.values())
        print(f"[build] Merged {total} articles from {len(added_by_source)} missing sources")
        for src, n in added_by_source.items():
            if src in source_stats:
                source_stats[src]["restored"] = n
                source_stats[src]["effective_count"] = source_stats[src].get("count", 0) + n
    for src, stats in source_stats.items():
        stats.setdefault("effective_count", stats.get("count", 0) + stats.get("restored", 0))
    return articles


async def main():
    print("=== rss-news build start ===")
    t0 = time.monotonic()

    old_articles = _load_old_articles()

    t = time.monotonic()
    articles, source_stats = await fetch_all()
    print(f"[time] fetch   {time.monotonic()-t:.1f}s")

    articles = _merge_missing_sources(articles, old_articles, source_stats)
    await retranslate_english_titles(articles)

    t = time.monotonic();  articles = await scrape_all(articles)
    print(f"[time] scrape  {time.monotonic()-t:.1f}s")

    t = time.monotonic();  articles = await analyse_all(articles)
    print(f"[time] analyse {time.monotonic()-t:.1f}s")

    articles = _apply_fallback_summaries(articles, old_articles)
    # Dedup first so clusters count only distinct reports.
    articles = detect_duplicates(articles)
    articles = cluster_articles(articles)
    articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    save_json(articles, source_stats)
    print(f"=== done in {time.monotonic()-t0:.1f}s ===")


if __name__ == "__main__":
    asyncio.run(main())
