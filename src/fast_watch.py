"""Fast keyword tripwire for the 3 highest-velocity sources (星島頭條,
am730, TVB 新聞) — title/RSS-only, no full-text scrape, no AI analysis,
so a keyword hit can reach Telegram in ~5 minutes instead of waiting for
the next full ~20-minute build.py cycle.

Runs as its own GitHub Actions workflow (fast-watch.yml) on GitHub-hosted
ubuntu — fully decoupled from update.yml / the self-hosted runner. This
module never touches docs/data and never commits to git, so it can't
collide with the main build's own pushes (a real risk in this repo — see
project_push_safety notes). "Seen" article ids persist across the
ephemeral 5-min runs via the GitHub Actions cache (same mechanism
guardian.yml already uses), not a repo commit.

Trade-off: this has no shared state with keyword_alert.py's own dedup
(docs/data/keyword_alerts.json, written by build.py), so a fast-lane hit
can occasionally be followed by a second, slower alert from the main
~20-min pipeline for the same article. Kept the message prefixes visually
distinct (⚡ vs 🔔) so an occasional duplicate is obviously not a bug;
not worth building cross-system dedup for a personal keyword watch.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from src.breaking_alert import TELEGRAM_BOT_TOKEN, _send_telegram
from src.feeds import HTTP_HEADERS, RSS_FEEDS
from src.fetch import _fetch_one
from src.keyword_alert import WATCH_KEYWORDS

WATCHED_SOURCES = {"星島頭條", "am730", "TVB 新聞"}
# cwd-relative — the workflow restores/saves this exact path via actions/cache.
STATE_PATH = Path("fast_watch_state.json")
FRESHNESS_HOURS = 2   # cold-start window; "seen" set prevents re-alerting after that
MAX_ALERTS_PER_RUN = 5
SEEN_CAP = 3000        # bound state.json size — plain id list, no dates to prune by


def _load_seen() -> set[str]:
    if STATE_PATH.exists():
        try:
            return set(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("seen") or [])
        except Exception as exc:
            print(f"[fast-watch] state load failed: {exc!r}")
    return set()


def _save_seen(seen: set[str]):
    ids = sorted(seen)[-SEEN_CAP:]
    STATE_PATH.write_text(json.dumps({"seen": ids}, ensure_ascii=False), encoding="utf-8")


def _match_keyword(article: dict) -> str | None:
    hay = f"{article.get('title', '')} {article.get('rss_content') or ''}".lower()
    for kw in WATCH_KEYWORDS:
        if kw and kw.lower() in hay:
            return kw
    return None


def _format_text(article: dict, keyword: str) -> str:
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = [
        f"⚡ <b>快訊關鍵字</b>：{esc(keyword)}",
        esc(article.get("title", "")),
        f"{esc(article.get('source', ''))} · 快速通道（未經全文/AI 分析）",
    ]
    url = article.get("url") or ""
    if url:
        lines.append(f'<a href="{esc(url)}">睇原文</a>')
    return "\n".join(lines)


async def _fetch_watched(session: aiohttp.ClientSession, cutoff: datetime) -> list[dict]:
    articles = []
    for feed_info in RSS_FEEDS:
        if feed_info["name"] not in WATCHED_SOURCES:
            continue
        try:
            batch, error, _not_modified = await _fetch_one(session, feed_info, cutoff, {})
        except Exception as exc:
            print(f"[fast-watch] {feed_info['name']} crashed: {exc!r}")
            continue
        if error:
            print(f"[fast-watch] {feed_info['name']}: {error}")
        articles.extend(batch)
    return articles


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        print("[fast-watch] skipped — no TELEGRAM_BOT_TOKEN")
        return
    if not WATCH_KEYWORDS:
        print("[fast-watch] skipped — WATCH_KEYWORDS empty")
        return

    seen = _load_seen()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)

    async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
        articles = await _fetch_watched(session, cutoff)
        fresh = [a for a in articles if a.get("id") and a["id"] not in seen]
        matched = [(a, kw) for a in fresh if (kw := _match_keyword(a))]
        matched.sort(key=lambda pair: pair[0].get("date", ""), reverse=True)

        sent = 0
        alerted_ids: set[str] = set()
        for article, keyword in matched[:MAX_ALERTS_PER_RUN]:
            try:
                status = await _send_telegram(
                    session, _format_text(article, keyword), photo_url=article.get("thumbnail") or ""
                )
                if 200 <= status < 300:
                    sent += 1
                    alerted_ids.add(article["id"])
                    print(f"[fast-watch] Alerted ({keyword}): {article.get('title', '')[:50]}")
                else:
                    print(f"[fast-watch] Telegram returned {status}")
            except Exception as exc:
                print(f"[fast-watch] Send failed: {exc!r}")

    # 唔好將 send 失敗／未過 MAX_ALERTS_PER_RUN cap 嘅 matched article 都計
    # 做「已讀」——`seen` 係唯一嘅去重機制，一入咗就永遠唔會再檢查，之前
    # 呢啲 article 嘅 alert 會永久遺失（2026-07-21 audit finding）。淨係將
    # 「冇撞中任何 keyword」或者「成功 send 咗」嘅 article 計入 seen；
    # matched 但送失敗/未輪到嘅留返俾下一輪（仲喺 freshness window 內）再試。
    matched_ids = {a["id"] for a, _ in matched}
    seen |= {a["id"] for a in articles if a.get("id") and a["id"] not in matched_ids}
    seen |= alerted_ids
    _save_seen(seen)
    print(f"[fast-watch] fetched {len(articles)}, {len(matched)} matched, {sent} alerted, {len(seen)} tracked")


if __name__ == "__main__":
    asyncio.run(main())
