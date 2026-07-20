"""Keyword watch — push a Telegram alert the moment a freshly-scraped
article matches a user-defined keyword.

Runs every build cycle (~20 min), same as breaking_alert.py — this is a
static site with no always-on listener, so "即刻" means "within one
build cycle of publication" (up to ~20 min), not true webhook-speed
real-time. A genuine push-the-moment-it's-published webhook would need a
separate always-on service watching the RSS feeds directly; this module
deliberately doesn't attempt that.

Purely rule-based (no MiniMax call) — keyword matching against
title/summary/tags, zero extra AI cost.

To change what's being watched, edit WATCH_KEYWORDS below and push;
takes effect on the next build (~20 min).
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from src.breaking_alert import TELEGRAM_BOT_TOKEN, _send_telegram

STATE_PATH = Path(__file__).parent.parent / "docs" / "data" / "keyword_alerts.json"

# 關鍵字之間係 OR 關係，每個都係 substring match（唔分大小寫）——
# 純字面比對，冇語義理解。想追蹤一個主題就要將相關字（品牌/產品/人名/
# 中英對照）全部列晒落嚟，例如淨係 "OpenAI" 唔會 match 到 "ChatGPT"。
WATCH_KEYWORDS: list[str] = [
    "OpenAI", "ChatGPT", "GPT-", "Sam Altman", "奧特曼",
    "Anthropic", "Claude AI",
    "Google", "Gemini AI", "DeepMind",
    "Meta AI", "Llama",
    # 淨用英文 "Apple"、唔加中文「蘋果」——快速通道對住嘅係綜合新聞
    # source（唔係科技台），「蘋果」會撞正生果價錢／蘋果日報舊聞呢類
    # 完全唔相關嘅新聞，誤鳴風險太高。
    "Apple", "庫克", "Tim Cook",
    "Microsoft", "微軟", "Satya Nadella",
    "Nvidia", "輝達", "黃仁勳",
]

FRESHNESS_HOURS      = 2    # 只揀呢個窗口內先出嘅文，避免每次 build 重新掃全部舊文
MAX_ALERTS_PER_BUILD = 5    # 防止太闊嘅關鍵字（例如「香港」）一次過洗版
STATE_TTL_HOURS      = 48   # prune 舊 alerted record


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[keyword] state load failed: {exc!r}")
    return {"alerted": {}}


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def _haystack(article: dict) -> str:
    tags = " ".join(article.get("tags") or [])
    return f"{article.get('title', '')} {article.get('summary', '')} {tags}".lower()


def detect_keyword_matches(articles: list, alerted_ids: set, *, now: datetime | None = None) -> list[dict]:
    """Return up to MAX_ALERTS_PER_BUILD fresh articles matching a watched
    keyword, newest first. Each result carries which keyword hit it."""
    if not WATCH_KEYWORDS:
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)
    keywords = [(kw, kw.lower()) for kw in WATCH_KEYWORDS if kw and kw.strip()]

    matches = []
    for a in articles:
        if not a.get("id") or a["id"] in alerted_ids or a.get("duplicate_of"):
            continue
        try:
            dt = datetime.fromisoformat(a.get("date", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if dt < cutoff:
            continue
        hay = _haystack(a)
        hit = next((kw for kw, kw_lower in keywords if kw_lower in hay), None)
        if hit:
            matches.append({**a, "_matched_keyword": hit})

    matches.sort(key=lambda a: a.get("date", ""), reverse=True)
    return matches[:MAX_ALERTS_PER_BUILD]


def _format_alert_text(article: dict) -> str:
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = [
        f"🔔 <b>關鍵字提醒</b>：{esc(article['_matched_keyword'])}",
        esc(article.get("title", "")),
    ]
    summary = str(article.get("summary") or "").strip()
    if summary:
        points = [p.strip() for p in summary.replace("\\n", "\n").split("\n") if p.strip()]
        lines.extend(f"・{esc(p.lstrip('・'))}" for p in points[:3])
    meta = " · ".join(filter(None, [article.get("category", ""), article.get("source", "")]))
    if meta:
        lines.append(esc(meta))
    url = article.get("url") or ""
    if url:
        lines.append(f'<a href="{esc(url)}">睇原文</a>')
    return "\n".join(lines)


async def send_keyword_alerts(articles: list) -> None:
    """Alert Telegram for articles matching WATCH_KEYWORDS."""
    state = _load_state()

    if not TELEGRAM_BOT_TOKEN or not WATCH_KEYWORDS:
        _save_state(state)   # ensure the file always exists
        return

    alerted = state.get("alerted") or {}
    matches = detect_keyword_matches(articles, set(alerted.keys()))

    if matches:
        async with aiohttp.ClientSession() as session:
            for a in matches:
                text = _format_alert_text(a)
                try:
                    status = await _send_telegram(session, text, photo_url=a.get("thumbnail") or "")
                    if 200 <= status < 300:
                        alerted[a["id"]] = a.get("date", "")
                        print(f"[keyword] Alerted ({a['_matched_keyword']}): {a.get('title', '')[:50]}")
                    else:
                        print(f"[keyword] Telegram returned {status}")
                except Exception as exc:
                    print(f"[keyword] Send failed: {exc!r}")

    cutoff_str = (datetime.now(timezone.utc) - timedelta(hours=STATE_TTL_HOURS)).isoformat()
    alerted = {aid: ts for aid, ts in alerted.items() if ts > cutoff_str}
    state["alerted"] = alerted
    _save_state(state)
    print(f"[keyword] {len(matches)} new alerts sent, {len(alerted)} tracked")
