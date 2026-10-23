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
from src.media_fields import clean_unit_markers
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup, NavigableString

from src.feeds import ENGLISH_SOURCES
from src.article_evidence import bind_current_body_hash
from src.enrichment_budget import legacy_source_limit
from src.minimax_client import MINIMAX_API_KEY, post_messages, should_retry

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "translated_content.json"

TRANSLATE_CONCURRENCY = 5
TRANSLATE_MAX_ATTEMPTS = 3
TRANSLATE_BACKOFF_BUDGET = 20.0
SAVE_CACHE_EVERY = 5   # incremental cache flush so a mid-run timeout keeps already-paid-for translations
# Admit an entire article or keep its full original body. A long review must
# not repeatedly ask for a 90k-token monolithic response under the 90s stage
# deadline. Cached translations are reused before applying this lower budget.
MAX_TRANSLATION_CHARS = 8000
MAX_TRANSLATION_OUTPUT_TOKENS = MAX_TRANSLATION_CHARS * 3
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
    "輸入每項有 text（要翻譯嘅文字片段）及 paragraph_context（只供理解語法同指代嘅完整段落）。"
    "輸出一個 JSON 陣列，每個元素只翻譯同一位置嘅 text 片段，"
    "按原文順序排列，陣列長度必須等於輸入片段數量。"
    "唔好將 paragraph_context 翻譯或重複輸出；片段可能由行內格式分開，"
    "用段落上下文保持前後語意連貫，但唔好將其他片段嘅意思搬入當前片段。"
    "唔好有任何其他文字、解釋、markdown 或思考過程。"
    "翻譯要自然流暢，符合新聞寫作風格，人名/地名/機構名用約定俗成嘅香港譯法"
    "（唔係台灣或者大陸譯法）；如果冇把握某個人名/地名/作品名嘅香港譯法，"
    "直接保留英文原文，唔好自己砌一個音譯；空白段落原樣輸出空字串。"
)

TRANSLATE_VERSION = "dom2-" + hashlib.md5(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]


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


def _validate_body(article: dict) -> None:
    content, expected = article.get('content'), article.get('content_hash')
    if content and expected and hashlib.sha256(content.encode('utf-8')).hexdigest() != expected:
        raise ValueError('body hash mismatch before translation: ' + article['id'])


def _replace_content(article: dict, content: str) -> None:
    # Translation/cache reuse and the known marker repair intentionally change
    # the reader body. Check existing bytes first; never bless a corrupt input.
    _validate_body(article)
    article['content'] = content
    bind_current_body_hash(article)


def extract_paragraphs(content: str) -> tuple[BeautifulSoup, list]:
    """Parse content and return (soup, tags) where tags is every block-level
    text tag that has real text, no nested <img> (an image-bearing paragraph
    is left untouched rather than risking mangling the image), and isn't
    implausibly long (over _MAX_PARAGRAPH_CHARS — see comment there)."""
    soup = BeautifulSoup(content or "", "html.parser")
    tags = []
    for tag in soup.find_all(_TEXT_TAGS):
        # A selected parent and child contain the same prose. Translate only
        # leaf blocks so quotations and list containers remain intact.
        if tag.find(_TEXT_TAGS):
            continue
        if tag.find("img"):
            continue
        text = tag.get_text(" ", strip=True)
        if text and len(text) <= _MAX_PARAGRAPH_CHARS:
            tags.append(tag)
    return soup, tags


def _replace_tag_text(tag, new_text: str):
    """Replace a tag's text content in place, keeping the tag itself (and
    thus the surrounding document structure/position) untouched."""
    if isinstance(tag, NavigableString):
        original = str(tag)
        leading = original[:len(original) - len(original.lstrip())]
        trailing = original[len(original.rstrip()):]
        tag.replace_with(NavigableString(leading + new_text.strip() + trailing))
    else:
        # Plain blocks have no inline elements. Never clear structured DOM.
        tag.string.replace_with(NavigableString(new_text))


def _translation_units(content: str):
    soup = BeautifulSoup(content or "", "html.parser")
    units = []
    for node in soup.descendants:
        if type(node) is not NavigableString or not str(node).strip():
            continue
        block = node.find_parent(_TEXT_TAGS)
        if (block is None or block.find("img")
                or len(block.get_text(" ", strip=True)) > _MAX_PARAGRAPH_CHARS):
            continue
        # Nearest block ownership visits every text occurrence once, including
        # a list item's direct prose before its nested sub-list.
        units.append(node)
    return soup, units


def _translation_omissions(content: str) -> bool:
    """Existing text-block guards preserve source prose rather than translate it."""
    soup = BeautifulSoup(content or "", "html.parser")
    for node in soup.descendants:
        if type(node) is not NavigableString or not str(node).strip():
            continue
        block = node.find_parent(_TEXT_TAGS)
        if block is not None and (block.find("img") or
                len(block.get_text(" ", strip=True)) > _MAX_PARAGRAPH_CHARS):
            return True
    return False


def _translation_state(article: dict, status: str, reason: str = "") -> str:
    article["translation_status"] = status
    article["translation_reason"] = reason
    return status


def _admission_reason(article: dict, texts: list[str]) -> str | None:
    if article.get("_paid_enrichment_deferred"):
        return "collection-budget"
    if sum(map(len, texts)) > MAX_TRANSLATION_CHARS:
        return "input-budget"
    return None


def _parse_translated_array(raw: str, expected: int) -> list[str] | None:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if (not isinstance(data, list) or len(data) != expected or
            any(not isinstance(item, str) or not item.strip() for item in data)):
        return None
    values = list(data)
    numbered = [re.match(r'^\[(\d+)\]\s+', value) for value in values]
    if len(values) >= 3 and all(numbered) and [int(m[1]) for m in numbered] == list(range(len(values))):
        values = [value[m.end():] for value, m in zip(values, numbered)]
    return values


async def _translate_article(
    session: aiohttp.ClientSession,
    article: dict,
    sem: asyncio.Semaphore,
    cache: dict,
    save_lock: asyncio.Lock,
    counter: list,
) -> str:
    _validate_body(article)
    content = article.get("content") or ""
    soup, tags = _translation_units(content)
    if not tags:
        return _translation_state(article, "unavailable", "no-translatable-text")

    texts = [tag.get_text(" ", strip=True) for tag in tags]
    if reason := _admission_reason(article, texts):
        return _translation_state(article, "deferred", reason)
    user_text = "\n".join(
        f"[{i}] " + json.dumps({
            "text": text,
            "paragraph_context": tag.find_parent(_TEXT_TAGS).get_text(" ", strip=True),
        }, ensure_ascii=False)
        for i, (tag, text) in enumerate(zip(tags, texts))
    )

    translated: list[str] | None = None
    async with sem:
        for attempt in range(TRANSLATE_MAX_ATTEMPTS):
            backoff = TRANSLATE_BACKOFF_BUDGET / TRANSLATE_MAX_ATTEMPTS * (attempt + 1)
            try:
                raw, err, status = await post_messages(
                    session,
                    system=SYSTEM_PROMPT,
                    user_text=user_text,
                    max_tokens=min(MAX_TRANSLATION_OUTPUT_TOKENS,
                                   max(1500, sum(len(t) for t in texts) * 3)),
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
                return _translation_state(article, "failed", "request-error")
            if err:
                if attempt < TRANSLATE_MAX_ATTEMPTS - 1 and should_retry(err, status):
                    await asyncio.sleep(backoff)
                    continue
                print(f"[translate] {article.get('source')} {article.get('id')}: API error {status} {err.get('type')}")
                return _translation_state(article, "failed", f"api-error-{status}")
            translated = _parse_translated_array(raw, len(texts))
            if translated:
                break
            print(f"[translate] {article.get('source')} {article.get('id')}: unparseable output (attempt {attempt + 1})")
            if attempt < TRANSLATE_MAX_ATTEMPTS - 1:
                await asyncio.sleep(backoff)
    if not translated:
        return _translation_state(article, "failed", "invalid-output")

    for tag, new_text in zip(tags, translated):
        if new_text.strip():
            _replace_tag_text(tag, new_text)

    _replace_content(article, str(soup))
    omitted = _translation_omissions(content)
    _translation_state(article, "partial" if omitted else "translated",
                       "unsupported-text-blocks" if omitted else "")
    cache[article["id"]] = {
        "content": article["content"],
        "source_hash": _source_hash(texts),
        "translated_hash": _source_hash([t.get_text(" ", strip=True) for t in _translation_units(article["content"])[1]]),
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
    return "translated"


async def translate_english_content(articles: list) -> dict:
    """Translate article bodies for ENGLISH_SOURCES in place. Cheap no-op
    (skipped entirely) when MINIMAX_API_KEY is unset, matching every other
    MiniMax-backed build step."""
    report = {"eligible": 0, "cached": 0, "translated": 0, "failed": 0,
              "deferred": 0, "unavailable": 0, "partial": 0}

    def finish(*, disabled=False):
        incomplete = sum(report[key] for key in ("failed", "deferred", "unavailable", "partial"))
        status = ("disabled" if disabled else "complete" if not incomplete else
                  "partial" if report["cached"] + report["translated"] else "failed")
        return {**report, "ok": not incomplete, "status": status}

    for article in articles:
        if article.get('source') in ENGLISH_SOURCES:
            _validate_body(article)
        if article.get('id') == 'f7bef5e0ede8' and article.get('source') == '9to5Mac' and article.get('content'):
            cleaned, changed = clean_unit_markers(article['content'])
            if changed:
                article['_unit_marker_repair_from'] = article['content']
                _replace_content(article, cleaned)
    if not MINIMAX_API_KEY:
        for a in articles:
            if a.get("source") in ENGLISH_SOURCES:
                report["eligible"] += 1
                _translation_state(a, "disabled", "provider-disabled")
        return finish(disabled=True)

    cache = load_cache()
    pending = []
    admitted_by_source = {}
    for a in articles:
        if a.get("source") not in ENGLISH_SOURCES:
            continue
        report["eligible"] += 1
        if not a.get("content"):
            report["unavailable"] += 1
            _translation_state(a, "unavailable", "source-body-unavailable")
            continue
        soup, tags = _translation_units(a["content"])
        if not tags:
            report["unavailable"] += 1
            _translation_state(a, "unavailable", "no-translatable-text")
            continue
        texts = [t.get_text(" ", strip=True) for t in tags]
        original_texts = texts
        if a.get('_unit_marker_repair_from'):
            original_texts = [t.get_text(' ', strip=True) for t in _translation_units(a['_unit_marker_repair_from'])[1]]
        cached = cache.get(a["id"])
        if (
            cached
            and cached.get("version") == TRANSLATE_VERSION
            and (cached.get("source_hash") == _source_hash(texts)
                 or cached.get("translated_hash") == _source_hash(texts)
                 or cached.get("translated_hash") == _source_hash(original_texts)
                 or (a.get("content_retention") and
                     _source_hash([t.get_text(" ", strip=True) for t in _translation_units(cached.get("content", ""))[1]]) == _source_hash(texts)))
        ):
            # Reuse translated text, not stale HTML that may predate repaired
            # RSS images. Keep the current sanitized media and document order.
            _, translated_tags = _translation_units(cached["content"])
            if len(translated_tags) == len(tags):
                for tag, translated_tag in zip(tags, translated_tags):
                    _replace_tag_text(tag, translated_tag.get_text(" ", strip=True))
                _replace_content(a, str(soup))
                if a.get('id') == 'f7bef5e0ede8' and cached.get('deterministic_unit_cleanup'):
                    a['_unit_marker_repair_from'] = a['content']
                # The audit confirmed this one cached unit-label echo. A
                # deterministic repair must not trigger paid retranslation.
                if a.get('id') == 'f7bef5e0ede8' and a.get('source') == '9to5Mac':
                    cleaned, changed = clean_unit_markers(a['content'])
                    if changed:
                        a['_unit_marker_repair_from'] = a['content']
                        _replace_content(a, cleaned)
                report["cached"] += 1
                omitted = _translation_omissions(a["content"])
                report["partial"] += int(omitted)
                _translation_state(a, "partial" if omitted else "cached",
                                   "unsupported-text-blocks" if omitted else "")
                continue
        if a.get('_unit_marker_repair_from'):
            # A known artifact repair cannot create a paid cache-miss path.
            report["deferred"] += 1
            _translation_state(a, "deferred", "deterministic-cleanup")
            continue
        if reason := _admission_reason(a, texts):
            report["deferred"] += 1
            _translation_state(a, "deferred", reason)
            continue
        source = a["source"]
        if admitted_by_source.get(source, 0) >= legacy_source_limit(source):
            report["deferred"] += 1
            _translation_state(a, "deferred", "collection-budget")
            continue
        admitted_by_source[source] = admitted_by_source.get(source, 0) + 1
        _translation_state(a, "pending", "translation-in-progress")
        pending.append(a)

    if not pending:
        return finish()

    sem = asyncio.Semaphore(TRANSLATE_CONCURRENCY)
    save_lock = asyncio.Lock()
    counter = [0]
    tasks = []
    try:
        async with aiohttp.ClientSession() as session:
            tasks = [asyncio.create_task(_translate_article(session, a, sem, cache,
                                                           save_lock, counter)) for a in pending]
            results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # wait_for cancels this stage. Drain every child before the final
        # atomic flush, including a tail of 1–4 completed paid translations.
        # Failed/cancelled work never enters the successful-result cache.
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for a in pending:
            if a.get("translation_status") == "pending":
                _translation_state(a, "deferred", "stage-cancelled")
        active_ids = {a["id"] for a in articles}
        save_cache({k: v for k, v in cache.items() if k in active_ids})
    for a, r in zip(pending, results):
        if isinstance(r, BaseException):
            print(f"[translate] {a.get('id')} crashed: {r!r}")
            _translation_state(a, "failed", "translation-error")
            report["failed"] += 1
        elif r in ("translated", "failed", "deferred", "unavailable"):
            report[r] += 1
            report["partial"] += int(a.get("translation_status") == "partial")
        else:
            report["failed"] += 1
            _translation_state(a, "failed", "translation-result-unavailable")
    print(f"[translate] {report['translated']}/{len(pending)} articles translated; "
          f"{report['failed']} failed, {report['deferred']} deferred")
    return finish()
