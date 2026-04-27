import asyncio
import calendar
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urljoin

import aiohttp
import feedparser
import zhconv
from bs4 import BeautifulSoup

from src.feeds import (
    ENGLISH_SOURCES,
    HTTP_HEADERS,
    MAX_ITEMS_PER_FEED,
    RSS_FEEDS,
    SIMPLIFIED_SOURCES,
)

ARTICLE_MAX_AGE_HOURS = 30
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")

# Conditional-request cache: maps feed URL → {"etag": ..., "last_modified": ...}
# Saves bandwidth when the upstream feed has not changed (HTTP 304 path).
_FEED_CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "feed_http_cache.json"


def _parse_title_translations(raw: str, expected: int) -> list[str] | None:
    text = re.sub(r"^\s*```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```\s*$", "", text)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    return [zhconv.convert(str(item).strip(), "zh-hk") for item in data]


async def _translate_titles_minimax(
    session: aiohttp.ClientSession,
    titles: list[str],
) -> list[str]:
    if not titles or not MINIMAX_API_KEY:
        return titles

    numbered = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))
    user_text = (
        "Translate the following news titles into Hong Kong Traditional Chinese.\n"
        "Return only a JSON array of strings, same order and same length. "
        "Do not add explanations.\n\n"
        f"{numbered}"
    )
    try:
        async with session.post(
            "https://api.minimax.io/anthropic/v1/messages",
            headers={
                "x-api-key": MINIMAX_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": MINIMAX_MODEL,
                # MiniMax-M2.7 prepends an internal "thinking" segment before the
                # JSON answer. If the budget truncates the thinking, the answer
                # never arrives and _parse_title_translations returns None,
                # causing us to silently fall back to English titles. Budget
                # generously so the final array always fits.
                "max_tokens": max(1200, len(titles) * 200),
                "system": "You are a concise news title translator. Output valid JSON only.",
                "messages": [{"role": "user", "content": user_text}],
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as exc:
        print(f"[WARN] MiniMax title translation failed: {exc!r}")
        return titles

    if data.get("error"):
        print(f"[WARN] MiniMax title translation error: {data.get('error')!r}")
        return titles
    blocks = data.get("content") or []
    raw = next((b.get("text", "").strip() for b in blocks if b.get("type") == "text"), "")
    translated = _parse_title_translations(raw, len(titles)) if raw else None
    if not translated:
        print(f"[WARN] title translation parse failed ({len(titles)} titles); "
              f"raw head: {raw[:120]!r}")
        return titles
    return translated


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


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_date(entry) -> datetime:
    # Prefer feedparser's already-normalised struct_time — it handles both
    # RFC 2822 (<pubDate>) and ISO 8601 (<updated> in Atom / HKEPC) feeds.
    # parsedate_to_datetime only understands RFC 2822, so feeds like HKEPC
    # that ship ISO dates used to fall through to datetime.min and get
    # filtered out by the cutoff.
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime.fromtimestamp(calendar.timegm(val), tz=timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated"):
        val = getattr(entry, attr, None)
        if val:
            dt: datetime | None = None
            try:
                dt = parsedate_to_datetime(val)
            except Exception:
                # Some feeds ship ISO-8601 strings in `published`/`updated`.
                # Keep this fallback so we do not drop those entries when
                # feedparser fails to populate *_parsed.
                try:
                    dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                except Exception:
                    dt = None
            if dt is None:
                continue
            return _as_utc(dt)
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


def _map_category_for_url(article_url: str, feed_info: dict) -> str:
    decoded = unquote(article_url or "")
    for pattern, cat in (feed_info.get("url_category") or {}).items():
        if pattern in decoded:
            return cat
    return feed_info["category"]


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


def _parse_oncc_datetime(url: str) -> datetime:
    match = re.search(r"/(\d{8})/bkn-(\d{14})", url)
    if not match:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.strptime(match.group(2), "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _parse_am730_date(raw: str) -> datetime:
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return _as_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _am730_find_text(node: ET.Element, path: str) -> str:
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def _parse_am730_sitemap(xml_text: str, feed_info: dict, cutoff: datetime) -> list[dict]:
    try:
        root = ET.fromstring(xml_text or "")
    except ET.ParseError:
        return []

    max_items = feed_info.get("max_items", MAX_ITEMS_PER_FEED)
    articles: list[dict] = []
    seen: set[str] = set()

    for url_node in root.findall(".//{*}url"):
        article_url = _clean_url(_am730_find_text(url_node, "{*}loc"))
        if not article_url or article_url in seen:
            continue
        pub_raw = _am730_find_text(url_node, ".//{*}publication_date")
        title = _am730_find_text(url_node, ".//{*}title")
        if not pub_raw or not title:
            continue

        date = _parse_am730_date(pub_raw)
        if date < cutoff:
            continue

        thumbnail = _clean_url(_am730_find_text(url_node, ".//{*}image/{*}loc")) or None
        seen.add(article_url)
        articles.append({
            "id": _make_id(article_url),
            "title": title,
            "url": article_url,
            "date": date.isoformat(),
            "source": feed_info["name"],
            "category": _map_category_for_url(article_url, feed_info),
            "content": None,
            "thumbnail": thumbnail,
            "rss_content": None,
        })
    articles.sort(key=lambda a: a["date"], reverse=True)
    return articles[:max_items]


_SKYPOST_SITEMAP_INDEX_URL = "http://skypost.hk/sitemap.xml"
_SKYPOST_NEWS_SECTION = "港聞"


def _skypost_article_id(url: str) -> str:
    match = re.search(r"/article/(\d+)", url or "")
    return match.group(1) if match else ""


def _parse_sitemap_urls(xml: str) -> list[str]:
    soup = BeautifulSoup(xml or "", "xml")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc") if loc.get_text(strip=True)]


def _dedupe_skypost_urls(urls: list[str]) -> list[str]:
    ordered_ids: list[str] = []
    by_id: dict[str, str] = {}
    for url in urls:
        aid = _skypost_article_id(url)
        if not aid:
            continue
        prev = by_id.get(aid)
        # Prefer the slugged URL over the bare /article/{id}/ entry.
        if not prev or len(url) > len(prev):
            by_id[aid] = url
        if aid not in ordered_ids:
            ordered_ids.append(aid)
    return [by_id[aid] for aid in ordered_ids if aid in by_id]


def _skypost_hidden_text(soup: BeautifulSoup, field: str) -> str:
    node = soup.select_one(f".hiddenOG .{field}")
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True) if node else "").strip()


def _skypost_http(url: str) -> str:
    return url.replace("https://", "http://", 1) if url.startswith("https://") else url


def _skypost_parse_date(soup: BeautifulSoup) -> datetime | None:
    raw = _skypost_hidden_text(soup, "ga4PublishDateHidden")
    if not raw:
        raw = re.sub(r"^\s*發佈時間:\s*", "", soup.select_one(".publish-time") .get_text(" ", strip=True) if soup.select_one(".publish-time") else "")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(raw[:10], fmt)
            return dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _skypost_parse_article(html: str, url: str, cutoff: datetime, feed_info: dict) -> dict | None:
    soup = BeautifulSoup(html or "", "html.parser")
    section = _skypost_hidden_text(soup, "sectionNameHidden") or _skypost_hidden_text(soup, "SectionNameCodeHidden")
    if section != _SKYPOST_NEWS_SECTION:
        return None
    date = _skypost_parse_date(soup)
    if not date:
        return None

    title = _skypost_hidden_text(soup, "metaTitleHidden") or (
        soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else ""
    ) or _skypost_hidden_text(soup, "urlHeadlineHidden")
    if not title:
        return None

    thumbnail = _skypost_hidden_text(soup, "ogImageUrlHidden")
    if not thumbnail:
        hero = soup.select_one(".article-details-img-container img")
        thumbnail = (hero.get("src") or hero.get("data-src") or "").strip() if hero else ""

    return {
        "id":          _make_id(url),
        "title":       title,
        "url":         url,
        "date":        date.isoformat(),
        "source":      feed_info["name"],
        "category":    feed_info["category"],
        "content":     None,
        "thumbnail":   thumbnail or None,
        "rss_content": None,
    }


async def _fetch_skypost(
    session:   aiohttp.ClientSession,
    feed_info: dict,
    cutoff:    datetime,
) -> tuple[list, str | None, bool]:
    """SkyPost does not expose a stable RSS feed, so pull candidate article
    URLs from the sitemap and filter by the page's hidden section markers.
    """
    articles: list = []
    async def _fetch_text(url: str, *, accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", referer: str | None = None) -> str | None:
        try:
            headers = {"Accept": accept}
            if referer:
                headers["Referer"] = referer
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                headers=headers,
            ) as resp:
                if resp.status >= 400:
                    return None
                raw = await resp.read()
                charset = resp.charset or "utf-8"
                return raw.decode(charset, errors="replace")
        except Exception:
            return None

    index_xml = await _fetch_text(_SKYPOST_SITEMAP_INDEX_URL, accept="application/xml,text/xml,*/*;q=0.8")
    if not index_xml:
        return articles, "sitemap index fetch failed", False

    sitemap_urls = _parse_sitemap_urls(index_xml)
    sitemap_url = sitemap_urls[-1] if sitemap_urls else ""
    if not sitemap_url:
        return articles, "empty sitemap index", False

    candidate_urls: list[str] = []
    for _ in range(4):
        sitemap_xml = await _fetch_text(_skypost_http(sitemap_url), accept="application/xml,text/xml,*/*;q=0.8")
        if not sitemap_xml:
            return articles, "monthly sitemap fetch failed", False
        parsed_urls = _parse_sitemap_urls(sitemap_xml)
        if not parsed_urls:
            return articles, "empty sitemap page", False
        if any(u.lower().endswith(".xml") for u in parsed_urls):
            sitemap_url = parsed_urls[-1]
            continue
        candidate_urls = _dedupe_skypost_urls(parsed_urls)
        break
    else:
        return articles, "sitemap depth exceeded", False

    # The sitemap is newest-first, so the first few dozen URLs are enough to
    # pick up the current news articles without hammering the whole month's
    # archive.
    candidate_urls = candidate_urls[:120]
    max_items = feed_info.get("max_items", MAX_ITEMS_PER_FEED)

    async def _fetch_and_parse(article_url: str) -> dict | None:
        html = await _fetch_text(
            _skypost_http(article_url),
            referer="http://skypost.hk/news/%E8%A6%81%E8%81%9E/",
        )
        if not html:
            return None
        return _skypost_parse_article(html, article_url, cutoff, feed_info)

    # Batch in small groups so we keep ordering while still overlapping the
    # slow page fetches.
    for i in range(0, len(candidate_urls), 10):
        batch = candidate_urls[i:i + 10]
        results = await asyncio.gather(*[_fetch_and_parse(url) for url in batch])
        for article in results:
            if not article:
                continue
            articles.append(article)
            if len(articles) >= max_items:
                return articles, None, False

    return articles, None, False


def _oncc_link_title(anchor) -> str:
    text = anchor.get_text(" ", strip=True)
    if text:
        return text
    img = anchor.find("img")
    if img:
        return (img.get("alt") or img.get("title") or "").strip()
    return ""


def _oncc_link_thumbnail(anchor, base_url: str) -> str | None:
    img = anchor.find("img")
    if not img:
        return None
    src = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-original")
        or img.get("data-lazy-src")
        or ""
    ).strip()
    return urljoin(base_url, src) if src else None


def _parse_oncc_index(html: str, feed_info: dict, cutoff: datetime) -> list[dict]:
    """Extract on.cc BKN links from a channel index page.

    on.cc index pages are not RSS feeds, but article URLs carry a stable
    timestamp (`bkn-YYYYMMDDHHMMSS...`) that is enough for our freshness
    cutoff. Keep this parser deliberately broad because the page markup shifts
    often while the URL shape stays stable.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    base_url = feed_info["url"]
    section = feed_info.get("oncc_section") or ""
    max_items = feed_info.get("max_items", MAX_ITEMS_PER_FEED)
    articles: list[dict] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        article_url = urljoin(base_url, anchor.get("href", ""))
        if "hk.on.cc/hk/bkn/cnt/" not in article_url:
            continue
        if section and f"/cnt/{section}/" not in article_url:
            continue
        if article_url in seen:
            continue

        date = _parse_oncc_datetime(article_url)
        if date < cutoff:
            continue

        title = _oncc_link_title(anchor)
        if not title:
            continue

        seen.add(article_url)
        articles.append({
            "id":          _make_id(article_url),
            "title":       title,
            "url":         article_url,
            "date":        date.isoformat(),
            "source":      feed_info["name"],
            "category":    feed_info["category"],
            "content":     None,
            "thumbnail":   _oncc_link_thumbnail(anchor, base_url),
            "rss_content": None,
        })
        if len(articles) >= max_items:
            break
    return articles


async def _read_feed(
    session: aiohttp.ClientSession,
    url: str,
    cond_headers: dict,
    *,
    ssl=True,
) -> tuple[int, bytes, dict]:
    async with session.get(
        url,
        timeout=aiohttp.ClientTimeout(total=15),
        headers=cond_headers or None,
        ssl=ssl,
    ) as resp:
        raw = await resp.read()
        return resp.status, raw, dict(resp.headers)


async def _read_feed_with_tls_fallback(
    session: aiohttp.ClientSession,
    url: str,
    cond_headers: dict,
) -> tuple[int, bytes, dict]:
    try:
        return await _read_feed(session, url, cond_headers)
    except aiohttp.ClientSSLError as exc:
        print(f"[WARN] feed TLS verification failed for {url[:60]}: {exc!r}; retrying without verification")
        return await _read_feed(session, url, cond_headers, ssl=False)


async def _fetch_hk01(
    session:   aiohttp.ClientSession,
    feed_info: dict,
    cutoff:    datetime,
) -> tuple[list, str | None, bool]:
    """HK01 has no RSS. Hit the same JSON endpoint the site itself uses
    (`/v2/feed/{category|zone}/{id}`) and adapt the payload to our article
    shape. The API ignores conditional-request headers, so not_modified
    is always False here."""
    articles: list = []
    url = feed_info["url"]
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"Accept": "application/json"},
        ) as resp:
            if resp.status >= 400:
                return articles, f"HTTP {resp.status}", False
            data = await resp.json(content_type=None)
    except Exception as exc:
        print(f"[WARN] fetch {feed_info['name']}: {exc!r}")
        return articles, repr(exc), False

    max_items = feed_info.get("max_items", MAX_ITEMS_PER_FEED)
    for item in (data.get("items") or [])[:max_items]:
        if item.get("type") != 1:
            continue  # skip sponsored cards / videos
        d = item.get("data") or {}
        article_url = _clean_url(d.get("publishUrl") or "")
        if not article_url:
            continue
        ts = d.get("publishTime")
        if not ts:
            continue
        try:
            date = datetime.fromtimestamp(int(ts), timezone.utc)
        except Exception:
            continue
        if date < cutoff:
            continue

        thumbnail = (d.get("mainImage") or {}).get("cdnUrl") \
            or (d.get("originalImage") or {}).get("cdnUrl") \
            or None
        description = (d.get("description") or "").strip() or None

        articles.append({
            "id":          _make_id(article_url),
            "title":       d.get("title", "(no title)"),
            "url":         article_url,
            "date":        date.isoformat(),
            "source":      feed_info["name"],
            "category":    _map_category_for_url(article_url, feed_info),
            "content":     None,
            "thumbnail":   thumbnail,
            "rss_content": description,
        })
    return articles, None, False


async def _fetch_am730(
    session: aiohttp.ClientSession,
    feed_info: dict,
    cutoff: datetime,
) -> tuple[list, str | None, bool]:
    """am730 publishes a Google News-style sitemap with title/date/image."""
    try:
        async with session.get(
            feed_info["url"],
            timeout=aiohttp.ClientTimeout(total=20),
            headers={"Accept": "application/xml,text/xml,*/*;q=0.8"},
        ) as resp:
            if resp.status >= 400:
                return [], f"HTTP {resp.status}", False
            raw = await resp.read()
            charset = resp.charset or "utf-8"
    except Exception as exc:
        print(f"[WARN] fetch {feed_info['name']}: {exc!r}")
        return [], repr(exc), False

    xml_text = raw.decode(charset, errors="replace")
    articles = _parse_am730_sitemap(xml_text, feed_info, cutoff)
    return articles, None, False


async def _fetch_oncc(
    session:   aiohttp.ClientSession,
    feed_info: dict,
    cutoff:    datetime,
) -> tuple[list, str | None, bool]:
    """Fetch on.cc channel index pages and adapt article links to our shape.

    The channel pages do not expose useful conditional-request validators in a
    way we rely on, so not_modified is always False here.
    """
    url = feed_info["url"]
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=15),
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.6",
            },
        ) as resp:
            if resp.status >= 400:
                return [], f"HTTP {resp.status}", False
            raw = await resp.read()
            charset = resp.charset or "utf-8"
    except aiohttp.ClientSSLError:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                ssl=False,
            ) as resp:
                if resp.status >= 400:
                    return [], f"HTTP {resp.status}", False
                raw = await resp.read()
                charset = resp.charset or "utf-8"
        except Exception as exc:
            print(f"[WARN] fetch {feed_info['name']}: {exc!r}")
            return [], repr(exc), False
    except Exception as exc:
        print(f"[WARN] fetch {feed_info['name']}: {exc!r}")
        return [], repr(exc), False

    html = raw.decode(charset, errors="replace")
    articles = _parse_oncc_index(html, feed_info, cutoff)
    return articles, None, False


async def _fetch_one(
    session:    aiohttp.ClientSession,
    feed_info:  dict,
    cutoff:     datetime,
    http_cache: dict,
) -> tuple[list, str | None, bool]:
    """Return (articles, error_message, not_modified).
    not_modified=True means the upstream returned HTTP 304 and we should
    reuse previously-built articles for this source."""
    if feed_info.get("fetcher") == "hk01":
        return await _fetch_hk01(session, feed_info, cutoff)
    if feed_info.get("fetcher") == "am730":
        return await _fetch_am730(session, feed_info, cutoff)
    if feed_info.get("fetcher") == "oncc":
        return await _fetch_oncc(session, feed_info, cutoff)
    if feed_info.get("fetcher") == "skypost":
        return await _fetch_skypost(session, feed_info, cutoff)
    articles = []
    url = feed_info["url"]
    prev = http_cache.get(url) or {}
    cond_headers = {}
    if prev.get("etag"):
        cond_headers["If-None-Match"] = prev["etag"]
    if prev.get("last_modified"):
        cond_headers["If-Modified-Since"] = prev["last_modified"]
    try:
        status, raw, headers = await _read_feed_with_tls_fallback(session, url, cond_headers)
        if status == 304:
            return articles, None, True
        if status >= 400:
            return articles, f"HTTP {status}", False

        # Store new validators for next run
        new_entry = {}
        if headers.get("ETag"):
            new_entry["etag"] = headers["ETag"]
        if headers.get("Last-Modified"):
            new_entry["last_modified"] = headers["Last-Modified"]
        if new_entry:
            http_cache[url] = new_entry
        elif url in http_cache:
            http_cache.pop(url, None)
        feed = feedparser.parse(raw)
        if getattr(feed, "bozo", False):
            if not feed.entries:
                return articles, f"parse: {feed.bozo_exception!r}", False
            print(f"[WARN] feed {feed_info['name']}: bozo ({feed.bozo_exception!r}) but {len(feed.entries)} entries — proceeding")
        # Per-feed override lets mixed-category feeds (星島 main RSS) keep
        # enough items that category reclassification via `url_category`
        # actually picks up entertainment/leisure items, which tend to sit
        # below the top-20 breaking-news slice.
        max_items = feed_info.get("max_items", MAX_ITEMS_PER_FEED)
        pending_title_translations: list[dict] = []
        for entry in feed.entries[:max_items]:
            article_url = _clean_url(entry.get("link", ""))
            if not article_url:
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

            article = {
                "id":          _make_id(article_url),
                "title":       title,
                "url":         article_url,
                "date":        date.isoformat(),
                "source":      feed_info["name"],
                "category":    _map_category_for_url(article_url, feed_info),
                "content":     None,
                "thumbnail":   thumbnail,
                "rss_content": rss_content,
            }
            articles.append(article)
            if feed_info["name"] in ENGLISH_SOURCES:
                pending_title_translations.append(article)
        if pending_title_translations:
            titles = [article["title"] for article in pending_title_translations]
            translated = await _translate_titles_minimax(session, titles)
            for article, title in zip(pending_title_translations, translated):
                article["title"] = title
    except Exception as exc:
        print(f"[WARN] fetch {feed_info['name']}: {exc!r}")
        return articles, repr(exc), False
    return articles, None, False


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _looks_untranslated(title: str) -> bool:
    """English-source titles re-hydrated from a prior build (where the
    translation step may have silently failed) stay in English. Detect by
    absence of CJK characters so we can re-run translation on them."""
    return bool(title) and not _CJK_RE.search(title)


async def retranslate_english_titles(articles: list) -> None:
    """Re-translate ENGLISH_SOURCES titles that are still English in-place.

    Covers two cases the fetch loop can't: (a) articles hydrated from a prior
    build whose translation was silently truncated, and (b) 304-cached feeds
    that never re-entered the translation path this run. Mutates `articles`
    directly."""
    if not MINIMAX_API_KEY:
        return
    pending = [
        a for a in articles
        if a.get("source") in ENGLISH_SOURCES and _looks_untranslated(a.get("title", ""))
    ]
    if not pending:
        return
    titles = [a["title"] for a in pending]
    async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
        # Translate in chunks of 10 to keep each response well under the token
        # budget. Chunks run concurrently so one slow straggler does not
        # serialize the whole retrofix pass.
        chunks = [titles[i:i + 10] for i in range(0, len(titles), 10)]
        results = await asyncio.gather(
            *[_translate_titles_minimax(session, chunk) for chunk in chunks]
        )
        translated: list[str] = [t for out in results for t in out]
    changed = 0
    for article, new_title in zip(pending, translated):
        if new_title and new_title != article["title"]:
            article["title"] = new_title
            changed += 1
    if changed:
        print(f"[fetch] retranslated {changed}/{len(pending)} stale English titles")


async def fetch_all() -> tuple[list, dict]:
    cutoff     = datetime.now(timezone.utc) - timedelta(hours=ARTICLE_MAX_AGE_HOURS)
    http_cache = _load_feed_http_cache()
    connector  = aiohttp.TCPConnector(limit=30)
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
