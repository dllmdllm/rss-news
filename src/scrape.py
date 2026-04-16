import asyncio
import re

import aiohttp
import trafilatura
import zhconv
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

from src.feeds import ENGLISH_SOURCES, SCRAPE_CONCURRENCY, SIMPLIFIED_SOURCES

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

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
    html = re.sub(r'<graphic([^>]*)></graphic>', lambda m: '<img' + m.group(1) + '>', html, flags=re.IGNORECASE)
    html = re.sub(r'<graphic([^>]*?)/>', lambda m: '<img' + m.group(1) + '>', html, flags=re.IGNORECASE)
    return html


def _remove_leading_title(content: str, title: str) -> str:
    """Remove leading <h1> if it duplicates the article title."""
    import re as _re
    soup = BeautifulSoup(content, "html.parser")
    h1 = soup.find("h1")
    if h1:
        # Normalise whitespace before comparing
        h1_text = _re.sub(r"\s+", " ", h1.get_text()).strip()
        title_clean = _re.sub(r"\s+", " ", title).strip()
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


def _translate_to_hk(html_content: str) -> str:
    """Translate English HTML content to HK Traditional Chinese paragraph by paragraph."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        translator = GoogleTranslator(source="auto", target="zh-TW")
        tags = soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "figcaption", "blockquote"])
        for tag in tags:
            text = tag.get_text(separator=" ", strip=True)
            if text and len(text) > 3:
                try:
                    translated = translator.translate(text[:4500])
                    if translated:
                        tag.clear()
                        tag.append(translated)
                except Exception:
                    pass
        result = str(soup)
        return zhconv.convert(result, "zh-hk")
    except Exception as exc:
        print(f"[WARN] translation error: {exc!r}")
        return html_content


async def _scrape_one(
    session: aiohttp.ClientSession,
    article: dict,
    sem: asyncio.Semaphore,
) -> dict:
    async with sem:
        for attempt in range(2):
            try:
                async with session.get(
                    article["url"], timeout=aiohttp.ClientTimeout(total=40)
                ) as resp:
                    raw = await resp.read()
                    charset = resp.charset or "utf-8"
                html = raw.decode(charset, errors="replace")

                if _is_blocked(html):
                    print(f"[BLOCK] {article['source']} — {article['url'][:60]}")
                    rss = article.get("rss_content") or ""
                    thumb = article.get("thumbnail") or ""
                    img_html = f'<img src="{thumb}" style="max-width:100%;border-radius:6px;margin-bottom:1em">' if thumb else ""
                    if rss or img_html:
                        content = img_html + rss
                        if article["source"] in SIMPLIFIED_SOURCES:
                            content = _to_hk_traditional(content)
                        article["content"] = content
                    return article

                html = _extract_noscript_imgs(html)
                html = _fix_lazy_images(html)

                content = trafilatura.extract(
                    html,
                    output_format="html",
                    include_images=True,
                    include_links=False,
                    favor_precision=True,
                    no_fallback=False,
                )

                if content:
                    content = _fix_graphic_tags(content)
                    content = _remove_leading_title(content, article.get("title", ""))
                    content = _add_featured_image(content, article.get("thumbnail") or "")
                    if article["source"] in SIMPLIFIED_SOURCES:
                        content = _to_hk_traditional(content)
                    elif article["source"] in ENGLISH_SOURCES:
                        content = _translate_to_hk(content)
                    article["content"] = content

                # Extract og:image thumbnail if not already set from RSS
                if not article.get("thumbnail"):
                    meta = trafilatura.extract_metadata(html)
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
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        tasks = [_scrape_one(session, a, sem) for a in articles]
        results = await asyncio.gather(*tasks)
    scraped = sum(1 for a in results if a["content"])
    print(f"[scrape] {scraped}/{len(results)} articles with content")
    return results
