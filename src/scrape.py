import asyncio
import json
import re

import aiohttp
import trafilatura
import zhconv
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from src.feeds import (
    ENGLISH_SOURCES,
    HTTP_HEADERS,
    SCRAPE_CONCURRENCY,
    SIMPLIFIED_SOURCES,
)

# Per-request timeouts — fallbacks fire after the main aiohttp fetch fails or
# is blocked, so pages that do not respond quickly are unlikely to recover.
_MAIN_TIMEOUT     = 20
_FALLBACK_TIMEOUT = 15

_BLOCK_PHRASES = [
    "cloudflare ray id",
    "security service to protect",
    "cf-browser-verification",
    "checking your browser",
    "please enable cookies",
    "enable javascript and cookies",
    "ddos protection by",
]

_LAZY_ATTRS = [
    "data-src", "data-lazy-src", "data-original", "data-lazy",
    "data-delayed-url", "data-url", "data-image", "data-echo",
    "lazysrc", "data-actualsrc", "data-hi-res-src",
]


def _expand_stheadline_galleries(html: str) -> str:
    """
    星島頭條 uses <gallery-N> custom elements populated at runtime by JS.
    The actual image data lives in a JS variable `article_galleries` embedded
    in the page HTML.  Replace each <gallery-N> with the corresponding <img>
    tags so trafilatura can see them.
    """
    if 'article_galleries' not in html:
        return html
    m = re.search(r'const article_galleries\s*=\s*(\{.*?\});\s*\n', html, re.DOTALL)
    if not m:
        return html
    try:
        galleries = json.loads(m.group(1))
    except Exception:
        return html

    def _gallery_imgs(key):
        imgs = []
        for item in galleries.get(key, []):
            src = item.get("src") or ""
            alt = item.get("alt_text") or item.get("caption") or ""
            if src:
                imgs.append(f'<img src="{src}" alt="{alt}">')
        return "\n".join(imgs)

    def _replace_gallery(m2):
        key = m2.group(1)  # e.g. "gallery-1"
        return _gallery_imgs(key)

    html = re.sub(r'<(gallery-\d+)>\s*</\1>', _replace_gallery, html, flags=re.IGNORECASE)
    html = re.sub(r'<(gallery-\d+)\s*/>', _replace_gallery, html, flags=re.IGNORECASE)
    html = re.sub(r'<(gallery-\d+)>', _replace_gallery, html, flags=re.IGNORECASE)
    return html


def _fix_lazy_images(html: str) -> str:
    for attr in _LAZY_ATTRS:
        html = re.sub(
            rf'(<img(?![^>]*\ssrc=)[^>]*?){attr}=(["\'])([^"\']+)\2',
            r'\1src=\2\3\2',
            html,
            flags=re.IGNORECASE,
        )
    return html


def _extract_noscript_imgs(html: str) -> str:
    def _unwrap(m):
        inner = m.group(1)
        img = re.search(r'<img[^>]+>', inner, re.IGNORECASE)
        return img.group(0) if img else ""
    return re.sub(
        r'<noscript[^>]*>(.*?)</noscript>',
        _unwrap,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )


def _is_blocked(html: str) -> bool:
    sample = html[:4000].lower()
    return any(phrase in sample for phrase in _BLOCK_PHRASES)


def _fix_graphic_tags(html: str) -> str:
    """Convert trafilatura's <graphic> TEI elements to standard <img>."""
    def _to_img(m):
        attrs = m.group(1)
        # trafilatura uses url= attribute; browsers need src=
        attrs = re.sub(r'\burl=', 'src=', attrs)
        return '<img' + attrs + '>'
    html = re.sub(r'<graphic([^>]*)></graphic>', _to_img, html, flags=re.IGNORECASE)
    html = re.sub(r'<graphic([^>]*?)/>', _to_img, html, flags=re.IGNORECASE)
    return html


def _fix_picture_elements(html: str) -> str:
    """
    Convert <picture>…</picture> to a plain <img> so trafilatura preserves them.
    Priority: img[src] > img[data-src] > source[srcset] > source[data-srcset]
    """
    if '<picture' not in html:
        return html
    soup = BeautifulSoup(html, "html.parser")
    changed = False
    for pic in soup.find_all("picture"):
        img = pic.find("img")
        url = None
        if img:
            url = (img.get("src") or img.get("data-src") or
                   img.get("data-lazy-src") or img.get("data-original"))
        if not url:
            for src_tag in pic.find_all("source"):
                for attr in ("srcset", "data-srcset"):
                    val = src_tag.get(attr, "")
                    if val:
                        # srcset may be "img.jpg 1x, img@2x.jpg 2x" — take first URL
                        url = val.split(",")[0].split()[0].strip()
                        break
                if url:
                    break
        if url:
            new_img = soup.new_tag("img", src=url)
            if img:
                for attr in ("alt", "width", "height", "class"):
                    if img.get(attr):
                        new_img[attr] = img[attr]
            pic.replace_with(new_img)
            changed = True
    return str(soup) if changed else html


def _remove_leading_title(content: str, title: str) -> str:
    """Remove leading <h1> if it duplicates the article title."""
    soup = BeautifulSoup(content, "html.parser")
    h1 = soup.find("h1")
    if h1:
        h1_text = re.sub(r"\s+", " ", h1.get_text()).strip()
        title_clean = re.sub(r"\s+", " ", title).strip()
        if h1_text and (h1_text in title_clean or title_clean in h1_text or h1_text == title_clean):
            h1.decompose()
            return str(soup)
    return content


def _add_featured_image(content: str, thumbnail: str) -> str:
    """Prepend thumbnail as featured image if content has no inline images."""
    if thumbnail and '<img' not in content:
        img = f'<img src="{thumbnail}" style="max-width:100%;border-radius:6px;margin-bottom:1em">'
        # BeautifulSoup wraps fragments with <html><body>…</body></html>,
        # so insert after <body> to avoid being stripped by innerHTML assignment.
        if '<body>' in content:
            return content.replace('<body>', f'<body>{img}', 1)
        return img + content
    return content


def _to_hk_traditional(content: str) -> str:
    return zhconv.convert(content, "zh-hk")


_TRANSLATE_SEP = "\n@@@@@\n"   # unlikely to appear in article text
_TRANSLATE_CHUNK_CHARS = 4000  # Google Translate caps near 5000 chars/request


def _translate_to_hk(html_content: str) -> str:
    """Translate English HTML → HK Traditional Chinese.
    Collect all text nodes, batch-translate with a separator, then re-inject —
    trades per-paragraph serial HTTP calls (O(N)) for ~ceil(total_chars/4000)
    calls. For a typical 9to5Mac article this drops from ~20 calls to 1–2.
    """
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        translator = GoogleTranslator(source="auto", target="zh-TW")
        tags = soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "figcaption", "blockquote"])
        targets = []
        for tag in tags:
            text = tag.get_text(separator=" ", strip=True)
            if text and len(text) > 3:
                targets.append((tag, text))
        if not targets:
            return zhconv.convert(str(soup), "zh-hk")

        texts = [t for _, t in targets]
        translated_all: list[str] = []
        # Pack texts into batches that stay under the char cap
        batch: list[str] = []
        batch_len = 0
        for t in texts:
            prospective = batch_len + len(t) + len(_TRANSLATE_SEP)
            if batch and prospective > _TRANSLATE_CHUNK_CHARS:
                translated_all.extend(_translate_batch(translator, batch))
                batch, batch_len = [], 0
            batch.append(t)
            batch_len += len(t) + len(_TRANSLATE_SEP)
        if batch:
            translated_all.extend(_translate_batch(translator, batch))

        for (tag, _), out in zip(targets, translated_all):
            if out:
                tag.clear()
                tag.append(out)
        return zhconv.convert(str(soup), "zh-hk")
    except Exception as exc:
        print(f"[WARN] translation error: {exc!r}")
        return html_content


def _translate_batch(translator, texts: list[str]) -> list[str]:
    """Translate a batch of text chunks. Returns one output per input,
    falling back to the original on any shape mismatch or API error."""
    joined = _TRANSLATE_SEP.join(texts)
    try:
        out = translator.translate(joined) or ""
    except Exception:
        return list(texts)
    parts = out.split(_TRANSLATE_SEP.strip()) if _TRANSLATE_SEP.strip() in out else out.split(_TRANSLATE_SEP)
    if len(parts) != len(texts):
        # Separator got mangled — fall back to per-text translation
        fallback = []
        for t in texts:
            try:
                r = translator.translate(t[:4500])
                fallback.append(r or t)
            except Exception:
                fallback.append(t)
        return fallback
    return [p.strip() for p in parts]


async def _urllib_fetch(url: str) -> str | None:
    """Fetch using urllib.request in thread pool — bypasses Cloudflare TLS fingerprinting."""
    try:
        import urllib.request
        loop = asyncio.get_running_loop()
        def _fetch():
            req = urllib.request.Request(url, headers=HTTP_HEADERS)
            with urllib.request.urlopen(req, timeout=_FALLBACK_TIMEOUT) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        return await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        print(f"[WARN] urllib_fetch {url[:60]}: {exc!r}")
        return None


async def _cloudscraper_fetch(url: str) -> str | None:
    """Bypass Cloudflare using cloudscraper (runs in thread pool)."""
    try:
        import cloudscraper
        loop = asyncio.get_running_loop()
        def _fetch():
            scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
            r = scraper.get(url, timeout=_FALLBACK_TIMEOUT)
            return r.text
        return await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        print(f"[WARN] cloudscraper {url[:60]}: {exc!r}")
        return None


async def _scrape_one(
    session: aiohttp.ClientSession,
    article: dict,
    sem: asyncio.Semaphore,
) -> dict:
    # Already scraped in a previous build and restored via _merge_missing_sources
    if article.get("content"):
        return article
    async with sem:
        for attempt in range(2):
            try:
                async with session.get(
                    article["url"], timeout=aiohttp.ClientTimeout(total=_MAIN_TIMEOUT)
                ) as resp:
                    raw = await resp.read()
                    charset = resp.charset or "utf-8"
                html = raw.decode(charset, errors="replace")

                if _is_blocked(html):
                    print(f"[BLOCK] {article['source']} — trying urllib fallback")
                    html = await _urllib_fetch(article["url"])
                    if html and not _is_blocked(html):
                        print(f"[UNBLOCK] {article['source']} — urllib succeeded")
                    else:
                        print(f"[BLOCK] {article['source']} — trying cloudscraper fallback")
                        html = await _cloudscraper_fetch(article["url"])
                        if html and not _is_blocked(html):
                            print(f"[UNBLOCK] {article['source']} — cloudscraper succeeded")
                        else:
                            print(f"[BLOCK] {article['source']} — falling back to RSS content")
                            rss = article.get("rss_content") or ""
                            thumb = article.get("thumbnail") or ""
                            img_html = f'<img src="{thumb}" style="max-width:100%;border-radius:6px;margin-bottom:1em">' if thumb else ""
                            if rss or img_html:
                                content = img_html + rss
                                if article["source"] in SIMPLIFIED_SOURCES:
                                    content = _to_hk_traditional(content)
                                article["content"] = content
                            return article

                html = _expand_stheadline_galleries(html)
                html = _extract_noscript_imgs(html)
                html = _fix_picture_elements(html)
                html = _fix_lazy_images(html)

                # trafilatura.extract is CPU-bound; run in executor so the
                # event loop can continue overlapping other HTTP fetches.
                loop = asyncio.get_running_loop()
                content = await loop.run_in_executor(
                    None,
                    lambda: trafilatura.extract(
                        html,
                        output_format="html",
                        include_images=True,
                        include_links=False,
                        favor_precision=True,
                        no_fallback=False,
                    ),
                )

                if content:
                    content = _fix_graphic_tags(content)
                    content = _remove_leading_title(content, article.get("title", ""))
                    content = _add_featured_image(content, article.get("thumbnail") or "")
                    if article["source"] in SIMPLIFIED_SOURCES:
                        content = _to_hk_traditional(content)
                    elif article["source"] in ENGLISH_SOURCES:
                        content = await loop.run_in_executor(None, _translate_to_hk, content)
                    article["content"] = content

                # Extract og:image thumbnail if not already set from RSS
                if not article.get("thumbnail"):
                    meta = await loop.run_in_executor(None, trafilatura.extract_metadata, html)
                    if meta and meta.image:
                        article["thumbnail"] = meta.image

                break  # success, no retry needed

            except Exception as exc:
                if attempt == 0:
                    await asyncio.sleep(3)  # wait before retry
                else:
                    print(f"[WARN] scrape {article['url'][:70]}: {exc!r}")
    return article


async def scrape_all(articles: list) -> list:
    sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=SCRAPE_CONCURRENCY, ssl=False)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, connector=connector) as session:
        tasks = [_scrape_one(session, a, sem) for a in articles]
        results = await asyncio.gather(*tasks)
    scraped = sum(1 for a in results if a["content"])
    print(f"[scrape] {scraped}/{len(results)} articles with content")
    return results
