"""Dead-source detection — catch feeds that quietly stop producing articles.

Motivation (2026-07-25): SkyPost 要聞 and Engadget 中文 both sat at 0
articles for over a month without anyone noticing. Nothing was broken
loudly enough to show up: the build stayed green, every stage reported
ok, and `_fetch_skypost()` returns `error=None` even when it collects
zero articles, so `articles.json` just carried `effective_count: 0`
forever. This is the same silent-failure class CLAUDE.md already
records for 東網娛樂 ("error=None 所以冇人發現") — the third time it
has bitten, hence a general mechanism rather than another per-fetcher
patch.

A single empty cycle means nothing (upstream hiccups, quiet news hours,
HTTP 304 on a slow feed), so a source has to stay empty for
ZERO_ALERT_AFTER_HOURS before it is called dead. Alerting follows
guardian.yml's shape: fire once on the healthy→dead transition, send a
🟢 when it comes back, and re-nag only every REMIND_EVERY_HOURS while
it stays dead.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from src.breaking_alert import TELEGRAM_BOT_TOKEN, _send_telegram

STATE_PATH = Path(__file__).parent.parent / "docs" / "data" / "source_health.json"

# A source must produce nothing for this long before we call it dead. Long
# enough to ride out an upstream outage or a slow news night, short enough
# that a genuinely dead feed surfaces the next day instead of next month.
ZERO_ALERT_AFTER_HOURS = 24
REMIND_EVERY_HOURS     = 24


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sources"), dict):
                return data
        except Exception as exc:
            print(f"[source-health] state load failed: {exc!r}")
    return {"sources": {}}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def evaluate_sources(source_stats: dict, *, now: datetime | None = None, state: dict | None = None) -> tuple[dict, list[dict]]:
    """Fold this build's per-source counts into the persistent health state.

    Returns (new_state, events) where each event is
    {"source", "kind": "dead"|"recovered", "hours"}. Pure bookkeeping —
    no I/O — so tests can drive it directly with a fake clock.
    """
    now = now or datetime.now(timezone.utc)
    state = state or {"sources": {}}
    tracked = dict(state.get("sources") or {})
    events: list[dict] = []

    for name, stats in (source_stats or {}).items():
        count = (stats or {}).get("effective_count")
        if count is None:
            count = (stats or {}).get("count", 0)
        entry = dict(tracked.get(name) or {})

        if count and count > 0:
            # Recovered — only worth announcing if we had called it dead.
            if entry.get("alerted_at"):
                events.append({"source": name, "kind": "recovered", "hours": 0.0})
            tracked.pop(name, None)
            continue

        zero_since = _parse(entry.get("zero_since")) or now
        entry["zero_since"] = zero_since.isoformat()
        hours = (now - zero_since).total_seconds() / 3600

        last_alert = _parse(entry.get("alerted_at"))
        due = (
            hours >= ZERO_ALERT_AFTER_HOURS
            and (last_alert is None or (now - last_alert).total_seconds() / 3600 >= REMIND_EVERY_HOURS)
        )
        if due:
            entry["alerted_at"] = now.isoformat()
            events.append({"source": name, "kind": "dead", "hours": hours})
        tracked[name] = entry

    return {"sources": tracked}, events


def _format(event: dict) -> str:
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    name = esc(event["source"])
    if event["kind"] == "recovered":
        return f"🟢 <b>來源恢復</b>：{name} 又有新文章"
    days = event["hours"] / 24
    span = f"{days:.1f} 日" if days >= 1 else f"{event['hours']:.0f} 小時"
    return (
        f"🟠 <b>來源斷更</b>：{name}\n"
        f"連續 {span} 抓唔到任何文章，可能係網站改版／關閉。"
    )


async def check_source_health(source_stats: dict, *, now: datetime | None = None) -> list[dict]:
    """Update the health state and push a Telegram note on any transition."""
    now = now or datetime.now(timezone.utc)
    state = _load_state()
    new_state, events = evaluate_sources(source_stats, now=now, state=state)
    _save_state(new_state)

    if not events:
        return events
    for e in events:
        print(f"[source-health] {e['kind']}: {e['source']} ({e['hours']:.0f}h)")
    if not TELEGRAM_BOT_TOKEN:
        return events

    async with aiohttp.ClientSession() as session:
        for e in events:
            try:
                await _send_telegram(session, _format(e))
            except Exception as exc:
                print(f"[source-health] send failed: {exc!r}")
    return events
