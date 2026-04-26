import asyncio
import json
import re
from html import escape as _html_escape
from urllib.parse import urljoin

import aiohttp
import trafilatura
import zhconv
from bs4 import BeautifulSoup

from src.feeds import (
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


def _is_mingpao_url(url: str) -> bool:
    return "mingpao.com" in (url or "").lower()


def _is_mingpao_article(article: dict) -> bool:
    return str(article.get("source", "")).startswith("明報") or _is_mingpao_url(article.get("url", ""))


def _is_hk01_url(url: str) -> bool:
    return "hk01.com" in (url or "").lower()


def _is_oncc_url(url: str) -> bool:
    return "hk.on.cc" in (url or "").lower()


def _is_skypost_url(url: str) -> bool:
    return "skypost.hk" in (url or "").lower()


def _hk01_tokens_to_text(tokens: list) -> str:
    """Flatten HK01 htmlTokens paragraphs into plain text.
    Only 'text' tokens observed in practice; unknown types fall back to content."""
    if not tokens:
        return ""
    return "".join(t.get("content", "") for t in tokens if isinstance(t, dict))


def _build_hk01_content(html: str) -> str | None:
    """Render HK01 article content from its embedded __NEXT_DATA__ JSON so image
    order is preserved. HK01 is a Next.js app that ships an empty article body
    and hydrates client-side, so trafilatura sees only a handful of paragraphs
    pre-rendered for SEO and none of the inline images. Return assembled HTML
    fragment or None if the page shape is unexpected."""
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None

    article = (
        data.get("props", {})
        .get("initialProps", {})
        .get("pageProps", {})
        .get("article")
    ) or {}
    blocks = article.get("blocks") or []
    if not blocks:
        return None

    parts: list[str] = []
    def _emit_image(img: dict):
        url = (img or {}).get("cdnUrl")
        if not url:
            return
        caption = (img or {}).get("caption") or ""
        safe_url = _html_escape(url, quote=True)
        if caption:
            parts.append(
                f'<figure><img src="{safe_url}" alt="{_html_escape(caption)}">'
                f'<figcaption>{_html_escape(caption)}</figcaption></figure>'
            )
        else:
            parts.append(f'<img src="{safe_url}">')

    for block in blocks:
        btype = block.get("blockType")
        if btype == "summary":
            for para in block.get("summary") or []:
                text = (para or "").strip()
                if text:
                    parts.append(f"<p>{_html_escape(text)}</p>")
        elif btype == "image":
            _emit_image(block.get("image") or {})
        elif btype == "gallery":
            for img in block.get("images") or []:
                _emit_image(img)
        elif btype == "text":
            for para in block.get("htmlTokens") or []:
                text = _hk01_tokens_to_text(para).strip()
                if text:
                    parts.append(f"<p>{_html_escape(text)}</p>")
        # related / code / video / ads → skipped on purpose
    if not parts:
        return None
    return "<html><body>" + "".join(parts) + "</body></html>"


_ONCC_CONTAINER_SELECTORS = [
    "article",
    "#articleContent",
    "#article_content",
    ".articleContent",
    ".article_content",
    ".newsContent",
    ".news_content",
    ".content",
]

_ONCC_SKIP_RE = re.compile(
    r"(advert|banner|share|social|related|recommend|keyword|tag|nav|menu|breadcrumb|"
    r"video|player|comment|toolbar|button|date|time)",
    re.IGNORECASE,
)

_ONCC_TEXT_HINT_RE = re.compile(r"(paragraph|article|content|text|body|desc|intro)", re.IGNORECASE)
_ONCC_CAPTION_RE = re.compile(r"(caption|cap|desc|photo_text|phototext|txt|text)", re.IGNORECASE)


def _node_token(node) -> str:
    return " ".join(
        str(v)
        for v in (
            node.get("id", ""),
            " ".join(node.get("class", []) if isinstance(node.get("class"), list) else [node.get("class", "")]),
            node.name or "",
        )
        if v
    )


def _is_oncc_skip_node(node) -> bool:
    return bool(_ONCC_SKIP_RE.search(_node_token(node)))


def _normalise_oncc_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_oncc_text(text: str) -> str:
    text = _normalise_oncc_text(text)
    text = re.sub(
        r"^.*?\d{4}年\d{2}月\d{2}日\s+\d{1,2}:\d{2}\s+Tweet\s+東網電視\s+更多新聞短片\s*",
        "",
        text,
    )
    text = re.sub(r"\bTweet\s+東網電視\s+更多新聞短片\s*", "", text)
    text = re.sub(r"\s*上一則\s+下一則\s+on\.cc東網.*$", "", text)
    return _normalise_oncc_text(text)


def _split_oncc_paragraphs(text: str) -> list[str]:
    text = _clean_oncc_text(text)
    if not text:
        return []
    # on.cc sometimes stores the whole article as one text blob. Split on
    # Chinese sentence boundaries so the reader still gets readable paragraphs.
    if len(text) < 140 and len(re.findall(r"[。！？；]", text)) < 2:
        return [text]
    parts = [p.strip() for p in re.split(r"(?<=[。！？；])\s+", text) if p.strip()]
    return parts or [text]


def _oncc_image_url(img, base_url: str) -> str:
    for attr in (
        "src", "data-src", "data-original", "data-lazy-src",
        "data-url", "data-image", "data-actualsrc",
    ):
        val = (img.get(attr) or "").strip()
        if val:
            return urljoin(base_url, val)
    return ""


def _oncc_caption_for_image(img) -> str:
    for parent in [img.parent, img.parent.parent if img.parent else None]:
        if not parent:
            continue
        for candidate in parent.find_all(["figcaption", "span", "div", "p"], recursive=True):
            if candidate is img or candidate.find("img"):
                continue
            token = _node_token(candidate)
            if not _ONCC_CAPTION_RE.search(token):
                continue
            text = _normalise_oncc_text(candidate.get_text(" ", strip=True))
            if text:
                return text
    return _normalise_oncc_text(img.get("alt") or img.get("title") or "")


def _oncc_best_container(soup: BeautifulSoup):
    selector_nodes = []
    for selector in _ONCC_CONTAINER_SELECTORS:
        node = soup.select_one(selector)
        if node:
            selector_nodes.append(node)
            parent = node.parent
            while parent and parent.name not in {"body", "html", "[document]"}:
                selector_nodes.append(parent)
                parent = parent.parent

    candidates = selector_nodes + soup.find_all(["main", "section", "div"])
    if not candidates:
        return soup.body or soup
    unique = []
    seen_ids = set()
    for node in candidates:
        ident = id(node)
        if ident not in seen_ids and not _is_oncc_skip_node(node):
            seen_ids.add(ident)
            unique.append(node)
    return max(
        unique or candidates,
        key=lambda node: len(_clean_oncc_text(node.get_text(" ", strip=True))) + 600 * len(node.find_all("img")),
    )


def _build_oncc_content(html: str, url: str) -> str | None:
    """Extract on.cc article text and images in DOM order.

    on.cc pages are not RSS and often rely on gallery/lazy image markup. This
    parser intentionally follows the source DOM instead of asking trafilatura to
    infer structure, so inline photos stay close to their surrounding text.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup.find_all(["script", "style", "noscript", "iframe", "form"]):
        node.decompose()

    root = _oncc_best_container(soup)
    parts: list[str] = []
    seen_text: set[str] = set()
    seen_images: set[str] = set()

    def emit_text(text: str):
        for para in _split_oncc_paragraphs(text):
            if len(para) < 6:
                continue
            if para in seen_text:
                continue
            seen_text.add(para)
            parts.append(f"<p>{_html_escape(para)}</p>")

    def emit_image(img):
        src = _oncc_image_url(img, url)
        if not src or src in seen_images:
            return
        seen_images.add(src)
        caption = _oncc_caption_for_image(img)
        safe_src = _html_escape(src, quote=True)
        safe_alt = _html_escape(caption or img.get("alt") or "", quote=True)
        if caption:
            parts.append(
                f'<figure><img src="{safe_src}" alt="{safe_alt}">'
                f'<figcaption>{_html_escape(caption)}</figcaption></figure>'
            )
        else:
            parts.append(f'<img src="{safe_src}" alt="{safe_alt}">')

    def walk(node):
        if getattr(node, "name", None) is None:
            return
        if _is_oncc_skip_node(node):
            return
        if node.name == "img":
            emit_image(node)
            return
        if node.name in {"p", "h2", "h3", "blockquote", "li"}:
            if not node.find("img"):
                emit_text(node.get_text(" ", strip=True))
                return
        if node.name in {"div", "section"}:
            has_nested_blocks = node.find(
                ["p", "h2", "h3", "blockquote", "li", "figure", "img"],
                recursive=False,
            )
            token = _node_token(node)
            if not has_nested_blocks and _ONCC_TEXT_HINT_RE.search(token):
                emit_text(node.get_text(" ", strip=True))
                return
        for child in list(getattr(node, "children", [])):
            walk(child)

    walk(root)
    if not parts:
        return None
    # Strip tags with regex instead of reparsing via BeautifulSoup — we only
    # need the approximate character count to decide whether the extraction
    # was worthwhile.
    text_chars = len(re.sub(r"<[^>]+>", "", "".join(parts)).strip())
    if text_chars < 80 and not seen_images:
        return None
    return "<html><body>" + "".join(parts) + "</body></html>"


_SKYPOST_INLINE_IMAGE_RE = re.compile(r'\{\{hket:inline-image name="([^"]+)"\}\}')


def _skypost_hidden_text(soup: BeautifulSoup, field: str) -> str:
    node = soup.select_one(f".hiddenOG .{field}")
    return _normalise_oncc_text(node.get_text(" ", strip=True) if node else "")


def _build_skypost_content(html: str, url: str) -> str | None:
    """Extract SkyPost article content while preserving inline image order.

    SkyPost renders the article body as sequential <p> nodes, with inline image
    placeholders hidden inside display:none paragraphs. The actual image base
    path is exposed via hiddenOG.prefixHidden, so we can reconstruct inline
    <img> tags in DOM order instead of flattening the story.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    root = soup.select_one(".article-details-content-container")
    if not root:
        return None

    prefix = _skypost_hidden_text(soup, "prefixHidden")
    hero = soup.select_one(".article-details-img-container img")
    parts: list[str] = []

    def _emit_image(src: str, alt: str = ""):
        src = (src or "").strip()
        if not src:
            return
        safe_src = _html_escape(src, quote=True)
        safe_alt = _html_escape(alt or "", quote=True)
        if alt:
            parts.append(
                f'<figure><img src="{safe_src}" alt="{safe_alt}">'
                f'<figcaption>{_html_escape(alt)}</figcaption></figure>'
            )
        else:
            parts.append(f'<img src="{safe_src}" alt="{safe_alt}">')

    if hero and (hero.get("src") or hero.get("data-src")):
        _emit_image(hero.get("src") or hero.get("data-src") or "", hero.get("alt") or "")

    def walk(node):
        if getattr(node, "name", None) is None:
            return
        if node.name in {"script", "style", "noscript"}:
            return
        if node.name == "img":
            _emit_image(node.get("src") or node.get("data-src") or "", node.get("alt") or "")
            return
        if node.name == "p":
            raw = node.decode_contents() or ""
            names = _SKYPOST_INLINE_IMAGE_RE.findall(raw)
            text = _normalise_oncc_text(node.get_text(" ", strip=True))
            if names and not text:
                for name in names:
                    if prefix:
                        _emit_image(prefix.rstrip("/") + "/" + name, "")
                return
            if names and text:
                parts.append(f"<p>{_html_escape(text)}</p>")
                for name in names:
                    if prefix:
                        _emit_image(prefix.rstrip("/") + "/" + name, "")
                return
            if text:
                parts.append(f"<p>{_html_escape(text)}</p>")
            return
        for child in list(getattr(node, "children", [])):
            walk(child)

    walk(root)
    if not parts:
        return None
    content = "<html><body>" + "".join(parts) + "</body></html>"
    return content


def _extra_headers_for_url(url: str) -> dict:
    if not _is_mingpao_url(url):
        return {}
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.6",
        "Referer": "https://news.mingpao.com/",
    }


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


_INTRO_RESTORE_SOURCES = ("WeekendHK", "GoTrip")


def _restore_intro_from_description(html: str, content: str, title: str, source: str) -> str:
    """Restore a missing intro paragraph from og:description for known sites.

    Some sites keep a short lead paragraph in og:description and start the
    visible article body at the first heading. If extraction skips that lead,
    prepend it once here.
    """
    if not any(name in (source or "") for name in _INTRO_RESTORE_SOURCES):
        return content

    soup = BeautifulSoup(html or "", "html.parser")
    meta = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
    desc = _normalise_oncc_text(meta.get("content") if meta else "")
    if not desc or not content:
        return content

    body = BeautifulSoup(content, "html.parser")
    container = body.body or body
    body_text = _normalise_oncc_text(container.get_text(" ", strip=True))
    if not body_text:
        return content

    first_heading = ""
    for node in container.find_all(["h2", "h3", "h4"], recursive=True):
        text = _normalise_oncc_text(node.get_text(" ", strip=True))
        if text and text != _normalise_oncc_text(title):
            first_heading = text
            break
    if not first_heading:
        return content

    split_at = desc.find(first_heading)
    if split_at <= 0:
        return content
    intro = desc[:split_at].strip()
    if not intro:
        return content

    # Skip if the intro is already present near the top of the body.
    if intro in body_text[: max(len(intro) + 128, 256)]:
        return content

    wrapper = body.new_tag("p")
    wrapper.string = intro
    container.insert(0, wrapper)
    return str(body)


def _add_featured_image(content: str, thumbnail: str) -> str:
    """Prepend thumbnail as featured image if content has no inline images."""
    if thumbnail and '<img' not in content:
        img = f'<img src="{_html_escape(thumbnail, quote=True)}" style="max-width:100%;border-radius:6px;margin-bottom:1em">'
        # BeautifulSoup wraps fragments with <html><body>…</body></html>,
        # so insert after <body> to avoid being stripped by innerHTML assignment.
        if '<body>' in content:
            return content.replace('<body>', f'<body>{img}', 1)
        return img + content
    return content


def _to_hk_traditional(content: str) -> str:
    return zhconv.convert(content, "zh-hk")


def content_quality(content: str, *, source: str, fallback: str) -> dict:
    soup = BeautifulSoup(content or "", "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    images = len(soup.find_all("img"))
    chars = len(text)
    if chars >= 1200:
        score = 3
    elif chars >= 500:
        score = 2
    elif chars >= 150:
        score = 1
    else:
        score = 0
    return {
        "score": score,
        "chars": chars,
        "images": images,
        "source": source,
        "fallback": fallback,
    }


def _split_fallback_text(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    if text.count("・") >= 2:
        return ["・" + item.strip(" ・") for item in text.split("・") if item.strip(" ・")]

    sentences = [s.strip() for s in re.split(r"(?<=[。！？；])\s*", text) if s.strip()]
    return sentences or [text]


def _format_rss_fallback_html(rss: str) -> str:
    soup = BeautifulSoup(rss or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    parts = _split_fallback_text(text)
    if not parts:
        return ""
    return "".join(f"<p>{_html_escape(part)}</p>" for part in parts)


def _rss_fallback_content(
    article: dict,
    *,
    fallback: str,
    allow_minimal: bool = False,
) -> str | None:
    """Build readable article content from RSS text and thumbnail."""
    rss = _format_rss_fallback_html(article.get("rss_content") or "")
    thumb = article.get("thumbnail") or ""
    img_html = (
        f'<img src="{_html_escape(thumb, quote=True)}" '
        'style="max-width:100%;border-radius:6px;margin-bottom:1em">'
        if thumb else ""
    )
    if not (rss or img_html):
        if not allow_minimal:
            return None
        title = _html_escape(article.get("title") or "未能擷取全文")
        url = _html_escape(article.get("url") or "#", quote=True)
        rss = (
            f"<p><strong>{title}</strong></p>"
            "<p>暫時未能從來源擷取全文或 RSS 摘要。</p>"
            f'<p><a href="{url}" target="_blank" rel="noopener">閱讀原文</a></p>'
        )
        fallback = "minimal"

    content = img_html + rss
    if article["source"] in SIMPLIFIED_SOURCES:
        content = _to_hk_traditional(content)
    article["content"] = content
    article["content_quality"] = content_quality(
        content,
        source=article["source"],
        fallback=fallback,
    )
    return content


async def _urllib_fetch(url: str, extra_headers: dict | None = None) -> str | None:
    """Fetch using urllib.request in thread pool — bypasses Cloudflare TLS fingerprinting."""
    try:
        import urllib.request
        loop = asyncio.get_running_loop()
        def _fetch():
            headers = {**HTTP_HEADERS, **(extra_headers or {})}
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_FALLBACK_TIMEOUT) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        return await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        print(f"[WARN] urllib_fetch {url[:60]}: {exc!r}")
        return None


async def _cloudscraper_fetch(url: str, extra_headers: dict | None = None) -> str | None:
    """Bypass Cloudflare using cloudscraper (runs in thread pool)."""
    try:
        import cloudscraper
        loop = asyncio.get_running_loop()
        def _fetch():
            scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
            r = scraper.get(url, timeout=_FALLBACK_TIMEOUT, headers=extra_headers or None)
            return r.text
        return await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        print(f"[WARN] cloudscraper {url[:60]}: {exc!r}")
        return None


async def _fetch_html(session: aiohttp.ClientSession, url: str) -> str:
    async def _read(resp):
        # Bail on 4xx/5xx so trafilatura does not treat the error body as
        # article content — seen a 404 page leak into a card before.
        if resp.status >= 400:
            print(f"[WARN] scrape HTTP {resp.status} for {url[:60]}")
            return ""
        raw = await resp.read()
        charset = resp.charset or "utf-8"
        return raw.decode(charset, errors="replace")

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=_MAIN_TIMEOUT),
            headers=_extra_headers_for_url(url) or None,
        ) as resp:
            return await _read(resp)
    except aiohttp.ClientSSLError as exc:
        print(f"[WARN] scrape TLS verification failed for {url[:60]}: {exc!r}; retrying without verification")
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=_MAIN_TIMEOUT),
            ssl=False,
            headers=_extra_headers_for_url(url) or None,
        ) as resp:
            return await _read(resp)


def _process_html_sync(html: str, url: str, need_og_image: bool) -> tuple[str | None, str | None]:
    """Run every CPU-bound HTML transform in a single call so the caller can
    dispatch the whole thing to an executor and keep the event loop free for
    overlapping HTTP fetches. Returns (extracted_content, og_image_or_None)."""
    html = _expand_stheadline_galleries(html)
    html = _extract_noscript_imgs(html)
    html = _fix_picture_elements(html)
    html = _fix_lazy_images(html)

    # HK01 ships an empty article body and hydrates from __NEXT_DATA__, so
    # trafilatura never sees the inline images. Build content from the JSON
    # blocks instead to preserve the original image/text order; fall back to
    # trafilatura if the page shape is unexpected.
    content: str | None = None
    if _is_hk01_url(url):
        content = _build_hk01_content(html)
    elif _is_oncc_url(url):
        content = _build_oncc_content(html, url)
    elif _is_skypost_url(url):
        content = _build_skypost_content(html, url)
    if content is None:
        content = trafilatura.extract(
            html,
            output_format="html",
            include_images=True,
            include_links=False,
            favor_precision=True,
            no_fallback=False,
        )

    og_image: str | None = None
    if need_og_image:
        meta = trafilatura.extract_metadata(html)
        if meta and meta.image:
            og_image = meta.image
    return content, og_image


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
                html = await _fetch_html(session, article["url"])
                extra_headers = _extra_headers_for_url(article["url"])

                if not html and _is_mingpao_article(article):
                    print(f"[MINGPAO] empty/blocked response — trying urllib fallback")
                    html = await _urllib_fetch(article["url"], extra_headers)
                    if html and not _is_blocked(html):
                        print(f"[MINGPAO] urllib succeeded")
                    else:
                        print(f"[MINGPAO] trying cloudscraper fallback")
                        html = await _cloudscraper_fetch(article["url"], extra_headers)
                        if html and not _is_blocked(html):
                            print(f"[MINGPAO] cloudscraper succeeded")

                if _is_blocked(html):
                    print(f"[BLOCK] {article['source']} — trying urllib fallback")
                    html = await _urllib_fetch(article["url"], extra_headers)
                    if html and not _is_blocked(html):
                        print(f"[UNBLOCK] {article['source']} — urllib succeeded")
                    else:
                        print(f"[BLOCK] {article['source']} — trying cloudscraper fallback")
                        html = await _cloudscraper_fetch(article["url"], extra_headers)
                        if html and not _is_blocked(html):
                            print(f"[UNBLOCK] {article['source']} — cloudscraper succeeded")
                        else:
                            print(f"[BLOCK] {article['source']} — falling back to RSS content")
                            _rss_fallback_content(article, fallback="rss-blocked", allow_minimal=True)
                            return article

                loop = asyncio.get_running_loop()
                # All synchronous HTML work — regex rewrites, BeautifulSoup
                # parses, custom DOM walks and trafilatura.extract — runs in
                # one executor call so only a single thread hop per article.
                need_og = not article.get("thumbnail")
                content, og_image = await loop.run_in_executor(
                    None,
                    _process_html_sync,
                    html,
                    article["url"],
                    need_og,
                )

                if content:
                    content = _fix_graphic_tags(content)
                    content = _restore_intro_from_description(html, content, article.get("title", ""), article.get("source", ""))
                    content = _remove_leading_title(content, article.get("title", ""))
                    content = _add_featured_image(content, article.get("thumbnail") or "")
                    if article["source"] in SIMPLIFIED_SOURCES:
                        content = _to_hk_traditional(content)
                    article["content"] = content
                    article["content_quality"] = content_quality(
                        content,
                        source=article["source"],
                        fallback="none",
                    )
                else:
                    if _rss_fallback_content(article, fallback="rss-empty", allow_minimal=True):
                        print(f"[FALLBACK] {article['source']} — trafilatura returned no content; used RSS")

                if og_image and not article.get("thumbnail"):
                    article["thumbnail"] = og_image

                break  # success, no retry needed

            except Exception as exc:
                if attempt == 0:
                    await asyncio.sleep(3)  # wait before retry
                else:
                    print(f"[WARN] scrape {article['url'][:70]}: {exc!r}")
    return article


async def scrape_all(articles: list) -> list:
    sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=SCRAPE_CONCURRENCY)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, connector=connector) as session:
        tasks = [_scrape_one(session, a, sem) for a in articles]
        results = await asyncio.gather(*tasks)
    scraped = sum(1 for a in results if a.get("content"))
    print(f"[scrape] {scraped}/{len(results)} articles with content")
    return results
