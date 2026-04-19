import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

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

# Conditional-request cache: maps feed URL → {"etag": ..., "last_modified": ...}
# Saves bandwidth when the upstream feed has not changed (HTTP 304 path).
_FEED_CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "feed_http_cache.json"


def _load_feed_http_cache() -> dict:
    if _FEED_CACHE_PATH.exists():
        try:
            return json.loads(_FEED_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_feed_http_cache(cache: dict):
    _FEED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FEED_CACHE_PATH.with_suffix(_FEED_CACHE_PATH.suffix + ".tmp")
    tmp.write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, _FEED_CACHE_PATH)


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


def _clean_url(url: str) -> str:
    """Trim garbage past the first unsafe char. Some feeds (e.g. 明報 娛樂)
    concatenate `<link>` with `" target="blank"` into the URL, breaking
    every downstream parser. First whitespace/quote/angle-bracket wins."""
    if not url:
        return ""
    return re.split(r'[\s"<>]', url, maxsplit=1)[0].strip()


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
    session:    aiohttp.ClientSession,
    feed_info:  dict,
    cutoff:     datetime,
    http_cache: dict,
) -> tuple[list, str | None, bool]:
    """Return (articles, error_message, not_modified).
    not_modified=True means the upstream returned HTTP 304 and we should
    reuse previously-built articles for this source."""
    articles = []
    url = feed_info["url"]
    prev = http_cache.get(url) or {}
    cond_headers = {}
    if prev.get("etag"):
        cond_headers["If-None-Match"] = prev["etag"]
    if prev.get("last_modified"):
        cond_headers["If-Modified-Since"] = prev["last_modified"]
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers=cond_headers or None,
        ) as resp:
            if resp.status == 304:
                return articles, None, True
            if resp.status >= 400:
                return articles, f"HTTP {resp.status}", False
            raw = await resp.read()
            # Store new validators for next run
            new_entry = {}
            if resp.headers.get("ETag"):
                new_entry["etag"] = resp.headers["ETag"]
            if resp.headers.get("Last-Modified"):
                new_entry["last_modified"] = resp.headers["Last-Modified"]
            if new_entry:
                http_cache[url] = new_entry
            elif url in http_cache:
                http_cache.pop(url, None)
        feed = feedparser.parse(raw)
        if getattr(feed, "bozo", False) and not feed.entries:
            return articles, f"parse: {feed.bozo_exception!r}", False
        for entry in feed.entries[:MAX_ITEMS_PER_FEED]:
            url = _clean_url(entry.get("link", ""))
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
        return articles, repr(exc), False
    return articles, None, False


async def fetch_all() -> tuple[list, dict]:
    cutoff     = datetime.now(timezone.utc) - timedelta(hours=ARTICLE_MAX_AGE_HOURS)
    http_cache = _load_feed_http_cache()
    connector  = aiohttp.TCPConnector(limit=30, ssl=False)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, connector=connector) as session:
        tasks   = [_fetch_one(session, f, cutoff, http_cache) for f in RSS_FEEDS]
        results = await asyncio.gather(*tasks)

    source_stats: dict[str, dict] = {}
    articles: list = []
    failed_sources     = 0
    not_modified_count = 0
    for feed_info, (batch, error, not_modified) in zip(RSS_FEEDS, results):
        source_stats[feed_info["name"]] = {
            "category":     feed_info["category"],
            "count":        len(batch),
            "error":        error,
            "not_modified": not_modified,
        }
        if error:
            failed_sources += 1
        if not_modified:
            not_modified_count += 1
        articles.extend(batch)

    # Drop cache entries for feeds no longer in RSS_FEEDS so the file
    # does not grow unbounded as sources get renamed/removed.
    active_urls = {f["url"] for f in RSS_FEEDS}
    http_cache = {u: v for u, v in http_cache.items() if u in active_urls}
    _save_feed_http_cache(http_cache)

    seen, unique = set(), []
    for a in articles:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique.append(a)

    unique.sort(key=lambda x: x["date"], reverse=True)
    parts = [
        f"[fetch] {len(unique)} articles (last {ARTICLE_MAX_AGE_HOURS}h) from {len(RSS_FEEDS)} feeds",
    ]
    if not_modified_count:
        parts.append(f"{not_modified_count} cached (304)")
    if failed_sources:
        parts.append(f"{failed_sources} failed")
    print(" ".join(parts) if len(parts) == 1 else parts[0] + " (" + ", ".join(parts[1:]) + ")")
    return unique, source_stats
