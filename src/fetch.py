import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import aiohttp
import feedparser
import zhconv

from src.feeds import (
    ENGLISH_SOURCES,
    HTTP_HEADERS,
    MAX_ITEMS_PER_FEED,
    RSS_FEEDS,
    SIMPLIFIED_SOURCES,
)

ARTICLE_MAX_AGE_HOURS = 48


def _parse_date(entry) -> datetime:
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return parsedate_to_datetime(val)
            except Exception:
                pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _make_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _rss_thumbnail(entry) -> str | None:
    """Extract image URL from RSS media tags."""
    if getattr(entry, "media_thumbnail", None):
        return entry.media_thumbnail[0].get("url") or None
    if getattr(entry, "media_content", None):
        for m in entry.media_content:
            url = m.get("url", "")
            if url and any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                return url
    if getattr(entry, "enclosures", None):
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image/"):
                return enc.get("href") or enc.get("url") or None
    return None


async def _fetch_one(
    session: aiohttp.ClientSession, feed_info: dict, cutoff: datetime,
) -> tuple[list, str | None]:
    """Return (articles, error_message). error_message is None on success."""
    articles = []
    try:
        async with session.get(
            feed_info["url"], timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status >= 400:
                return articles, f"HTTP {resp.status}"
            raw = await resp.read()
        feed = feedparser.parse(raw)
        if getattr(feed, "bozo", False) and not feed.entries:
            return articles, f"parse: {feed.bozo_exception!r}"
        for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
            url = entry.get("link", "")
            if not url:
                continue

            date = _parse_date(entry)
            if date < cutoff:
                continue  # skip articles older than ARTICLE_MAX_AGE_HOURS

            # RSS image
            thumbnail = _rss_thumbnail(entry)

            # RSS full content (fallback for blocked sites)
            rss_content = None
            if getattr(entry, "content", None):
                rss_content = entry.content[0].get("value") or None
            if not rss_content:
                rss_content = getattr(entry, "summary", None) or None

            title = entry.get("title", "(no title)")
            if feed_info["name"] in SIMPLIFIED_SOURCES:
                title = zhconv.convert(title, "zh-hk")
            elif feed_info["name"] in ENGLISH_SOURCES:
                try:
                    from deep_translator import GoogleTranslator
                    loop = asyncio.get_running_loop()
                    translated = await loop.run_in_executor(
                        None,
                        lambda t=title: GoogleTranslator(source="auto", target="zh-TW").translate(t),
                    )
                    title = zhconv.convert(translated or title, "zh-hk")
                except Exception:
                    pass

            # Allow per-feed URL-based category override
            category = feed_info["category"]
            for pattern, cat in (feed_info.get("url_category") or {}).items():
                if pattern in url:
                    category = cat
                    break

            articles.append({
                "id":          _make_id(url),
                "title":       title,
                "url":         url,
                "date":        date.isoformat(),
                "source":      feed_info["name"],
                "category":    category,
                "content":     None,
                "thumbnail":   thumbnail,
                "rss_content": rss_content,
            })
    except Exception as exc:
        print(f"[WARN] fetch {feed_info['name']}: {exc!r}")
        return articles, repr(exc)
    return articles, None


async def fetch_all() -> tuple[list, dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ARTICLE_MAX_AGE_HOURS)
    connector = aiohttp.TCPConnector(limit=30, ssl=False)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, connector=connector) as session:
        tasks = [_fetch_one(session, f, cutoff) for f in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    source_stats: dict[str, dict] = {}
    articles: list = []
    failed_sources = 0
    for feed_info, (batch, error) in zip(RSS_FEEDS, results):
        source_stats[feed_info["name"]] = {
            "category": feed_info["category"],
            "count":    len(batch),
            "error":    error,
        }
        if error:
            failed_sources += 1
        articles.extend(batch)

    seen, unique = set(), []
    for a in articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique.append(a)

    unique.sort(key=lambda x: x["date"], reverse=True)
    print(
        f"[fetch] {len(unique)} articles (last {ARTICLE_MAX_AGE_HOURS}h) "
        f"from {len(RSS_FEEDS)} feeds"
        + (f" ({failed_sources} failed)" if failed_sources else "")
    )
    return unique, source_stats
