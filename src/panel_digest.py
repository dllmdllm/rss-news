"""Per-cluster panel digest — a second LLM pass that compares how
different outlets cover the same story.

Runs only on clusters that pass a threshold (size or max score), with
results cached by membership signature so unchanged clusters aren't
re-analysed across builds.
"""
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

import aiohttp

from src.analyse import (
    MINIMAX_API_KEY,
    MINIMAX_MODEL,
    _should_retry,
    _strip_fences,
)

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "panel_digests.json"
# This file doubles as both cache (signature/version per entry) and the
# frontend-readable artefact (just look up by cluster_id and pull `.digest`).

DIGEST_CONCURRENCY = 3            # smaller than per-article — clusters are heavier
DIGEST_MAX_ATTEMPTS = 3
DIGEST_BACKOFF_BUDGET = 45.0
DIGEST_MIN_CLUSTER_SIZE = 4       # clusters smaller than this only qualify via score
DIGEST_MIN_PEAK_SCORE = 8         # clusters with at least one score >= this qualify
DIGEST_PER_CLUSTER_MAX = 25       # cap clusters per build to bound LLM cost

PANEL_PROMPT = (
    "你係一個新聞編輯助手。輸入係幾個媒體就同一件事嘅分別報導，"
    "請對比佢哋嘅角度、共識同分歧。"
    "輸出一個 JSON object，唔好有任何其他文字、markdown 或思考過程。\n"
    "格式：\n"
    '{"headline":"事件一句話總結（中文，唔超過25字）",'
    '"consensus":"各媒體共識嘅基本事實（唔超過60字）",'
    '"angles":['
    '{"label":"焦點短描述（唔超過12字）","sources":["來源A","來源B"],"detail":"呢啲來源點寫法（唔超過40字）"}'
    "]（最多 4 個 angle，至少 2 個）,"
    '"tension":"分歧、矛盾或缺口（如有，唔超過60字；冇就空字串 \\"\\"）",'
    '"contradictions":['
    '{"claim_a":"來源A嘅具體說法","source_a":"來源名稱","claim_b":"來源B嘅具體說法","source_b":"來源名稱","type":"數字|時間|人物|地點"}'
    "]（若有可核實嘅事實矛盾就列出，最多 3 個；冇就空陣列 []）,"
    '"timeline":['
    '{"date":"YYYY-MM-DD","event":"事件描述（唔超過20字）"}'
    "]（若報導日期橫跨兩日或以上就列出事件發展時間軸，最多 6 個；單日或日期不明就空陣列 []）}"
)

DIGEST_VERSION = "d-" + hashlib.md5(PANEL_PROMPT.encode("utf-8")).hexdigest()[:8]


def _signature(cluster_id: str, article_ids: list[str]) -> str:
    """Stable hash of cluster membership — re-analyse only when it changes."""
    payload = cluster_id + "|" + "|".join(sorted(article_ids))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


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


def collect_qualifying_clusters(articles: list) -> list[tuple[str, list[dict]]]:
    """Walk articles and return cluster_id → member articles for clusters
    that pass the size or peak-score threshold. Sorted descending by
    (peak_score, size) so the LLM budget is spent on the strongest stories first."""
    by_cluster: dict[str, list[dict]] = {}
    for a in articles:
        if a.get("duplicate_of"):
            continue
        cid = a.get("cluster_id")
        if not cid:
            continue
        by_cluster.setdefault(cid, []).append(a)

    qualifying: list[tuple[int, int, str, list[dict]]] = []
    for cid, members in by_cluster.items():
        if len(members) < 2:
            continue
        peak_score = max((m.get("score") or 0) for m in members)
        if len(members) >= DIGEST_MIN_CLUSTER_SIZE or peak_score >= DIGEST_MIN_PEAK_SCORE:
            qualifying.append((peak_score, len(members), cid, members))

    qualifying.sort(reverse=True)
    return [(cid, members) for _peak, _size, cid, members in qualifying[:DIGEST_PER_CLUSTER_MAX]]


def _format_member(member: dict, idx: int) -> str:
    summary = (member.get("summary") or "").replace("\n", " ").strip()
    title = member.get("title", "").strip()
    source = member.get("source", "").strip()
    date = (member.get("date") or "")[:10]
    date_str = f"（{date}）" if date else ""
    return f"### 第 {idx} 篇\n來源：{source}{date_str}\n標題：{title}\n摘要：{summary}"


def _normalise_digest(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    headline = re.sub(r"\s+", " ", str(data.get("headline") or "")).strip()[:50]
    consensus = re.sub(r"\s+", " ", str(data.get("consensus") or "")).strip()[:120]
    tension = re.sub(r"\s+", " ", str(data.get("tension") or "")).strip()[:120]

    angles_raw = data.get("angles") or []
    angles = []
    if isinstance(angles_raw, list):
        for item in angles_raw[:4]:
            if not isinstance(item, dict):
                continue
            label = re.sub(r"\s+", " ", str(item.get("label") or "")).strip()[:24]
            detail = re.sub(r"\s+", " ", str(item.get("detail") or "")).strip()[:80]
            sources_raw = item.get("sources") or []
            if isinstance(sources_raw, str):
                sources_raw = [s for s in re.split(r"[,，、\s]+", sources_raw) if s]
            sources = [str(s).strip()[:20] for s in sources_raw if str(s).strip()][:5]
            if not (label and sources):
                continue
            angles.append({"label": label, "sources": sources, "detail": detail})

    contradictions_raw = data.get("contradictions") or []
    contradictions = []
    if isinstance(contradictions_raw, list):
        for item in contradictions_raw[:3]:
            if not isinstance(item, dict):
                continue
            claim_a  = re.sub(r"\s+", " ", str(item.get("claim_a")  or "")).strip()[:100]
            source_a = re.sub(r"\s+", " ", str(item.get("source_a") or "")).strip()[:20]
            claim_b  = re.sub(r"\s+", " ", str(item.get("claim_b")  or "")).strip()[:100]
            source_b = re.sub(r"\s+", " ", str(item.get("source_b") or "")).strip()[:20]
            ctype    = re.sub(r"\s+", " ", str(item.get("type")     or "")).strip()[:10]
            if claim_a and source_a and claim_b and source_b:
                contradictions.append({
                    "claim_a": claim_a, "source_a": source_a,
                    "claim_b": claim_b, "source_b": source_b,
                    "type": ctype,
                })

    timeline_raw = data.get("timeline") or []
    timeline = []
    if isinstance(timeline_raw, list):
        for item in timeline_raw[:6]:
            if not isinstance(item, dict):
                continue
            date  = re.sub(r"\s+", "", str(item.get("date")  or ""))[:10]
            event = re.sub(r"\s+", " ", str(item.get("event") or "")).strip()[:40]
            if date and event:
                timeline.append({"date": date, "event": event})

    if not headline or len(angles) < 2:
        return None
    return {
        "headline":       headline,
        "consensus":      consensus,
        "angles":         angles,
        "tension":        tension,
        "contradictions": contradictions,
        "timeline":       timeline,
        "version":        DIGEST_VERSION,
    }


def _parse_digest(raw: str) -> dict | None:
    text = _strip_fences(raw)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return _normalise_digest(json.loads(m.group(0)))
    except Exception:
        return None


async def _digest_one(
    session: aiohttp.ClientSession,
    cid: str,
    members: list[dict],
    sem: asyncio.Semaphore,
    out: dict,
):
    parts = [_format_member(m, i + 1) for i, m in enumerate(members)]
    user = (
        f"以下係 {len(members)} 個媒體就同一新聞嘅報導，請做對比分析：\n\n"
        + "\n\n".join(parts)
    )
    async with sem:
        total_waited = 0.0
        for attempt in range(DIGEST_MAX_ATTEMPTS):
            try:
                # Cluster digest needs a different system prompt than the
                # per-article schema in analyse.py — call the API directly.
                async with session.post(
                    "https://api.minimax.io/anthropic/v1/messages",
                    headers={
                        "x-api-key":         MINIMAX_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "Content-Type":      "application/json",
                    },
                    json={
                        "model":      MINIMAX_MODEL,
                        "max_tokens": 1100,
                        "system":     PANEL_PROMPT,
                        "messages":   [{"role": "user", "content": user}],
                    },
                    timeout=aiohttp.ClientTimeout(total=45, connect=20),
                ) as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
                err = data.get("error") or {}
                blocks = data.get("content") or []
                raw = next(
                    (b.get("text", "").strip() for b in blocks if b.get("type") == "text"),
                    ""
                )
                if _should_retry(err, status) and attempt < DIGEST_MAX_ATTEMPTS - 1:
                    delay = min(2 ** (attempt + 2), DIGEST_BACKOFF_BUDGET - total_waited)
                    if delay <= 0:
                        return
                    await asyncio.sleep(delay)
                    total_waited += delay
                    continue
                if not raw:
                    return
                parsed = _parse_digest(raw)
                if parsed:
                    out[cid] = parsed
                    return
                if attempt < DIGEST_MAX_ATTEMPTS - 1:
                    delay = min(2 ** attempt, DIGEST_BACKOFF_BUDGET - total_waited)
                    if delay > 0:
                        await asyncio.sleep(delay)
                        total_waited += delay
                    continue
                return
            except Exception as exc:
                if attempt == DIGEST_MAX_ATTEMPTS - 1:
                    print(f"[digest] {cid}: {exc!r}")
                    return
                delay = min(2 ** attempt, DIGEST_BACKOFF_BUDGET - total_waited)
                if delay <= 0:
                    return
                await asyncio.sleep(delay)
                total_waited += delay


async def generate_panel_digests(articles: list) -> dict:
    """Run panel-digest analysis for all qualifying clusters.

    Output file is keyed by cluster_id and serves both as the cache
    (signature + version on each entry → re-analyse only if changed)
    and the frontend-readable artefact. Returns the cluster_id → digest
    map for direct use by callers."""
    if not MINIMAX_API_KEY:
        print("[digest] Skipped — set MINIMAX_API_KEY")
        return {}

    qualifying = collect_qualifying_clusters(articles)
    if not qualifying:
        print("[digest] No qualifying clusters")
        save_cache({})
        return {}

    cache = load_cache()
    output: dict = {}
    pending: list[tuple[str, list[dict], str]] = []

    # Reuse cached digests when the cluster's membership signature is unchanged.
    for cid, members in qualifying:
        sig = _signature(cid, [m["id"] for m in members])
        cached = cache.get(cid)
        if (
            isinstance(cached, dict)
            and cached.get("signature") == sig
            and cached.get("version") == DIGEST_VERSION
            and isinstance(cached.get("digest"), dict)
        ):
            output[cid] = cached["digest"]
        else:
            pending.append((cid, members, sig))

    print(f"[digest] {len(output)} cached, {len(pending)} to generate "
          f"({len(qualifying)} qualifying clusters)")

    if pending:
        sem = asyncio.Semaphore(DIGEST_CONCURRENCY)
        new_results: dict = {}
        async with aiohttp.ClientSession() as session:
            tasks = [_digest_one(session, cid, members, sem, new_results)
                     for cid, members, _sig in pending]
            await asyncio.gather(*tasks)

        for cid, _members, sig in pending:
            digest = new_results.get(cid)
            if not digest:
                continue
            output[cid] = digest
            cache[cid] = {
                "signature": sig,
                "version":   DIGEST_VERSION,
                "digest":    digest,
            }

    # Drop cache entries for clusters no longer in the qualifying set.
    active_cids = {cid for cid, _ in qualifying}
    pruned = {cid: entry for cid, entry in cache.items() if cid in active_cids}
    save_cache(pruned)

    print(f"[digest] {len(output)}/{len(qualifying)} clusters with digest")
    return output
