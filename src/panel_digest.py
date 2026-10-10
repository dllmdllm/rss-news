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

from src.analyse import _article_text, _strip_fences
from src.minimax_client import (
    MINIMAX_API_KEY,
    MINIMAX_MODEL,
    post_messages,
    should_retry as _should_retry,
)

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "panel_digests.json"
# This file doubles as both cache (signature/version per entry) and the
# frontend-readable artefact (just look up by cluster_id and pull `.digest`).

DIGEST_CONCURRENCY = 5            # 7 typical clusters at 3-concurrent took ~110s
                                  # in 3 rounds; 5-concurrent fits in 2 rounds.
                                  # Still well under the 500 RPM token plan cap
                                  # since panel runs sequentially after analyse.
DIGEST_MAX_ATTEMPTS = 3
DIGEST_BACKOFF_BUDGET = 45.0
DIGEST_MIN_CLUSTER_SIZE = 4       # clusters smaller than this only qualify via score
DIGEST_MIN_PEAK_SCORE = 8         # clusters with at least one score >= this qualify
DIGEST_PER_CLUSTER_MAX = 25       # cap clusters per build to bound LLM cost

PANEL_PROMPT = (
    "你係一個新聞編輯助手。輸入係幾個媒體就同一件事嘅分別報導，"
    "請對比佢哋嘅角度、共識同分歧。"
    "矛盾必須係同一件事嘅互不相容事實；相同日期、相同說法、至少15%同15–20%都唔係矛盾。"
    "claim_a/claim_b必須等於quote_a/quote_b嘅逐字原文，並附正確文章ID；唔可捏造來源或原文。"
    "輸出一個 JSON object，唔好有任何其他文字、markdown 或思考過程。\n"
    "格式：\n"
    '{"headline":"事件一句話總結（中文，唔超過25字）",'
    '"consensus":"各媒體共識嘅基本事實（唔超過60字）",'
    '"angles":['
    '{"label":"焦點短描述（唔超過12字）","sources":["來源A","來源B"],"detail":"呢啲來源點寫法（唔超過40字）"}'
    "]（最多 4 個 angle，至少 2 個）,"
    '"tension":"分歧、矛盾或缺口（如有，唔超過60字；冇就空字串 \\"\\"）",'
    '"contradictions":['
    '{"claim_a":"來源A嘅具體說法","source_a":"來源名稱","claim_b":"來源B嘅具體說法","source_b":"來源名稱","article_id_a":"文章ID","quote_a":"原文逐字證據","article_id_b":"文章ID","quote_b":"原文逐字證據","type":"數字|時間|人物|地點"}'
    "]（若有可核實嘅事實矛盾就列出，最多 3 個；冇就空陣列 []）,"
    '"timeline":['
    '{"date":"YYYY-MM-DD","event":"事件描述（唔超過20字）"}'
    "]（若報導日期橫跨兩日或以上就列出事件發展時間軸，最多 6 個；單日或日期不明就空陣列 []）}"
)

DIGEST_VERSION = "d-" + hashlib.sha256(("evidence-v3|" + MINIMAX_MODEL + "|" + PANEL_PROMPT).encode("utf-8")).hexdigest()[:12]


def _signature(cluster_id: str, members: list) -> str:
    """Hash the actual model input, including source provenance and model version."""
    ordered = sorted(members, key=lambda m: str(m.get("id", "")) if isinstance(m, dict) else str(m))
    parts = [_format_member(m, i + 1) if isinstance(m, dict) else str(m)
             for i, m in enumerate(ordered)]
    payload = json.dumps([cluster_id, DIGEST_VERSION, MINIMAX_MODEL, parts], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    return f"### 第 {idx} 篇\n文章ID：{member.get('id', '')}\n網址：{member.get('url', '')}\n來源：{source}{date_str}\n標題：{title}\n摘要：{summary}\n原文：{_article_text(member)[:3000]}"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _quantity_claim(text: str):
    """Conservative comparable percentage bounds; different contexts stay unproven."""
    pattern = r"(?P<qual>至少|最少|不少於|不低於|超過|至多|最多|不多於|低於|at least\s*|>=|≥|<=|≤)?(?P<low>\d+(?:\.\d+)?)\s*(?:%|％)?\s*(?:[–—~至-]\s*(?P<high>\d+(?:\.\d+)?))?\s*[%％]"
    matches = list(re.finditer(pattern, text, re.I))
    if len(matches) != 1:
        return None
    match = matches[0]
    low = float(match["low"])
    high = float(match["high"]) if match["high"] else low
    qual = (match["qual"] or "").strip().casefold()
    if qual in {"至少", "最少", "不少於", "不低於", "超過", "at least", ">=", "≥"}:
        high = float("inf")
    elif qual in {"至多", "最多", "不多於", "低於", "<=", "≤"}:
        low = float("-inf")
    context = _compact(text[:match.start()] + "#" + text[match.end():])
    return context, low, high


def _provably_conflicting(a: str, b: str) -> bool:
    # Estimates and tentative dates have no exact incompatible bounds.
    if any(re.search(r"大約|約|估計|預計|可能|暫定|左右|前後|approximately|about|around", value, re.I)
           for value in (a, b)):
        return False
    if _compact(a) == _compact(b):
        return False
    qa, qb = _quantity_claim(a), _quantity_claim(b)
    if qa and qb and qa[0] == qb[0]:
        return qa[2] < qb[1] or qb[2] < qa[1]
    # Exact calendar dates only. Approximate/relative dates cannot prove a conflict.
    pattern = r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日"
    da, db = list(re.finditer(pattern, a)), list(re.finditer(pattern, b))
    if len(da) == len(db) == 1:
        ma, mb = da[0], db[0]
        context_a = _compact(a[:ma.start()] + "#" + a[ma.end():])
        context_b = _compact(b[:mb.start()] + "#" + b[mb.end():])
        values_a = tuple(map(int, re.findall(r"\d+", ma[0])))
        values_b = tuple(map(int, re.findall(r"\d+", mb[0])))
        return context_a == context_b and len(values_a) == len(values_b) and values_a != values_b
    return False


def _validated_contradiction(item: dict, members: list[dict]) -> dict | None:
    """Require attributable verbatim claims and deterministic incompatible facts.

    Unproven differences remain in angles; they must not be labelled contradictions.
    Model-provided URLs are never trusted.
    """
    by_id = {str(m.get("id")): m for m in members}
    evidence = {}
    claims = []
    for side in ("a", "b"):
        aid = str(item.get(f"article_id_{side}") or "")
        member = by_id.get(aid)
        quote = str(item.get(f"quote_{side}") or "").strip()
        claim = str(item.get(f"claim_{side}") or "").strip()
        if not member or not quote or len(quote) > 100 or _compact(claim) != _compact(quote):
            return None
        if item.get(f"source_{side}") != member.get("source"):
            return None
        # Validate against precisely the text supplied to the model (no inferred evidence).
        supplied = [str(member.get("title") or ""), _article_text(member)[:3000]]
        if not any(_compact(quote) in _compact(text) for text in supplied):
            return None
        url = str(member.get("url") or "")
        if not re.match(r"^https?://[^\s]+$", url, re.I):
            return None
        evidence.update({f"article_id_{side}": aid, f"quote_{side}": quote, f"url_{side}": url})
        claims.append(quote)
    if evidence["article_id_a"] == evidence["article_id_b"] or not _provably_conflicting(*claims):
        return None
    return evidence


def _normalise_digest(data, members: list[dict] | None = None) -> dict | None:
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
            evidence = _validated_contradiction(item, members or [])
            if claim_a and source_a and claim_b and source_b and evidence:
                contradictions.append({
                    **evidence,
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


def _parse_digest(raw: str, members: list[dict] | None = None) -> dict | None:
    text = _strip_fences(raw)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return _normalise_digest(json.loads(m.group(0)), members)
    except Exception:
        return None


async def _digest_one(
    session: aiohttp.ClientSession,
    cid: str,
    members: list[dict],
    sem: asyncio.Semaphore,
    out: dict,
):
    members = sorted(members, key=lambda m: str(m.get("id", "")))
    parts = [_format_member(m, i + 1) for i, m in enumerate(members)]
    user = (
        f"以下係 {len(members)} 個媒體就同一新聞嘅報導，請做對比分析：\n\n"
        + "\n\n".join(parts)
    )
    async with sem:
        total_waited = 0.0
        for attempt in range(DIGEST_MAX_ATTEMPTS):
            try:
                raw, err, status = await post_messages(
                    session,
                    system=PANEL_PROMPT,
                    user_text=user,
                    max_tokens=1100,
                    timeout=45,
                    thinking={"type": "disabled"},
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
                parsed = _parse_digest(raw, members)
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
    qualifying = collect_qualifying_clusters(articles)
    if not MINIMAX_API_KEY:
        # Offline builds may retain only evidence that still matches current inputs.
        existing = load_cache()
        signatures = {cid: _signature(cid, members) for cid, members in qualifying}
        valid = {cid: entry for cid, entry in existing.items()
                 if isinstance(entry, dict) and entry.get("signature") == signatures.get(cid)
                 and cid in signatures and entry.get("version") == DIGEST_VERSION
                 and isinstance(entry.get("digest"), dict)}
        if valid != existing:
            save_cache(valid)
        print("[digest] Skipped — set MINIMAX_API_KEY")
        return {cid: entry["digest"] for cid, entry in valid.items()}

    if not qualifying:
        save_cache({})
        print("[digest] No qualifying clusters")
        return {}

    cache = load_cache()
    output: dict = {}
    pending: list[tuple[str, list[dict], str]] = []

    # Reuse cached digests when the cluster's membership signature is unchanged.
    for cid, members in qualifying:
        sig = _signature(cid, members)
        cached = cache.get(cid)
        if (
            isinstance(cached, dict)
            and cached.get("signature") == sig
            and cached.get("version") == DIGEST_VERSION
            and isinstance(cached.get("digest"), dict)
        ):
            output[cid] = cached["digest"]
        else:
            cache.pop(cid, None)  # Failed refresh must not republish obsolete evidence.
            pending.append((cid, members, sig))

    print(f"[digest] {len(output)} cached, {len(pending)} to generate "
          f"({len(qualifying)} qualifying clusters)")

    if pending:
        sem = asyncio.Semaphore(DIGEST_CONCURRENCY)
        new_results: dict = {}
        async with aiohttp.ClientSession() as session:
            tasks = [_digest_one(session, cid, members, sem, new_results)
                     for cid, members, _sig in pending]
            # return_exceptions=True so one cluster's failure doesn't cancel
            # siblings; successful digests are still written via the `out` dict.
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for (cid, _members, _sig), r in zip(pending, results):
            if isinstance(r, BaseException):
                print(f"[digest] {cid} crashed: {r!r}")

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
