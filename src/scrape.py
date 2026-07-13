import asyncio
import json
import re
from html import escape as _html_escape
from urllib.parse import urljoin

import aiohttp
import trafilatura
import zhconv
from bs4 import BeautifulSoup, NavigableString

from src.feeds import (
    HTTP_HEADERS,
    SCRAPE_CONCURRENCY,
    SIMPLIFIED_SOURCES,
)

# Per-request timeouts — fallbacks fire after the main aiohttp fetch fails or
# is blocked, so pages that do not respond quickly are unlikely to recover.
_MAIN_TIMEOUT     = 15
_FALLBACK_TIMEOUT = 10

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


def _is_tvb_url(url: str) -> bool:
    return "news.tvb.com" in (url or "").lower()


def _is_stheadline_url(url: str) -> bool:
    return "stheadline.com" in (url or "").lower()


def _is_nowsnews_url(url: str) -> bool:
    return "news.now.com" in (url or "").lower()


def _is_am730_url(url: str) -> bool:
    return "am730.com.hk" in (url or "").lower()


def _is_lookmedia_url(url: str) -> bool:
    """GoTrip and WeekendHK share 新傳媒's "look-child" WordPress theme."""
    u = (url or "").lower()
    return "gotrip.hk" in u or "weekendhk.com" in u


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
    description = (article.get("description") or "").strip()
    # HK01 store SEO meta `description` 經常會喺 ~70 字 cut off（例如 cut 喺
    # 「巴士於中環民光」），亦會將 lead 同 blocks[1] 開頭撈埋一齊再 truncate
    # （例如「...正式訪問。外交部發言人日前表示，夏巴」）。`teaser[0]` 係 HK01
    # 自己 curate 嘅 clean lead 段落，所以只要存在就優先用，唔好揀 longer。
    teaser_items = article.get("teaser") or []
    teaser_text = ""
    if isinstance(teaser_items, list):
        for item in teaser_items:
            if isinstance(item, str) and item.strip():
                teaser_text = item.strip()
                break
    lead_text = teaser_text or description
    if not blocks and not lead_text:
        return None

    parts: list[str] = []
    # 揀完 lead 之後仍要做 prefix check：個別 article 嘅 blocks[0] text 可能
    # 同 lead 重疊（HK01 偶爾會將 lead 入埋 blocks）。
    first_block_text = ""
    for block in blocks:
        if block.get("blockType") == "text":
            for para in block.get("htmlTokens") or []:
                txt = _hk01_tokens_to_text(para).strip()
                if txt:
                    first_block_text = txt
                    break
        if first_block_text:
            break
    if lead_text:
        lead_norm = re.sub(r"\s+", "", lead_text)
        first_norm = re.sub(r"\s+", "", first_block_text)
        is_prefix = bool(lead_norm) and bool(first_norm) and first_norm.startswith(lead_norm)
        if not is_prefix:
            parts.append(f"<p>{_html_escape(lead_text)}</p>")
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
    text = re.sub(r"\s*上一則\s+下一則(\s+on\.cc東網.*)?$", "", text)
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


# One <img> contributes ~600 "character equivalents" when ranking candidate
# article containers — empirically tuned so an image-heavy gallery node still
# beats a slightly longer adjacent paragraph wrapper. Set well above typical
# caption length (~150 chars) so a stray figure caption doesn't tip the scale.
_ONCC_IMAGE_SCORE_WEIGHT = 600


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
        key=lambda node: len(_clean_oncc_text(node.get_text(" ", strip=True))) + _ONCC_IMAGE_SCORE_WEIGHT * len(node.find_all("img")),
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
            # recursive=True (any descendant, not just direct children) —
            # the root container (e.g. #centerCTN.news_content) wraps several
            # levels of layout <div>s before reaching the real <p>/<img> nodes,
            # so a direct-children-only check saw none, matched the "content"
            # substring in its own id, and flattened the whole subtree via
            # get_text() before ever recursing down to the inline images.
            has_nested_blocks = node.find(
                ["p", "h2", "h3", "blockquote", "li", "figure", "img"],
                recursive=True,
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


# Nuxt 3's payload format ("devalue"): the array is a flat pool of values;
# objects/arrays reference sibling values by index instead of embedding them,
# so a plain json.loads() gives back index numbers where the real value is
# expected. _nuxt_deref walks a raw index into its resolved value, unwrapping
# the reactivity wrapper tuples Nuxt injects (["ShallowReactive", <idx>] etc).
_NUXT_REACTIVE_MARKERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef"}


def _nuxt_deref(raw: list, i, depth: int = 0):
    if depth > 40 or not isinstance(i, int) or i < 0 or i >= len(raw):
        return i
    val = raw[i]
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, list):
        if val and isinstance(val[0], str) and val[0] in _NUXT_REACTIVE_MARKERS:
            return _nuxt_deref(raw, val[1], depth + 1)
        if val and isinstance(val[0], str) and val[0] == "Set":
            return [_nuxt_deref(raw, j, depth + 1) for j in val[1:]]
        return [_nuxt_deref(raw, j, depth + 1) for j in val]
    if isinstance(val, dict):
        return {k: _nuxt_deref(raw, v, depth + 1) for k, v in val.items()}
    return val


def _build_tvb_content(html: str) -> str | None:
    """Extract TVB News article from its Nuxt __NUXT_DATA__ payload.

    TVB migrated from Next.js to Nuxt sometime around 2026-07 — the old
    __NEXT_DATA__-based parser silently stopped matching anything and every
    TVB article fell through to bare trafilatura against the SPA shell
    (near-empty text, no images). The article now lives in Nuxt's SSR state
    cache under a "article-detail-{id}-tc" key; `content`/`content_hk` is
    already fully-formed HTML with inline <img> in reading position, so this
    parser is mostly just: find that key, lightly wrap it, done.
    """
    m = re.search(
        r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.+?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        raw = json.loads(m.group(1))
        if not isinstance(raw, list) or len(raw) < 2:
            return None
        state = _nuxt_deref(raw, 1)
        data_obj = state.get("data") if isinstance(state, dict) else None
        if not isinstance(data_obj, dict):
            return None
        article = next(
            (v for k, v in data_obj.items() if "article" in k and isinstance(v, dict)),
            None,
        )
    except Exception:
        return None
    if not article:
        return None

    body_html = (article.get("content") or article.get("content_hk") or "").strip()
    if not body_html:
        return None

    parts: list[str] = []
    cover = article.get("cover") or []
    cover_url = (cover[0].get("url") or "").strip() if cover and isinstance(cover[0], dict) else ""
    # Skip the cover if it's already the first image inline in body_html —
    # same duplicate-hero-image concern _add_featured_image guards against
    # elsewhere, just checked here since this parser supplies both directly.
    if cover_url and cover_url not in body_html:
        parts.append(f'<img src="{_html_escape(cover_url, quote=True)}">')
    parts.append(body_html)
    return "<html><body>" + "".join(parts) + "</body></html>"


_NOWSNEWS_JUNK_STRINGS = (
    "抱歉，我們並不支援你正使用的瀏覽器",
    "為達至最佳瀏覽效果，請更新至最新的瀏覽器版本",
    "pccwmediaiapps@pccw.com",
)

# Suicide-prevention helpline boilerplate that HK outlets append to any story
# touching self-harm. Killed at the post-process step so it never reaches the
# AI summary (which would otherwise treat hotline numbers as article content).
_SUICIDE_HELPLINE_MARKERS = (
    "求助網站和熱線",
    "求助網站及熱線",
    "求助熱線",
    "情緒通",
    "情緒自救法",
    "防止自殺會",
    "精神健康支援熱線",
    "精神健康專線",
    "芷若園",
    "撒瑪利亞會熱線",
    "撒瑪利亞防止自殺",
    "社會福利署熱線",
    "生命熱線",
    "明愛向晴",
    "不要放棄你的生命",
    "請看看這些求助",
)


def _strip_suicide_helpline(content: str) -> str:
    """Remove helpline boilerplate paragraphs/headings without touching real body."""
    if not content or not any(m in content for m in _SUICIDE_HELPLINE_MARKERS):
        return content
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "li", "blockquote"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if any(marker in text for marker in _SUICIDE_HELPLINE_MARKERS):
            tag.decompose()
    return str(soup)


# Cross-promo / "related reading" inline links appended by HK / TW news sites.
# Prefix-match only (not substring) to avoid killing real prose that happens to
# mention "相關閱讀" mid-sentence — extremely rare but better safe than sorry.
_RELATED_READING_RE = re.compile(
    r"^\s*(相關|延伸|推薦|更多)\s*(閱讀|報[導道]|新聞)\s*[：:｜|・·]"
)


def _strip_related_reading(content: str) -> str:
    """Remove '相關閱讀：xxx' / '延伸閱讀：xxx' link paragraphs."""
    if not content or "閱讀" not in content and "報道" not in content and "報導" not in content and "新聞" not in content:
        return content
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "li", "blockquote"]):
        text = tag.get_text(" ", strip=True)
        if text and _RELATED_READING_RE.match(text):
            tag.decompose()
    return str(soup)

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


_LOOKMEDIA_SKIP_RE = re.compile(
    r"(read_btn|adbox|ad-slot|advert|social|share|related|lrec|code-block|sponsor)",
    re.IGNORECASE,
)


def _build_lookmedia_content(html: str) -> str | None:
    """GoTrip / WeekendHK article body walker.

    Two reasons trafilatura can't handle these pages:
      1. Multi-page listicles render inline as sequential `._page_N` divs;
         trafilatura keeps only part of them.
      2. Images are lazy-loaded (base64 placeholder + data-src) AND live on
         extension-less CDN URLs (imgs.gotrip.hk/...<hash> with no .jpg),
         which trafilatura's image validator rejects outright — live-tested
         0/7 images survived even with the real src restored.
    Walking the theme's article container directly keeps text and images in
    original reading order.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    root = None
    for sel in (".entry-content", ".js-post-gallery", "[itemprop=articleBody]"):
        root = soup.select_one(sel)
        if root:
            break
    if not root:
        return None
    for node in root.find_all(["script", "style", "noscript", "iframe", "form"]):
        node.decompose()

    parts: list[str] = []
    seen_imgs: set[str] = set()

    def emit_img(img):
        src = ""
        # data-src first: when a lazy attr exists it IS the real image, and
        # src is a placeholder. WeekendHK's placeholder is a plain http URL
        # (a theme asset, same for every img) — filtering data: URIs alone
        # collapsed all article photos into one deduped placeholder.
        for attr in ("data-src", "data-lazy-src", "data-original", "src"):
            val = (img.get(attr) or "").strip()
            if val and not val.lower().startswith("data:"):
                src = val
                break
        if not src or src in seen_imgs:
            return
        seen_imgs.add(src)
        alt = _normalise_oncc_text(img.get("alt") or "")
        safe_src = _html_escape(src, quote=True)
        if alt:
            parts.append(
                f'<figure><img src="{safe_src}" alt="{_html_escape(alt, quote=True)}">'
                f'<figcaption>{_html_escape(alt)}</figcaption></figure>'
            )
        else:
            parts.append(f'<img src="{safe_src}">')

    def walk(node):
        name = getattr(node, "name", None)
        if name is None:
            return
        token = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
        if _LOOKMEDIA_SKIP_RE.search(token):
            return
        if name == "img":
            emit_img(node)
            return
        if name in {"p", "h2", "h3", "h4", "blockquote", "li"}:
            imgs = node.find_all("img")
            if imgs:
                # Caption paragraphs: the visible text duplicates the image's
                # alt/figcaption, so emit only the images (with captions).
                for img in imgs:
                    emit_img(img)
            else:
                text = node.get_text(" ", strip=True)
                if text:
                    parts.append(f"<p>{_html_escape(text)}</p>")
            return
        if name == "figure":
            for img in node.find_all("img"):
                emit_img(img)
            return
        for child in list(getattr(node, "children", [])):
            # The lead paragraph sits as a BARE text node directly inside
            # `._page_1.read-full` (no <p> wrapper) — tag-only walking
            # silently dropped it.
            if isinstance(child, NavigableString):
                text = _normalise_oncc_text(str(child))
                if len(text) >= 6:
                    parts.append(f"<p>{_html_escape(text)}</p>")
                continue
            walk(child)

    walk(root)
    if not parts:
        return None
    text_chars = len(re.sub(r"<[^>]+>", "", "".join(parts)))
    if text_chars < 60 and not seen_imgs:
        return None
    return "<html><body>" + "".join(parts) + "</body></html>"


# am730 has no RSS body/description and no client-hydrated JSON payload
# (unlike HK01/TVB) — the real article text is genuinely there in server HTML
# under .article__body, but generic trafilatura extraction (317 chars in
# live testing) comes out far thinner than the ~350-500 chars actually
# present, apparently confused by the ad slots / related-listing gallery
# interleaved with the real paragraphs. Targeting the container directly and
# skipping known junk siblings (ads, "相關新聞" asides, unrelated property
# listing thumbnails) is more reliable than tuning trafilatura's heuristics.
_AM730_SKIP_CLASS_RE = re.compile(
    r"(adbox|custom_content|newsflash|picset|sharebar|article__foot|article__head)",
    re.IGNORECASE,
)


def _build_am730_content(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "html.parser")
    body = soup.select_one(".article__body")
    if not body:
        return None

    parts: list[str] = []
    for child in list(body.find_all(recursive=False)):
        if getattr(child, "name", None) is None:
            continue
        token = " ".join(child.get("class") or []) + " " + (child.get("id") or "")
        if _AM730_SKIP_CLASS_RE.search(token):
            continue
        if child.name == "figure":
            img = child.find("img")
            if not img:
                continue
            src = (img.get("src") or img.get("data-src") or "").strip()
            if not src:
                continue
            safe_src = _html_escape(src, quote=True)
            caption = _normalise_oncc_text(child.get_text(" ", strip=True))
            if caption:
                parts.append(
                    f'<figure><img src="{safe_src}"><figcaption>{_html_escape(caption)}</figcaption></figure>'
                )
            else:
                parts.append(f'<img src="{safe_src}">')
            continue
        text = _normalise_oncc_text(child.get_text(" ", strip=True))
        if text and len(text) >= 6:
            parts.append(f"<p>{_html_escape(text)}</p>")

    if not parts:
        return None
    text_chars = len(re.sub(r"<[^>]+>", "", "".join(parts)))
    if text_chars < 40:
        return None
    return "<html><body>" + "".join(parts) + "</body></html>"


def _extra_headers_for_url(url: str) -> dict:
    if not _is_mingpao_url(url):
        return {}
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.6",
        "Referer": "https://news.mingpao.com/",
    }


_STHEADLINE_TAIL_MARKERS = (
    "延伸閱讀",
    "相關閱讀",
    "推薦閱讀",
    "更多閱讀",
    "推介閱讀",
    "編輯推薦",
    "更多文章",
    "相關文章",
)


def _trim_stheadline_tail(content: str) -> str:
    """星島頭條 column articles end with the author byline (e.g.
    `<h3>唐耀賢<br/>好師傅創辦人</h3>`) followed by a strip of related-article
    thumbnails and an optional `<h3>延伸閱讀…</h3>` heading + more thumbs.

    trafilatura captures all that as part of the body. Trim by:
      1. cutting at the first heading containing a 延伸閱讀-style marker;
      2. stripping trailing standalone `<img>` tags (the related thumbnails
         sitting between the byline and the cut point have no captions, so
         they fall off naturally once the cut runs)."""
    if not content:
        return content
    soup = BeautifulSoup(content, "html.parser")
    root = soup.body if soup.body else soup
    children = list(root.children)
    cut_idx = None
    for i, ch in enumerate(children):
        if getattr(ch, "name", None) in ("h2", "h3", "h4"):
            text = ch.get_text(strip=True)
            if any(m in text for m in _STHEADLINE_TAIL_MARKERS):
                cut_idx = i
                break
    if cut_idx is not None:
        for ch in children[cut_idx:]:
            try:
                ch.extract()
            except Exception:
                pass
    while True:
        last = None
        for ch in reversed(list(root.children)):
            if getattr(ch, "name", None) is None:
                if not str(ch).strip():
                    continue
                last = ch
                break
            last = ch
            break
        if last is None or getattr(last, "name", "") != "img":
            break
        last.extract()
    return str(soup)


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


# Matches an <img> whose src is an inline data: URI — the classic lazy-load
# placeholder (1px transparent gif). The real URL sits in a data-src-style
# attribute on the same tag.
_IMG_DATA_PLACEHOLDER_RE = re.compile(
    r'<img([^>]*?)\ssrc=(["\'])data:image/[^"\']*\2([^>]*?)>',
    re.IGNORECASE,
)


def _fix_lazy_images(html: str) -> str:
    for attr in _LAZY_ATTRS:
        html = re.sub(
            rf'(<img(?![^>]*\ssrc=)[^>]*?){attr}=(["\'])([^"\']+)\2',
            r'\1src=\2\3\2',
            html,
            flags=re.IGNORECASE,
        )

    # Second pass: imgs that DO have a src, but it's a base64 placeholder
    # (GoTrip / WeekendHK lazy-load pattern: src="data:image/gif;base64,…"
    # + data-src="real url"). The negative lookahead above deliberately
    # skips srcful imgs, so without this pass the real URL never surfaces
    # and trafilatura sees only a 1×1 transparent gif.
    def _promote(m):
        before, quote, after = m.group(1), m.group(2), m.group(3)
        rest = before + " " + after
        for attr in _LAZY_ATTRS:
            m2 = re.search(rf'{attr}=(["\'])([^"\']+)\1', rest, re.IGNORECASE)
            if m2:
                return f'<img{before} src={quote}{m2.group(2)}{quote}{after}>'
        return m.group(0)

    return _IMG_DATA_PLACEHOLDER_RE.sub(_promote, html)


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


# 4 KB is enough to hit <head> + the start of <body> on a Cloudflare interstitial
# or 403 page (where the block phrase always appears). Avoids lower-casing the
# whole article body when it's actually a normal response.
_BLOCK_SAMPLE_CHARS = 4000


def _is_blocked(html: str) -> bool:
    sample = html[:_BLOCK_SAMPLE_CHARS].lower()
    return any(phrase in sample for phrase in _BLOCK_PHRASES)


def _remove_relative_images(content: str) -> str:
    """Remove <img> tags whose src is a relative path — they resolve to the
    article origin, not our GitHub Pages, so they always 404 on our site."""
    soup = BeautifulSoup(content, "html.parser")
    changed = False
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if src and not src.startswith(("http://", "https://", "data:")):
            img.decompose()
            changed = True
    return str(soup) if changed else content


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
            # Skip data: URIs — lazy-load placeholders (1px gif). Taking
            # src first used to grab the placeholder and throw away the
            # real URL sitting in data-src (GoTrip/WeekendHK pattern).
            candidates = (img.get("src"), img.get("data-src"),
                          img.get("data-lazy-src"), img.get("data-original"))
            url = next(
                (c for c in candidates if c and not c.strip().lower().startswith("data:")),
                None,
            )
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


def _dedupe_paragraphs(content: str) -> str:
    # trafilatura occasionally emits the same <p> twice for short articles
    # (e.g. mingpao single-paragraph stories) — drop later duplicates.
    if not content:
        return content
    soup = BeautifulSoup(content, "html.parser")
    seen: set[str] = set()
    for tag in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "blockquote", "li"]):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if len(text) < 20:
            continue
        if text in seen:
            tag.decompose()
        else:
            seen.add(text)
    return str(soup)


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
    if split_at < 0:
        # Heading not in og:description — the whole description is the intro
        intro = desc
    elif split_at == 0:
        return content
    else:
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
        return await asyncio.wait_for(loop.run_in_executor(None, _fetch), timeout=_FALLBACK_TIMEOUT + 5)
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

    # HK01/TVB ship empty article bodies hydrated client-side; build from
    # __NEXT_DATA__ JSON to preserve image order and get full text.
    content: str | None = None
    if _is_hk01_url(url):
        content = _build_hk01_content(html)
    elif _is_tvb_url(url):
        content = _build_tvb_content(html)
    elif _is_oncc_url(url):
        content = _build_oncc_content(html, url)
    elif _is_skypost_url(url):
        content = _build_skypost_content(html, url)
    elif _is_am730_url(url):
        content = _build_am730_content(html)
    elif _is_lookmedia_url(url):
        content = _build_lookmedia_content(html)

    if content is None:
        content = trafilatura.extract(
            html,
            output_format="html",
            include_images=True,
            include_links=False,
            favor_precision=True,
            no_fallback=False,
        )

    # Strip Now News browser-compat notice injected before the article body
    if content and _is_nowsnews_url(url):
        soup = BeautifulSoup(content, "html.parser")
        for p in soup.find_all("p"):
            t = p.get_text(strip=True)
            if any(j in t for j in _NOWSNEWS_JUNK_STRINGS) or t == "廣告":
                p.decompose()
        content = str(soup)

    if content and _is_stheadline_url(url):
        content = _trim_stheadline_tail(content)

    if content:
        content = _strip_suicide_helpline(content)
        content = _strip_related_reading(content)
        content = _dedupe_paragraphs(content)

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
    for attempt in range(2):
        async with sem:
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
                        elif not html:
                            # Both fallbacks returned nothing — give up, use RSS
                            _rss_fallback_content(article, fallback="rss-blocked", allow_minimal=True)
                            return article

                if html and _is_blocked(html):
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

                # Must run before _add_featured_image below — that call reads
                # article["thumbnail"] to decide the fallback image, so the
                # og:image discovered by this scrape has to land in the field
                # first. Doing it after (the previous order) meant any source
                # whose RSS carries no thumbnail (RTHK, 9to5Mac, GoTrip, HKEPC,
                # Unwire, WeekendHK …) never got its trafilatura-derived
                # og:image embedded in the article body — only the listing
                # card (which reads thumbnail directly) showed an image.
                if og_image and not article.get("thumbnail"):
                    article["thumbnail"] = og_image

                if content:
                    content = _fix_graphic_tags(content)
                    content = _remove_relative_images(content)
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

                return article  # success, no retry needed

            except Exception as exc:
                if attempt == 1:
                    print(f"[WARN] scrape {article['url'][:70]}: {exc!r}")
        # Semaphore released before sleeping so other articles can proceed
        if attempt == 0:
            await asyncio.sleep(3)
    return article


async def scrape_all(articles: list) -> list:
    sem = asyncio.Semaphore(SCRAPE_CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=SCRAPE_CONCURRENCY)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, connector=connector) as session:
        tasks = [_scrape_one(session, a, sem) for a in articles]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=240,  # 4-min budget; stuck threads keep running but we move on
            )
            results = [a if isinstance(a, dict) else articles[i] for i, a in enumerate(results)]
        except asyncio.TimeoutError:
            print("[scrape] timeout — using partial results")
            results = articles
    scraped = sum(1 for a in results if a.get("content"))
    print(f"[scrape] {scraped}/{len(results)} articles with content")
    return results
