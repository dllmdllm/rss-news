import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

MINIMAX_API_KEY      = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL        = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
# Token Plan regularly rate-limits at 10 concurrent. Dropped to 5 after repeated
# 2062 errors caused majority of analyses to fail in a single build run.
ANALYSE_CONCURRENCY  = 5
BATCH_SIZE           = 5    # articles per batched API call — trades per-request overhead
                            # (incl. system prompt tokens) for a single larger call
MAX_ATTEMPTS         = 4    # total tries per call (incl. parse-fail retries)
MAX_BACKOFF_BUDGET   = 60.0 # hard cap on cumulative wait per call (seconds)
SAVE_CACHE_EVERY     = 20   # incremental cache flush so crash mid-run is not fatal

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "analyses.json"

SYSTEM_PROMPT = (
    "你係一個新聞分析助手。"
    "輸出一個 JSON 陣列，每篇新聞對應陣列內一個 object，按輸入編號順序排列，"
    "唔好有任何其他文字、解釋、markdown 或思考過程。"
    "陣列長度必須等於輸入新聞數量。\n"
    "每個 object 格式：\n"
    '{"summary":"單一字串（非array），5至8個重點，每點用「・」開頭，每點之間用換行符\\n分隔，每點唔超過10個字",'
    '"score":整數1到10（10=突發重大，5=一般新聞，1=普通資訊）,'
    '"tags":["標籤1","標籤2"]（最多3個中文標籤，唔帶#）,'
    '"sentiment":"positive"或"negative"或"neutral",'
    '"topic":"標準化話題名稱，唔超過10字"}'
)

# Derive version from the prompt hash so the cache auto-invalidates whenever
# SYSTEM_PROMPT changes — no manual bump needed.
ANALYSIS_VERSION = "p-" + hashlib.md5(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)


def _extract_text(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    return soup.get_text(separator=" ", strip=True)[:2000]


def _normalise_summary(raw) -> str:
    """Normalise AI-returned summary into newline-separated ・bullets.
    Handles: list of strings, single string with ・ delimiters missing newlines."""
    if isinstance(raw, list):
        items = [str(x).strip().lstrip("・ ").strip() for x in raw]
        return "\n".join("・" + i for i in items if i)
    text = str(raw or "").strip()
    if not text or "\n" in text:
        return text
    # No newlines: only split on ・ when it's clearly a bullet list (starts
    # with ・) — avoids breaking interdot names like 奧巴馬・侯賽因 in prose.
    if text.startswith("・") and text.count("・") >= 2:
        parts = [p.strip() for p in text.split("・") if p.strip()]
        return "\n".join("・" + p for p in parts)
    return text


def _normalise_parsed(data: dict) -> dict | None:
    """Coerce a parsed JSON object into our canonical analysis dict."""
    if not isinstance(data, dict):
        return None
    try:
        score_raw = data.get("score")
        try:
            score = max(1, min(10, int(score_raw)))
        except (TypeError, ValueError):
            score = 5
        sentiment = str(data.get("sentiment", "neutral")).lower()
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        tags_raw = data.get("tags") or []
        if isinstance(tags_raw, str):
            # Model occasionally returns "a,b,c" instead of ["a","b","c"]
            tags_raw = [t for t in re.split(r"[,，、\s]+", tags_raw) if t]
        elif not isinstance(tags_raw, list):
            tags_raw = []
        return {
            "summary":   _normalise_summary(data.get("summary")),
            "score":     score,
            "tags":      [str(t).strip().lstrip("#") for t in tags_raw[:3] if str(t).strip()],
            "sentiment": sentiment,
            "topic":     str(data.get("topic", "")).strip()[:20],
            "version":   ANALYSIS_VERSION,
        }
    except Exception:
        return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^\s*```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$",          "", text)
    return text


def _parse_analysis(raw: str) -> dict | None:
    """Parse a single JSON object from model output."""
    text = _strip_fences(raw)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return _normalise_parsed(json.loads(m.group(0)))
    except Exception:
        return None


def _parse_batch(raw: str, expected: int) -> list[dict | None] | None:
    """Parse a JSON array of analyses. Returns None on total shape mismatch
    so the caller retries or falls back. When the array shape matches but
    individual items are malformed, returns a list of same length with
    None at the failed slots — caller applies successes, fills failures
    via per-article fallback."""
    text = _strip_fences(raw)
    m_arr = re.search(r"\[.*\]", text, re.DOTALL)
    if m_arr:
        try:
            arr = json.loads(m_arr.group(0))
        except Exception:
            arr = None
        if isinstance(arr, list) and len(arr) == expected:
            return [_normalise_parsed(obj) for obj in arr]
    # Accept a bare object when batch size is 1 (some models drop the array)
    if expected == 1:
        single = _parse_analysis(text)
        if single:
            return [single]
    return None


def _needs_full_analysis(cached: dict) -> bool:
    """Return True if cached entry is stale/malformed and should be re-analysed."""
    if cached.get("score") is None:
        return True
    # Auto-invalidate when the prompt hash embedded in the cache entry no
    # longer matches the current ANALYSIS_VERSION hash.
    if cached.get("version") != ANALYSIS_VERSION:
        return True
    # Summary was stored as Python list repr (AI returned array, str()-ified)
    summary = cached.get("summary", "") or ""
    if summary.startswith("[") and summary.endswith("]") and "', '" in summary:
        return True
    return False


def _article_text(a: dict) -> str:
    """Choose the best available text source for analysis."""
    if a.get("content"):
        return _extract_text(a["content"])
    if a.get("rss_content"):
        return _extract_text(a["rss_content"])
    return a.get("title", "")


async def _post_messages(
    session:   aiohttp.ClientSession,
    user_text: str,
    max_tokens: int,
    timeout:   float,
) -> tuple[str, dict, int]:
    """Thin wrapper around the MiniMax messages endpoint.
    Returns (raw_text, error_dict, http_status)."""
    async with session.post(
        "https://api.minimax.io/anthropic/v1/messages",
        headers={
            "x-api-key":         MINIMAX_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type":      "application/json",
        },
        json={
            "model":      MINIMAX_MODEL,
            "max_tokens": max_tokens,
            "system":     SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_text}],
        },
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        status = resp.status
        data = await resp.json(content_type=None)
    err = data.get("error") or {}
    blocks = data.get("content") or []
    raw = next(
        (b.get("text", "").strip() for b in blocks if b.get("type") == "text"),
        ""
    )
    return raw, err, status


_RETRY_ERR_TYPES = {"overloaded_error", "rate_limit_error", "api_error"}


def _should_retry(err: dict, status: int) -> bool:
    return err.get("type") in _RETRY_ERR_TYPES or status == 429 or status >= 500


async def _apply_results(
    batch:     list,
    parsed:    list,
    cache:     dict,
    save_lock: asyncio.Lock,
    counter:   list,
):
    """Write parsed analyses back to articles + cache, save lazily.
    asyncio is single-threaded so the mutations below are atomic w.r.t.
    other coroutines; the lock only serializes the sync disk write."""
    for a, p in zip(batch, parsed):
        a["summary"]   = p["summary"]
        a["score"]     = p["score"]
        a["tags"]      = p["tags"]
        a["sentiment"] = p["sentiment"]
        a["topic"]     = p["topic"]
        cache[a["id"]] = p
    prev = counter[0]
    counter[0] += len(batch)
    if counter[0] // SAVE_CACHE_EVERY > prev // SAVE_CACHE_EVERY:
        async with save_lock:
            save_cache(cache)


async def _analyse_one(
    session:    aiohttp.ClientSession,
    article:    dict,
    sem:        asyncio.Semaphore,
    cache:      dict,
    save_lock:  asyncio.Lock,
    counter:    list,
) -> None:
    """Per-article fallback when a batch fails. Caller must ensure the
    article actually needs analysis (not cached)."""
    title = article.get("title", "")
    text  = _article_text(article)
    if not text.strip():
        return
    user_content = f"分析以下 1 篇新聞，返回長度 = 1 嘅 JSON 陣列：\n\n### 第 1 篇\n標題：{title}\n內容：{text}"
    async with sem:
        total_waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            try:
                raw, err, status = await _post_messages(session, user_content, max_tokens=500, timeout=30)
                if _should_retry(err, status) and attempt < MAX_ATTEMPTS - 1:
                    delay = min(2 ** (attempt + 2), MAX_BACKOFF_BUDGET - total_waited)
                    if delay <= 0:
                        break
                    await asyncio.sleep(delay)
                    total_waited += delay
                    continue
                if raw:
                    parsed = _parse_batch(raw, 1)
                    if parsed:
                        await _apply_results([article], parsed, cache, save_lock, counter)
                        return
                    if attempt < MAX_ATTEMPTS - 1:
                        delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                        if delay > 0:
                            await asyncio.sleep(delay)
                            total_waited += delay
                        continue
                    print(f"[WARN] analyse parse failed: {raw[:80]}")
                    return
                if err:
                    print(f"[WARN] analyse {article['url'][:60]}: {err}")
                return
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    print(f"[WARN] analyse {article['url'][:60]}: {exc!r}")
                    return
                delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                if delay <= 0:
                    return
                await asyncio.sleep(delay)
                total_waited += delay


async def _analyse_batch(
    session:   aiohttp.ClientSession,
    batch:     list,
    sem:       asyncio.Semaphore,
    cache:     dict,
    save_lock: asyncio.Lock,
    counter:   list,
) -> None:
    """Analyse up to BATCH_SIZE articles in a single API call.
    Falls back to per-article on batch-level failure."""
    if not batch:
        return

    parts = []
    for i, a in enumerate(batch, 1):
        parts.append(f"### 第 {i} 篇\n標題：{a.get('title', '')}\n內容：{_article_text(a)}")
    user_content = (
        f"分析以下 {len(batch)} 篇新聞，返回長度 = {len(batch)} 嘅 JSON 陣列：\n\n"
        + "\n\n".join(parts)
    )
    # Per-article output is ~200-350 tokens; budget generously and let the
    # server cap to the model ceiling. Timeout scales with batch size.
    max_tokens = min(4000, 400 * len(batch) + 200)
    timeout    = 30 + 10 * len(batch)

    batch_ok = False
    async with sem:
        total_waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            try:
                raw, err, status = await _post_messages(session, user_content, max_tokens, timeout)
                if _should_retry(err, status) and attempt < MAX_ATTEMPTS - 1:
                    delay = min(2 ** (attempt + 2), MAX_BACKOFF_BUDGET - total_waited)
                    if delay <= 0:
                        break
                    await asyncio.sleep(delay)
                    total_waited += delay
                    continue
                if raw:
                    parsed = _parse_batch(raw, len(batch))
                    if parsed:
                        # parsed has same length as batch; items may be None
                        # when a single element was malformed — apply the
                        # good ones and let per-article fallback fill gaps.
                        ok_arts = [a for a, p in zip(batch, parsed) if p]
                        ok_parsed = [p for p in parsed if p]
                        if ok_arts:
                            await _apply_results(ok_arts, ok_parsed, cache, save_lock, counter)
                        if len(ok_arts) == len(batch):
                            batch_ok = True
                        break
                    # Total shape mismatch — retry in batch mode, then fall
                    # back to per-article for the whole batch.
                    if attempt < MAX_ATTEMPTS - 1:
                        delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                        if delay > 0:
                            await asyncio.sleep(delay)
                            total_waited += delay
                        continue
                    print(f"[WARN] batch({len(batch)}) parse failed → per-article fallback")
                    break
                if err:
                    print(f"[WARN] batch({len(batch)}) analyse: {err}")
                break
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    print(f"[WARN] batch({len(batch)}) analyse: {exc!r}")
                    break
                delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
                total_waited += delay

    if batch_ok:
        return
    # Fallback: run each article as its own single-item batch. sem is already
    # released here, so each fallback call re-acquires it independently.
    for a in batch:
        if not a.get("summary"):
            await _analyse_one(session, a, sem, cache, save_lock, counter)


async def analyse_all(articles: list) -> list:
    if not MINIMAX_API_KEY:
        print("[analyse] Skipped — set MINIMAX_API_KEY")
        return articles

    cache = load_cache()

    # Hydrate cached articles up front; collect the rest for batched calls.
    pending: list = []
    for a in articles:
        aid = a["id"]
        if aid in cache and not _needs_full_analysis(cache[aid]):
            c = cache[aid]
            a["summary"]   = c.get("summary", "")
            a["score"]     = c.get("score", 5)
            a["tags"]      = c.get("tags", [])
            a["sentiment"] = c.get("sentiment", "neutral")
            a["topic"]     = c.get("topic", "")
        else:
            pending.append(a)

    cached_count = len(articles) - len(pending)
    print(f"[analyse] {cached_count} cached, {len(pending)} to generate (batch={BATCH_SIZE})")

    batches = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]

    sem       = asyncio.Semaphore(ANALYSE_CONCURRENCY)
    save_lock = asyncio.Lock()
    counter   = [0]
    async with aiohttp.ClientSession() as session:
        tasks = [_analyse_batch(session, b, sem, cache, save_lock, counter) for b in batches]
        await asyncio.gather(*tasks)

    # Evict stale cache entries for articles that have aged out
    active_ids = {a["id"] for a in articles}
    pruned = {k: v for k, v in cache.items() if k in active_ids}
    dropped = len(cache) - len(pruned)
    save_cache(pruned)
    done = sum(1 for a in articles if a.get("summary"))
    print(f"[analyse] {done}/{len(articles)} articles analysed"
          + (f" (pruned {dropped} stale cache entries)" if dropped else ""))
    return articles
