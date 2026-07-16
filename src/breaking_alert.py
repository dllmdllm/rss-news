"""Detect breaking clusters and send Telegram alerts.

A cluster is "breaking" when ≥3 different sources cover the same story
within a 2-hour window.  State is persisted so repeated builds don't
re-alert for the same cluster.
"""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
WORKER_URL         = os.getenv("WORKER_URL", "")
NOTIFY_SECRET      = os.getenv("NOTIFY_SECRET", "")
SITE_BASE          = "https://dllmdllm.github.io/rss-news"

STATE_PATH            = Path(__file__).parent.parent / "docs" / "data" / "breaking_alerts.json"
BREAKING_WINDOW_HOURS = 2
BREAKING_MIN_SOURCES  = 3
STATE_TTL_HOURS       = 48   # prune alerted entries older than this


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            # Silent failure here re-alerts every already-notified cluster on
            # next build — log so corrupt state is at least visible.
            print(f"[breaking] state load failed: {exc!r}")
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
                "thumbnail":  best.get("thumbnail") or "",
                # breaking alert 跑喺 analyse 之後，best 已有 AI 摘要——
                # 帶埋落通知度俾 _format_alert_text 出 bullet points。
                "summary":    best.get("summary") or "",
            })

    return breaking


async def _send_worker_push(session: aiohttp.ClientSession, headline: str, article_id: str) -> None:
    if not WORKER_URL or not NOTIFY_SECRET:
        return
    url = f"{SITE_BASE}/article.html?id={article_id}"
    try:
        async with session.post(
            f"{WORKER_URL}/notify",
            headers={"Authorization": f"Bearer {NOTIFY_SECRET}"},
            json={"title": "🔴 突發新聞", "body": headline[:120], "url": url},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            body = await resp.text()
            print(f"[breaking] Worker push: {resp.status} {body}")
    except Exception as exc:
        print(f"[breaking] Worker push failed: {exc!r}")


async def _send_telegram(session: aiohttp.ClientSession, text: str, photo_url: str = "") -> int:
    """sendPhoto（caption）when photo_url is given, else plain sendMessage.

    sendPhoto's caption caps at 1024 chars — callers must keep text short
    (breaking alerts and the daily brief's headline+bullets both do). If
    Telegram rejects the photo (dead URL, unsupported format) fall back to
    sendMessage so a bad thumbnail never silently drops the notification.
    """
    if photo_url:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        async with session.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url, "caption": text, "parse_mode": "HTML"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status < 400:
                return resp.status
            print(f"[telegram] sendPhoto failed ({resp.status}), falling back to sendMessage")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with session.post(
        url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        return resp.status


def _format_alert_text(b: dict) -> str:
    """🔴 標題 + AI 摘要 bullets + 來源。摘要每點 ≤10 字（analyse prompt 保證），
    最多 5 點，遠低於 sendPhoto caption 嘅 1024 字上限。冇摘要（分析超時）
    就淨返標題＋來源，唔會 block 通知。"""
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = [f"🔴 <b>突發</b>：{esc(b['headline'])}"]
    points = [p.strip() for p in re.split(r"[\n・•]+", b.get("summary") or "") if p.strip()]
    lines.extend(f"・{esc(p)}" for p in points[:5])
    lines.append(f"來源：{esc('、'.join(b['sources'][:5]))}")
    return "\n".join(lines)


async def send_breaking_alerts(articles: list) -> None:
    """Alert Telegram for newly-detected breaking clusters."""
    state = _load_state()

    if not TELEGRAM_BOT_TOKEN:
        _save_state(state)   # ensure the file always exists
        return

    breaking = detect_breaking_clusters(articles)
    if not breaking:
        _save_state(state)   # ensure the file always exists
        return

    alerted  = state.get("alerted") or {}
    new_ones = [b for b in breaking if b["cid"] not in alerted]

    if new_ones:
        async with aiohttp.ClientSession() as session:
            for b in new_ones:
                text = _format_alert_text(b)
                try:
                    status = await _send_telegram(session, text, photo_url=b.get("thumbnail", ""))
                    if 200 <= status < 300:
                        alerted[b["cid"]] = b["date"]
                        print(f"[breaking] Alerted: {b['headline'][:50]}")
                        await _send_worker_push(session, b["headline"], b["article_id"])
                    else:
                        print(f"[breaking] Telegram returned {status}")
                except Exception as exc:
                    print(f"[breaking] Send failed: {exc!r}")

    # Always prune so state doesn't grow unboundedly, even when no new alerts.
    cutoff_str = (datetime.now(timezone.utc) - timedelta(hours=STATE_TTL_HOURS)).isoformat()
    alerted    = {cid: ts for cid, ts in alerted.items() if ts > cutoff_str}
    state["alerted"] = alerted
    _save_state(state)
    print(f"[breaking] {len(new_ones)} new alerts sent, {len(alerted)} tracked")
