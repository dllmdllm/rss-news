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
from src.hk_text import to_hk

from src.analyse import _strip_fences
from src.minimax_client import (
    MINIMAX_API_KEY,
    post_messages,
    should_retry as _should_retry,
)

OUTPUT_PATH          = Path(__file__).parent.parent / "docs" / "data" / "entities.json"
ENTITY_MIN_ARTICLES  = 3
ENTITY_WINDOW_HOURS  = 168    # 7 days
ENTITY_TYPES         = ("people", "companies", "places")
ENTITY_MAX_PER_TYPE  = 20
ENTITY_CONCURRENCY   = 3
ENTITY_MAX_ATTEMPTS  = 3
ENTITY_VERSION       = "e1"

# 別名合併表（2026-07-25）。同一個真實實體俾 AI 抽成幾個名，各自嘅 count
# 被稀釋——「天文台」21 篇 +「香港天文台」13 篇，合埋 34 先係真相，拆開就
# 一個排第二一個排第四。
#
# ⚠️ **一定要人手維護，唔可以用 substring 自動 merge。** 實測過真數據，
# 自動化會冧得好肉酸：
#     「東京都」contains「京都」   ← 兩個唔同城市
#     「天文台」contains「歐洲南方天文台」
#     「香港」contains「香港會議展覽中心」  ← 唔同粒度
# Key 係 (type, 別名)，value 係 canonical 名——連 type 一齊 scope，
# 免得跨類撞名（「香港海關」係 companies、「香港」係 places）。
#
# 只需要處理「合併之後會影響排名」嗰啲：ENTITY_MIN_ARTICLES = 3 已經濾走
# 長尾，count 1-2 嘅變體唔使理。
ENTITY_ALIASES: dict[tuple[str, str], str] = {
    ("companies", "香港天文台"): "天文台",
    ("companies", "港鐵公司"): "港鐵",
    ("companies", "海關"): "香港海關",
    # TVB 2026-07 正式更名做無綫集團，法定名係電視廣播有限公司——全部同一間。
    ("companies", "無綫"): "TVB",
    ("companies", "無綫集團"): "TVB",
    ("companies", "無綫電視"): "TVB",
    ("companies", "無綫集團有限公司"): "TVB",
    ("companies", "電視廣播"): "TVB",
    ("companies", "電視廣播有限公司"): "TVB",
    ("companies", "攜程"): "攜程集團",
    ("companies", "攜程集團有限公司"): "攜程集團",
    ("companies", "國家市場監管總局"): "市場監管總局",
    ("companies", "中國海警"): "中國海警局",
    ("companies", "聯邦調查局"): "美國聯邦調查局",
    ("companies", "中國公安部"): "公安部",
    # 地點只合併「同一地方嘅寫法差異」，唔合併粒度差異——「廣東東部」唔會
    # merge 落「廣東」，因為打風報道講嘅正正係東部沿岸，資訊會蒸發。
    ("places", "廣東東部沿岸"): "廣東東部",
    ("places", "大埔宏福苑"): "宏福苑",
    ("places", "中國內地"): "內地",
}


def canonical_entity(etype: str, name: str) -> str:
    """簡繁 normalize + 別名合併。`build.py` 個 graph builder 都要 call 呢個
    ——之前佢淨係用 raw name（連 zhconv 都冇），所以「李慧琼」同「李慧瓊」
    喺 graph.json 係兩個節點，同 entities.json 對唔上（2026-07-25 發現）。"""
    name = to_hk(str(name or "").strip())
    return ENTITY_ALIASES.get((etype, name), name)

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

    entity_articles: dict[tuple, set[str]] = {}

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
                # 簡繁 normalize 做 canonical key——AI extract 嘅 entity name
                # 有時會混雜簡體字（M2.7 出過「地区」漏網嘅 case），令同一個
                # 真實實體因為簡繁唔同拆散做幾個 entry、各自嘅 count 被稀釋，
                # 更難夠 ENTITY_MIN_ARTICLES 呢條門檻上榜。轉做 zh-hk，同全站
                # 文章內文/標題 normalize 嘅 convention 一致（2026-07-21 audit
                # finding）。呢個淨係解決簡繁變體，唔處理稱謂/別名變體（例如
                # 「美國總統特朗普」vs「特朗普」）——嗰類要靠 alias table，
                # 冇一個安全嘅自動 heuristic 唔會誤merge唔同實體，未做。
                name = canonical_entity(etype, name)
                key = (etype, name)
                if aid:
                    entity_articles.setdefault(key, set()).add(aid)

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
                # thinking disabled is critical here: a 200-token cap leaves no
                # room for an M3 reasoning phase before the JSON answer.
                raw, err, status = await post_messages(
                    session,
                    system=ENTITY_SUMMARY_PROMPT,
                    user_text=user_msg,
                    max_tokens=200,
                    timeout=30,
                    connect=15,
                    thinking={"type": "disabled"},
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
    cached        = _load_cache()
    if not entities:
        if cached.get("entities"):
            print("[entities] 0 qualifying entities this run — keeping existing cache")
            return
        print("[entities] No qualifying entities")
        _write_output([], articles)
        return
    cached_by_name = {(e["type"], e["name"]): e for e in (cached.get("entities") or [])}
    articles_map   = {a["id"]: a for a in articles}

    pending = []
    result  = []
    for e in entities:
        sig      = _entity_sig(e["name"], e["article_ids"])
        cached_e = cached_by_name.get((e["type"], e["name"]))
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
