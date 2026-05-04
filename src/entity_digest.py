"""Entity digest — aggregate named entities from articles and generate AI summaries.

Outputs docs/data/entities.json (frontend-readable + cache).
"""
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from src.analyse import MINIMAX_API_KEY, MINIMAX_MODEL, _should_retry, _strip_fences

OUTPUT_PATH          = Path(__file__).parent.parent / "docs" / "data" / "entities.json"
ENTITY_MIN_ARTICLES  = 3
ENTITY_WINDOW_HOURS  = 168    # 7 days
ENTITY_TYPES         = ("people", "companies", "places")
ENTITY_MAX_PER_TYPE  = 20
ENTITY_CONCURRENCY   = 3
ENTITY_MAX_ATTEMPTS  = 3
ENTITY_VERSION       = "e1"

ENTITY_SUMMARY_PROMPT = (
    "你係一個新聞分析助手。根據以下新聞摘要，為指定嘅人物/機構/地點生成近況摘要。\n"
    "輸出一個 JSON object，唔好有任何其他文字。\n"
    '格式：{"summary":"一段話嘅近況描述（唔超過60字，中文）"}'
)


def _entity_sig(name: str, article_ids: list[str]) -> str:
    payload = name + "|" + "|".join(sorted(article_ids))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def _load_cache() -> dict:
    if OUTPUT_PATH.exists():
        try:
            data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def aggregate_entities(articles: list) -> list[dict]:
    """Walk articles, count entity appearances, return qualifying entities."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=ENTITY_WINDOW_HOURS)

    entity_articles: dict[tuple, list[str]] = {}

    n_dup = n_date_err = n_old = n_no_ent = n_ok = 0
    for a in articles:
        if a.get("duplicate_of"):
            n_dup += 1
            continue
        try:
            dt = datetime.fromisoformat(a.get("date", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < cutoff:
                n_old += 1
                continue
        except Exception:
            n_date_err += 1
            continue

        if not a.get("entities"):
            n_no_ent += 1

        aid      = a.get("id", "")
        entities = a.get("entities") or {}
        n_ok += 1
        for etype in ENTITY_TYPES:
            for raw in (entities.get(etype) or []):
                name = str(raw or "").strip()
                if not name or len(name) < 2:
                    continue
                key = (etype, name)
                if aid and aid not in entity_articles.get(key, []):
                    entity_articles.setdefault(key, []).append(aid)

    print(f"[entities] aggregate: {n_ok} ok, {n_dup} dup, {n_date_err} date-err, {n_old} old, {n_no_ent} no-entities")

    result = []
    for (etype, name), aids in entity_articles.items():
        if len(aids) >= ENTITY_MIN_ARTICLES:
            result.append({"type": etype, "name": name, "count": len(aids),
                           "article_ids": sorted(aids)})

    by_type: dict[str, list] = {t: [] for t in ENTITY_TYPES}
    for e in result:
        by_type[e["type"]].append(e)

    final = []
    for t in ENTITY_TYPES:
        by_type[t].sort(key=lambda x: x["count"], reverse=True)
        final.extend(by_type[t][:ENTITY_MAX_PER_TYPE])
    return final


async def _summarise_entity(
    session: aiohttp.ClientSession,
    entity: dict,
    articles_map: dict,
    sem: asyncio.Semaphore,
) -> str | None:
    aids     = entity["article_ids"][:8]
    snippets = []
    for aid in aids:
        a = articles_map.get(aid)
        if not a:
            continue
        title   = (a.get("title") or "").strip()
        summary = (a.get("summary") or "").replace("\n", " ").strip()[:80]
        snippets.append(f"・{title}：{summary}")
    if not snippets:
        return None

    type_label = {"people": "人物", "companies": "機構", "places": "地點"}.get(entity["type"], "")
    user_msg   = f"【{type_label}】{entity['name']}\n\n相關報導：\n" + "\n".join(snippets)

    async with sem:
        total_waited = 0.0
        for attempt in range(ENTITY_MAX_ATTEMPTS):
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
                        "max_tokens": 200,
                        "system":     ENTITY_SUMMARY_PROMPT,
                        "messages":   [{"role": "user", "content": user_msg}],
                    },
                    timeout=aiohttp.ClientTimeout(total=30, connect=15),
                ) as resp:
                    status = resp.status
                    data   = await resp.json(content_type=None)
                err    = data.get("error") or {}
                blocks = data.get("content") or []
                raw    = next(
                    (b.get("text", "").strip() for b in blocks if b.get("type") == "text"), ""
                )
                if _should_retry(err, status) and attempt < ENTITY_MAX_ATTEMPTS - 1:
                    delay = min(2 ** (attempt + 1), 20.0)
                    await asyncio.sleep(delay)
                    total_waited += delay
                    continue
                if not raw:
                    return None
                text = _strip_fences(raw)
                m    = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    return str(parsed.get("summary") or "").strip()[:120]
                return None
            except Exception as exc:
                if attempt == ENTITY_MAX_ATTEMPTS - 1:
                    print(f"[entities] {entity['name']}: {exc!r}")
                    return None
                await asyncio.sleep(2 ** attempt)
    return None


async def generate_entity_digests(articles: list) -> None:
    """Aggregate entities, generate AI summaries, write entities.json."""
    entities = aggregate_entities(articles)
    if not entities:
        print("[entities] No qualifying entities")
        _write_output([], articles)
        return

    cached        = _load_cache()
    cached_by_name = {e["name"]: e for e in (cached.get("entities") or [])}
    articles_map   = {a["id"]: a for a in articles}

    pending = []
    result  = []
    for e in entities:
        sig      = _entity_sig(e["name"], e["article_ids"])
        cached_e = cached_by_name.get(e["name"])
        if (
            isinstance(cached_e, dict)
            and cached_e.get("sig")     == sig
            and cached_e.get("version") == ENTITY_VERSION
            and cached_e.get("summary")
        ):
            e["summary"] = cached_e["summary"]
            e["sig"]     = sig
            e["version"] = ENTITY_VERSION
            result.append(e)
        else:
            e["sig"]     = sig
            e["version"] = ENTITY_VERSION
            pending.append(e)

    print(f"[entities] {len(result)} cached, {len(pending)} to summarise")

    if pending and MINIMAX_API_KEY:
        try:
            sem = asyncio.Semaphore(ENTITY_CONCURRENCY)
            async with aiohttp.ClientSession() as session:
                summaries = await asyncio.gather(*[
                    _summarise_entity(session, e, articles_map, sem)
                    for e in pending
                ], return_exceptions=True)
            for e, summary in zip(pending, summaries):
                if isinstance(summary, BaseException):
                    print(f"[entities] {e['name']}: unexpected {summary!r}")
                    summary = None
                e["summary"] = summary or ""
                result.append(e)
        except Exception as exc:
            print(f"[entities] summarise failed: {exc!r} — writing entities without summaries")
            for e in pending:
                if e not in result:
                    e["summary"] = ""
                    result.append(e)
    else:
        for e in pending:
            e["summary"] = ""
            result.append(e)

    _write_output(result, articles)


def _write_output(entities: list, articles: list):
    entities.sort(key=lambda e: (ENTITY_TYPES.index(e["type"]) if e["type"] in ENTITY_TYPES else 9,
                                 -e["count"], e["name"]))
    updated = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT")
    payload = {"updated": updated, "entities": entities}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(OUTPUT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, OUTPUT_PATH)
    print(f"[entities] {len(entities)} entities written")
