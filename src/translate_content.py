"""Full-text translation for English-language sources (ENGLISH_SOURCES).

fetch.py already translates titles; article bodies stayed in English "for
speed and reliability" (see feeds.py). This module translates the body too,
paragraph-by-paragraph so inline images/structure are untouched — only the
text inside block-level tags (p/h2/h3/h4/li/blockquote) gets replaced.

Must run BEFORE analyse.py: analyse's key_sentences are quoted verbatim from
article["content"], and the frontend highlights those quotes by substring
match against the displayed body. Translating content after analyse would
leave English key_sentences that can never match a now-Chinese body.

Cached by article id (like analyse.py/panel_digest.py) so an article that
stays in the RSS window across many 20-minute builds gets translated once,
not re-translated (and re-billed) every cycle. Keyed additionally by a hash
of the original English text, so if the source page's content genuinely
changes the cache still knows to re-translate rather than silently serving a
stale translation of different text.
"""
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup, NavigableString

from src.feeds import ENGLISH_SOURCES
from src.minimax_client import MINIMAX_API_KEY, post_messages, should_retry

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "translated_content.json"

TRANSLATE_CONCURRENCY = 5
TRANSLATE_MAX_ATTEMPTS = 3
TRANSLATE_BACKOFF_BUDGET = 20.0
SAVE_CACHE_EVERY = 5   # incremental cache flush so a mid-run timeout keeps already-paid-for translations
# Sanity cap, not a truncate-to length: a paragraph longer than this is left
# untranslated (original English) rather than sent truncated, which used to
# silently delete everything past the cutoff once the tag's content got
# replaced with the (short) translation — 2026-07-21 audit finding. Set well
# above any realistic news paragraph so this only ever excludes pathological
# content (e.g. a full transcript dumped in one <p>).
_MAX_PARAGRAPH_CHARS = 3000

_TEXT_TAGS = ("p", "h2", "h3", "h4", "li", "blockquote")

SYSTEM_PROMPT = (
    "你係一個新聞翻譯員，將英文新聞內文翻譯做香港繁體中文。"
    "輸出一個 JSON 陣列，每個元素對應輸入陣列同一位置嘅譯文，"
    "按原文順序排列，陣列長度必須等於輸入段落數量。"
    "唔好有任何其他文字、解釋、markdown 或思考過程。"
    "翻譯要自然流暢，符合新聞寫作風格，人名/地名/機構名用約定俗成嘅香港譯法"
    "（唔係台灣或者大陸譯法）；如果冇把握某個人名/地名/作品名嘅香港譯法，"
    "直接保留英文原文，唔好自己砌一個音譯；空白段落原樣輸出空字串。"
)

TRANSLATE_VERSION = "t-" + hashlib.md5(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)


def _source_hash(texts: list[str]) -> str:
    return hashlib.md5("\x1f".join(texts).encode("utf-8")).hexdigest()[:12]


def extract_paragraphs(content: str) -> tuple[BeautifulSoup, list]:
    """Parse content and return (soup, tags) where tags is every block-level
    text tag that has real text, no nested <img> (an image-bearing paragraph
    is left untouched rather than risking mangling the image), and isn't
    implausibly long (over _MAX_PARAGRAPH_CHARS — see comment there)."""
    soup = BeautifulSoup(content or "", "html.parser")
    tags = []
    for tag in soup.find_all(_TEXT_TAGS):
        if tag.find("img"):
            continue
        text = tag.get_text(" ", strip=True)
        if text and len(text) <= _MAX_PARAGRAPH_CHARS:
            tags.append(tag)
    return soup, tags


def _replace_tag_text(tag, new_text: str):
    """Replace a tag's text content in place, keeping the tag itself (and
    thus the surrounding document structure/position) untouched."""
    tag.clear()
    tag.append(NavigableString(new_text))


def _parse_translated_array(raw: str, expected: int) -> list[str] | None:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, list) or len(data) != expected:
        return None
    return [str(item) for item in data]


async def _translate_article(
    session: aiohttp.ClientSession,
    article: dict,
    sem: asyncio.Semaphore,
    cache: dict,
    save_lock: asyncio.Lock,
    counter: list,
) -> None:
    content = article.get("content") or ""
    soup, tags = extract_paragraphs(content)
    if not tags:
        return

    texts = [tag.get_text(" ", strip=True) for tag in tags]
    user_text = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))

    translated: list[str] | None = None
    async with sem:
        for attempt in range(TRANSLATE_MAX_ATTEMPTS):
            backoff = TRANSLATE_BACKOFF_BUDGET / TRANSLATE_MAX_ATTEMPTS * (attempt + 1)
            try:
                raw, err, status = await post_messages(
                    session,
                    system=SYSTEM_PROMPT,
                    user_text=user_text,
                    max_tokens=max(1500, sum(len(t) for t in texts) * 3),
                    timeout=45,
                    thinking={"type": "disabled"},
                )
            except Exception as exc:
                # A raw exception (timeout, connection reset, non-JSON error
                # body) used to bypass every remaining attempt entirely —
                # this is the same retry-on-exception pattern analyse.py
                # already uses (2026-07-21 audit finding).
                if attempt < TRANSLATE_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(backoff)
                    continue
                print(f"[translate] {article.get('source')} {article.get('id')}: crashed {exc!r}")
                return
            if err:
                if attempt < TRANSLATE_MAX_ATTEMPTS - 1 and should_retry(err, status):
                    await asyncio.sleep(backoff)
                    continue
                print(f"[translate] {article.get('source')} {article.get('id')}: API error {status} {err.get('type')}")
                return
            translated = _parse_translated_array(raw, len(texts))
            if translated:
                break
            print(f"[translate] {article.get('source')} {article.get('id')}: unparseable output (attempt {attempt + 1})")
            if attempt < TRANSLATE_MAX_ATTEMPTS - 1:
                await asyncio.sleep(backoff)
    if not translated:
        return

    for tag, new_text in zip(tags, translated):
        if new_text.strip():
            _replace_tag_text(tag, new_text)

    article["content"] = str(soup)
    cache[article["id"]] = {
        "content": article["content"],
        "source_hash": _source_hash(texts),
        "version": TRANSLATE_VERSION,
    }
    # Incremental flush so a mid-run timeout (build.py wraps this whole call
    # in asyncio.wait_for(90)) doesn't lose already-completed, already-paid-
    # for translations — previously the cache was only saved once, after
    # every task finished (2026-07-21 audit finding). asyncio is single-
    # threaded so the dict mutation above is atomic w.r.t. other coroutines;
    # the lock only serializes the sync disk write.
    prev = counter[0]
    counter[0] += 1
    if counter[0] // SAVE_CACHE_EVERY > prev // SAVE_CACHE_EVERY:
        async with save_lock:
            save_cache(cache)


async def translate_english_content(articles: list) -> None:
    """Translate article bodies for ENGLISH_SOURCES in place. Cheap no-op
    (skipped entirely) when MINIMAX_API_KEY is unset, matching every other
    MiniMax-backed build step."""
    if not MINIMAX_API_KEY:
        return

    cache = load_cache()
    pending = []
    for a in articles:
        if a.get("source") not in ENGLISH_SOURCES or not a.get("content"):
            continue
        _, tags = extract_paragraphs(a["content"])
        if not tags:
            continue
        texts = [t.get_text(" ", strip=True) for t in tags]
        cached = cache.get(a["id"])
        if (
            cached
            and cached.get("version") == TRANSLATE_VERSION
            and cached.get("source_hash") == _source_hash(texts)
        ):
            a["content"] = cached["content"]
            continue
        pending.append(a)

    if not pending:
        return

    sem = asyncio.Semaphore(TRANSLATE_CONCURRENCY)
    save_lock = asyncio.Lock()
    counter = [0]
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[_translate_article(session, a, sem, cache, save_lock, counter) for a in pending],
            return_exceptions=True,
        )
    for a, r in zip(pending, results):
        if isinstance(r, BaseException):
            print(f"[translate] {a.get('id')} crashed: {r!r}")
    translated_count = counter[0]

    active_ids = {a["id"] for a in articles}
    pruned = {k: v for k, v in cache.items() if k in active_ids}
    save_cache(pruned)
    print(f"[translate] {translated_count}/{len(pending)} articles translated")
