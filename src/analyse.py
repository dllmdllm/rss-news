import asyncio
import json
import os
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

MINIMAX_API_KEY      = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL        = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
ANALYSE_CONCURRENCY  = 10  # MiniMax-M2.7 allows 500 RPM

CACHE_PATH     = Path(__file__).parent.parent / "docs" / "data" / "analyses.json"
OLD_CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "summaries.json"

SYSTEM_PROMPT = (
    "你係一個新聞分析助手。"
    "輸出純JSON，唔好有任何其他文字，格式如下：\n"
    '{"summary":"5至8個重點，每點用「・」開頭，每點唔超過10個字，精簡如標題",'
    '"score":整數1到10（10=突發重大，5=一般新聞，1=普通資訊）,'
    '"tags":["標籤1","標籤2"]（最多3個中文標籤，唔帶#）,'
    '"sentiment":"positive"或"negative"或"neutral",'
    '"topic":"標準化話題名稱，唔超過10字"}'
)


def load_cache() -> dict:
    # Migrate old summaries.json on first run
    if not CACHE_PATH.exists() and OLD_CACHE_PATH.exists():
        cache = {}
        try:
            with open(OLD_CACHE_PATH, encoding="utf-8") as f:
                old = json.load(f)
            for aid, summary in old.items():
                cache[aid] = {
                    "summary":   summary,
                    "score":     None,
                    "tags":      [],
                    "sentiment": "neutral",
                    "topic":     "",
                }
        except Exception:
            pass
        return cache

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
        return {
            "summary":   str(data.get("summary", "")).strip(),
            "score":     score,
            "tags":      [str(t).strip() for t in (data.get("tags") or [])[:3] if str(t).strip()],
            "sentiment": sentiment,
            "topic":     str(data.get("topic", "")).strip()[:20],
        }
    except Exception:
        return None


def _needs_full_analysis(cached: dict) -> bool:
    """Return True if cached entry is missing score (migrated from old summaries.json)."""
    return cached.get("score") is None


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
        for attempt in range(3):
            try:
                if attempt > 0:
                    await asyncio.sleep(attempt * 3)  # 3s, 6s backoff on retry only
                async with session.post(
                    "https://api.minimax.io/anthropic/v1/messages",
                    headers={
                        "x-api-key":         MINIMAX_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "Content-Type":      "application/json",
                    },
                    json={
                        "model":      MINIMAX_MODEL,
                        "max_tokens": 400,
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
                    if err.get("type") == "overloaded_error" and attempt < 2:
                        await asyncio.sleep(10 * (attempt + 1))
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
                if attempt == 2:
                    print(f"[WARN] analyse {article['url'][:60]}: {exc!r}")

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

    save_cache(cache)
    done = sum(1 for a in results if a.get("summary"))
    print(f"[analyse] {done}/{len(results)} articles analysed")
    return results
