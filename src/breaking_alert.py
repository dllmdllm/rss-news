"""Detect breaking clusters and send Telegram alerts.

A cluster is "breaking" when ≥3 different sources cover the same story
within a 2-hour window.  State is persisted so repeated builds don't
re-alert for the same cluster.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "1122095129")

STATE_PATH            = Path(__file__).parent.parent / "docs" / "data" / "breaking_alerts.json"
BREAKING_WINDOW_HOURS = 2
BREAKING_MIN_SOURCES  = 3
STATE_TTL_HOURS       = 48   # prune alerted entries older than this


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"alerted": {}}


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def detect_breaking_clusters(articles: list) -> list[dict]:
    """Return list of dicts for breaking clusters."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=BREAKING_WINDOW_HOURS)

    by_cluster: dict[str, list[dict]] = {}
    for a in articles:
        cid = a.get("cluster_id")
        if not cid or a.get("duplicate_of"):
            continue
        by_cluster.setdefault(cid, []).append(a)

    breaking = []
    for cid, members in by_cluster.items():
        recent = []
        for m in members:
            try:
                dt = datetime.fromisoformat(m.get("date", ""))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt >= cutoff:
                    recent.append(m)
            except Exception:
                pass
        sources = {m["source"] for m in recent if m.get("source")}
        if len(sources) >= BREAKING_MIN_SOURCES:
            best = max(members, key=lambda m: (m.get("score") or 0, m.get("date") or ""))
            breaking.append({
                "cid":        cid,
                "headline":   best.get("title", ""),
                "sources":    sorted(sources),
                "score":      best.get("score") or 0,
                "date":       best.get("date") or "",
                "article_id": best.get("id", ""),
            })

    return breaking


async def _send_telegram(session: aiohttp.ClientSession, text: str) -> int:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with session.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        return resp.status


async def send_breaking_alerts(articles: list) -> None:
    """Alert Telegram for newly-detected breaking clusters."""
    if not TELEGRAM_BOT_TOKEN:
        return

    breaking = detect_breaking_clusters(articles)
    if not breaking:
        return

    state   = _load_state()
    alerted = state.get("alerted") or {}
    new_ones = [b for b in breaking if b["cid"] not in alerted]
    if not new_ones:
        return

    async with aiohttp.ClientSession() as session:
        for b in new_ones:
            sources_str = "、".join(b["sources"][:5])
            text = (
                f"🔴 <b>突發</b>：{b['headline']}\n"
                f"來源：{sources_str}"
            )
            try:
                status = await _send_telegram(session, text)
                if 200 <= status < 300:
                    alerted[b["cid"]] = b["date"]
                    print(f"[breaking] Alerted: {b['headline'][:50]}")
                else:
                    print(f"[breaking] Telegram returned {status}")
            except Exception as exc:
                print(f"[breaking] Send failed: {exc!r}")

    # Prune entries older than TTL so state file doesn't grow unboundedly.
    cutoff_str = (datetime.now(timezone.utc) - timedelta(hours=STATE_TTL_HOURS)).isoformat()
    alerted    = {cid: ts for cid, ts in alerted.items() if ts > cutoff_str}
    state["alerted"] = alerted
    _save_state(state)
    print(f"[breaking] {len(new_ones)} new alerts sent, {len(alerted)} tracked")
