import asyncio
import json
import os
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

MINIMAX_API_KEY  = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL    = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
SUMMARISE_CONCURRENCY = 1  # Token Plan has strict RPM limits

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "summaries.json"

SYSTEM_PROMPT = (
    "你係一個新聞摘要助手。"
    "用繁體中文（香港口語）輸出2至3個重點，每個重點用「・」開頭，唔好超過100個字。"
    "直接輸出重點列表，唔需要前言或標題。"
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


async def _summarise_one(
    session: aiohttp.ClientSession,
    article: dict,
    sem: asyncio.Semaphore,
    cache: dict,
) -> dict:
    aid = article["id"]

    # Use cached summary if available
    if aid in cache:
        article["summary"] = cache[aid]
        return article

    if not article.get("content"):
        return article

    text = _extract_text(article["content"])
    if not text.strip():
        return article

    async with sem:
        try:
            await asyncio.sleep(1)  # respect Token Plan RPM limits
            async with session.post(
                "https://api.minimax.io/anthropic/v1/messages",
                headers={
                    "x-api-key":         MINIMAX_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type":      "application/json",
                },
                json={
                    "model":      MINIMAX_MODEL,
                    "max_tokens": 300,
                    "system":     SYSTEM_PROMPT,
                    "messages": [
                        {"role": "user", "content": f"請摘要以下文章：\n\n{text}"},
                    ],
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json(content_type=None)
                # Anthropic-compatible response: content[0].text
                content_blocks = data.get("content") or []
                summary = next(
                    (b.get("text", "").strip() for b in content_blocks if b.get("type") == "text"),
                    ""
                )
                if summary:
                    article["summary"] = summary
                    cache[aid] = summary
                elif data.get("error"):
                    print(f"[WARN] summarise {article['url'][:60]}: {data['error']}")
        except Exception as exc:
            print(f"[WARN] summarise {article['url'][:60]}: {exc!r}")

    return article


async def summarise_all(articles: list) -> list:
    if not MINIMAX_API_KEY:
        print("[summarise] Skipped — set MINIMAX_API_KEY")
        return articles

    cache = load_cache()
    new_count = sum(1 for a in articles if a["id"] not in cache and a.get("content"))
    cached_count = sum(1 for a in articles if a["id"] in cache)
    print(f"[summarise] {cached_count} cached, {new_count} to generate")

    sem = asyncio.Semaphore(SUMMARISE_CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        tasks = [_summarise_one(session, a, sem, cache) for a in articles]
        results = await asyncio.gather(*tasks)

    save_cache(cache)
    done = sum(1 for a in results if a.get("summary"))
    print(f"[summarise] {done}/{len(results)} articles have summaries")
    return results
