import asyncio
import json
import os
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

MINIMAX_API_KEY      = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL        = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
ANALYSE_CONCURRENCY  = 10   # MiniMax-M2.7 allows 500 RPM
MAX_ATTEMPTS         = 3    # total tries per article
MAX_BACKOFF_BUDGET   = 30.0 # hard cap on cumulative wait per article (seconds)

# Bump when SYSTEM_PROMPT changes materially — forces re-analysis of all
# cached entries so output format stays consistent across the site.
ANALYSIS_VERSION = 2

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "analyses.json"

SYSTEM_PROMPT = (
    "你係一個新聞分析助手。"
    "輸出純JSON，唔好有任何其他文字，格式如下：\n"
    '{"summary":"單一字串（非array），5至8個重點，每點用「・」開頭，每點之間用換行符\\n分隔，每點唔超過10個字",'
    '"score":整數1到10（10=突發重大，5=一般新聞，1=普通資訊）,'
    '"tags":["標籤1","標籤2"]（最多3個中文標籤，唔帶#）,'
    '"sentiment":"positive"或"negative"或"neutral",'
    '"topic":"標準化話題名稱，唔超過10字"}'
)


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
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))


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


def _parse_analysis(raw: str) -> dict | None:
    """Parse JSON from model output, tolerating markdown code fences."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$",          "", text, flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
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


def _needs_full_analysis(cached: dict) -> bool:
    """Return True if cached entry is stale/malformed and should be re-analysed."""
    if cached.get("score") is None:
        return True
    # Re-analyse when prompt / output format has changed
    if cached.get("version", 1) < ANALYSIS_VERSION:
        return True
    # Summary was stored as Python list repr (AI returned array, str()-ified)
    summary = cached.get("summary", "") or ""
    if summary.startswith("[") and summary.endswith("]") and "', '" in summary:
        return True
    return False


async def _analyse_one(
    session: aiohttp.ClientSession,
    article:  dict,
    sem:      asyncio.Semaphore,
    cache:    dict,
) -> dict:
    aid = article["id"]

    if aid in cache and not _needs_full_analysis(cache[aid]):
        c = cache[aid]
        article["summary"]   = c.get("summary", "")
        article["score"]     = c.get("score", 5)
        article["tags"]      = c.get("tags", [])
        article["sentiment"] = c.get("sentiment", "neutral")
        article["topic"]     = c.get("topic", "")
        return article

    title = article.get("title", "")

    # Use full content if available; fall back to RSS summary; last resort: title only
    if article.get("content"):
        text = _extract_text(article["content"])
    elif article.get("rss_content"):
        text = _extract_text(article["rss_content"])
    else:
        text = title  # analyse from title alone

    if not text.strip():
        return article

    async with sem:
        total_waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with session.post(
                    "https://api.minimax.io/anthropic/v1/messages",
                    headers={
                        "x-api-key":         MINIMAX_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "Content-Type":      "application/json",
                    },
                    json={
                        "model":      MINIMAX_MODEL,
                        "max_tokens": 500,
                        "system":     SYSTEM_PROMPT,
                        "messages": [{
                            "role":    "user",
                            "content": f"分析以下新聞：\n\n標題：{title}\n\n{text}",
                        }],
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json(content_type=None)
                    err  = data.get("error") or {}
                    if err.get("type") == "overloaded_error" and attempt < MAX_ATTEMPTS - 1:
                        delay = min(2 ** (attempt + 2), MAX_BACKOFF_BUDGET - total_waited)
                        if delay <= 0:
                            break
                        await asyncio.sleep(delay)
                        total_waited += delay
                        continue
                    content_blocks = data.get("content") or []
                    raw_text = next(
                        (b.get("text", "").strip() for b in content_blocks if b.get("type") == "text"),
                        ""
                    )
                    if raw_text:
                        parsed = _parse_analysis(raw_text)
                        if parsed:
                            article["summary"]   = parsed["summary"]
                            article["score"]     = parsed["score"]
                            article["tags"]      = parsed["tags"]
                            article["sentiment"] = parsed["sentiment"]
                            article["topic"]     = parsed["topic"]
                            cache[aid]           = parsed
                        else:
                            print(f"[WARN] analyse parse failed: {raw_text[:80]}")
                    elif err:
                        print(f"[WARN] analyse {article['url'][:60]}: {err}")
                    break
            except Exception as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    print(f"[WARN] analyse {article['url'][:60]}: {exc!r}")
                    break
                delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
                total_waited += delay

    return article


async def analyse_all(articles: list) -> list:
    if not MINIMAX_API_KEY:
        print("[analyse] Skipped — set MINIMAX_API_KEY")
        return articles

    cache     = load_cache()
    new_count = sum(
        1 for a in articles
        if (a["id"] not in cache or _needs_full_analysis(cache[a["id"]]))
        and (a.get("content") or a.get("rss_content") or a.get("title"))
    )
    cached_count = len(articles) - new_count
    print(f"[analyse] {cached_count} cached, {new_count} to generate")

    sem = asyncio.Semaphore(ANALYSE_CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks   = [_analyse_one(session, a, sem, cache) for a in articles]
        results = await asyncio.gather(*tasks)

    # Evict stale cache entries for articles that have aged out
    active_ids = {a["id"] for a in results}
    pruned = {k: v for k, v in cache.items() if k in active_ids}
    dropped = len(cache) - len(pruned)
    save_cache(pruned)
    done = sum(1 for a in results if a.get("summary"))
    print(f"[analyse] {done}/{len(results)} articles analysed"
          + (f" (pruned {dropped} stale cache entries)" if dropped else ""))
    return results
