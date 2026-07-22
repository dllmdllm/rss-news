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
from src.keyword_alert import KEYWORD_CONTEXT, WATCH_KEYWORDS
from src.trends_watch import load_trending_keywords

# 一次過程一個 run，process 內冇再 resync（跟 WATCH_KEYWORDS 同一套做法）——
# Google Trends 熱門字（2026-07-21，用戶要求自動加入監控）由 build.py 嗰邊
# sync_trending_keywords() 寫落 repo-tracked config/trending_keywords.txt，
# 呢度靠 fast-watch.yml 嘅 git checkout 攞到最新版。
TRENDING_KEYWORDS = load_trending_keywords()

HKT = timezone(timedelta(hours=8))
WATCHED_SOURCES = {"星島頭條", "am730", "TVB 新聞"}
# cwd-relative — the workflow restores/saves this exact path via actions/cache.
STATE_PATH = Path("fast_watch_state.json")
FRESHNESS_HOURS = 2   # cold-start window; "seen" set prevents re-alerting after that
MAX_ALERTS_PER_RUN = 5
SEEN_CAP = 3000        # bound state.json size — plain id list, no dates to prune by
# 呢度冇 AI/clustering 分辨「同一單新聞」定「唔同新聞」（keyword_alert.py
# 嗰邊有 build.py 計好嘅 cluster_id 可以用，呢度冇）。廣泛嘅 keyword（例如
# 「交通意外」）一單意外俾好多 source 報，之前每篇都獨立 alert，連環彈
# 幾次。用「同一個 keyword 幾耐內唔再送」做土法 dedup（2026-07-21，用戶
# 反映；30 分鐘係用戶揀嘅預設）。
KEYWORD_COOLDOWN_MINUTES = 30
# Trending 字（Google 熱搜）冇 curated WATCH_KEYWORDS 咁「特登揀嘅」，成日
# 對應緊持續幾個鐘嘅日常熱話（六合彩攪珠、八卦人物）——用長啲 cooldown +
# 獨立細 quota，避免佢哋洗晒成個 MAX_ALERTS_PER_RUN（2026-07-21，用戶反映
# 太密集）。
TRENDING_COOLDOWN_MINUTES = 90
MAX_TRENDING_ALERTS_PER_RUN = 1
# fast-watch.yml's job timeout-minutes is 4 (240s), covering checkout +
# setup-python + pip install + cache restore/save around this one step too
# — unlike build.py, main() had no overall wait_for at all, so a hang here
# could eat the whole job timeout and skip _save_state() entirely, losing
# this run's dedup progress (2026-07-21 audit finding).
MAIN_TIMEOUT = 150


def _load_state() -> dict:
    """{"seen": {article_id: seen_at_iso}, "cooldown": {keyword: last_alerted_iso}}.
    Migrates the old plain-id-list `seen` format (pre-2026-07-21)."""
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            seen = data.get("seen")
            if isinstance(seen, list):
                now_iso = datetime.now(timezone.utc).isoformat()
                seen = {aid: now_iso for aid in seen}
            elif not isinstance(seen, dict):
                seen = {}
            cooldown = data.get("cooldown")
            if not isinstance(cooldown, dict):
                cooldown = {}
            return {"seen": seen, "cooldown": cooldown}
        except Exception as exc:
            print(f"[fast-watch] state load failed: {exc!r}")
    return {"seen": {}, "cooldown": {}}


def _save_state(state: dict):
    seen = state.get("seen") or {}
    if len(seen) > SEEN_CAP:
        # article id 嚟自 url 嘅 md5 hash，同 recency 完全冇關係——之前
        # `sorted(seen)[-SEEN_CAP:]` 個 alphabetical 淘汰policy可以evict
        # 啱啱先見過嘅article，保留舊嘅（2026-07-21 audit finding）。而家
        # 淘汰真正最舊嘅 timestamp。
        newest = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:SEEN_CAP]
        seen = dict(newest)
    payload = {"seen": seen, "cooldown": state.get("cooldown") or {}}
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _keyword_in_cooldown(keyword: str, cooldown: dict, now: datetime, minutes: int = KEYWORD_COOLDOWN_MINUTES) -> bool:
    ts = cooldown.get(keyword)
    if not ts:
        return False
    try:
        last = datetime.fromisoformat(ts)
    except Exception:
        return False
    return (now - last).total_seconds() < minutes * 60


def _is_trending_keyword(keyword: str) -> bool:
    return keyword in TRENDING_KEYWORDS and keyword not in WATCH_KEYWORDS


def _match_keyword(article: dict) -> str | None:
    hay = f"{article.get('title', '')} {article.get('rss_content') or ''}".lower()
    for kw in dict.fromkeys(WATCH_KEYWORDS + TRENDING_KEYWORDS):
        if not kw or kw.lower() not in hay:
            continue
        # 有啲 keyword（例如「死亡」「交通意外」）喺 keyword_alert.py 個
        # vault 度用 `context:` directive 要求埋 HK 脈絡字眼一齊出現先算
        # match（2026-07-21，減低海外新聞/歷史人物嗰類 false positive）——
        # 快速通道跟返同一套規則。
        required = KEYWORD_CONTEXT.get(kw)
        if required and not any(c.lower() in hay for c in required):
            continue
        return kw
    return None


def _format_hkt_time(article: dict) -> str:
    """文章發布時間（HKT，HH:MM）。Parse 唔到就靜靜返空字串，唔阻住send。"""
    try:
        dt = datetime.fromisoformat(article.get("date", ""))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(HKT).strftime("%H:%M")
    except Exception:
        return ""


def _format_text(article: dict, keyword: str, is_trending: bool = False) -> str:
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    meta = " · ".join(filter(None, [
        article.get("source", ""),
        _format_hkt_time(article),
        "快速通道（未經全文/AI 分析）",
    ]))
    keyword_label = esc(keyword) + (" (From Google Trend)" if is_trending else "")
    lines = [
        f"⚡ <b>快訊關鍵字</b>：{keyword_label}",
        esc(article.get("title", "")),
        esc(meta),
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
    if not WATCH_KEYWORDS and not TRENDING_KEYWORDS:
        print("[fast-watch] skipped — WATCH_KEYWORDS and TRENDING_KEYWORDS both empty")
        return

    state = _load_state()
    seen = state["seen"]
    cooldown = state["cooldown"]
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=FRESHNESS_HOURS)

    # Mutated by _run() as it progresses, so a timeout mid-way still leaves
    # us with whatever was determined so far to persist below — unlike a
    # bare `asyncio.wait_for(main(), ...)` wrapper from outside, which would
    # cancel everything including the never-reached _save_state() call.
    articles: list[dict] = []
    matched: list[tuple[dict, str]] = []
    alerted_ids: set[str] = set()
    alerted_keywords: set[str] = set()
    sent = 0

    async def _run():
        nonlocal articles, matched, sent
        async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
            articles = await _fetch_watched(session, cutoff)
            fresh = [a for a in articles if a.get("id") and a["id"] not in seen]
            matched = [(a, kw) for a in fresh if (kw := _match_keyword(a))]
            matched.sort(key=lambda pair: pair[0].get("date", ""), reverse=True)

            # 呢度冇 AI clustering 分辨「同一單新聞」——用「keyword 幾耐內
            # 唔再送」做土法 dedup（2026-07-21，用戶反映「交通意外」連環
            # 彈嘅問題）。Cooldown status 要每篇文即時check、send完即時更新
            # ——如果淨係響 loop 之前計一次 eligible list，同一個 run 入面
            # A 篇送咗都唔會即時擋住跟住嘅 B、C（佢哋撞正同一個 keyword），
            # 3 篇會齊齊送晒（試過真係中，先改做逐篇 check + 即時更新）。
            sent_this_run = 0
            trending_sent_this_run = 0
            for article, keyword in matched:
                if sent_this_run >= MAX_ALERTS_PER_RUN:
                    break
                is_trending = _is_trending_keyword(keyword)
                if is_trending and trending_sent_this_run >= MAX_TRENDING_ALERTS_PER_RUN:
                    continue
                minutes = TRENDING_COOLDOWN_MINUTES if is_trending else KEYWORD_COOLDOWN_MINUTES
                if _keyword_in_cooldown(keyword, cooldown, now, minutes):
                    continue
                try:
                    status = await _send_telegram(
                        session, _format_text(article, keyword, is_trending), photo_url=article.get("thumbnail") or ""
                    )
                    if 200 <= status < 300:
                        sent += 1
                        sent_this_run += 1
                        if is_trending:
                            trending_sent_this_run += 1
                        alerted_ids.add(article["id"])
                        alerted_keywords.add(keyword)
                        cooldown[keyword] = now.isoformat()
                        print(f"[fast-watch] Alerted ({keyword}): {article.get('title', '')[:50]}")
                    else:
                        print(f"[fast-watch] Telegram returned {status}")
                except Exception as exc:
                    print(f"[fast-watch] Send failed: {exc!r}")

    try:
        await asyncio.wait_for(_run(), timeout=MAIN_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        print(f"[fast-watch] timed out after {MAIN_TIMEOUT}s — saving partial progress")

    # 唔好將 send 失敗／未過 MAX_ALERTS_PER_RUN cap／仲喺 cooldown 嘅
    # matched article 都計做「已讀」——`seen` 係唯一嘅去重機制，一入咗就
    # 永遠唔會再檢查，之前呢啲 article 嘅 alert 會永久遺失（2026-07-21
    # audit finding）。淨係將「冇撞中任何 keyword」或者「成功 send 咗」嘅
    # article 計入 seen；matched 但送失敗/未輪到/仲喺 cooldown 嘅留返俾
    # 下一輪（仲喺 freshness window 內）再試。
    now_iso = now.isoformat()
    matched_ids = {a["id"] for a, _ in matched}
    for a in articles:
        if a.get("id") and a["id"] not in matched_ids:
            seen[a["id"]] = now_iso
    for aid in alerted_ids:
        seen[aid] = now_iso
    for kw in alerted_keywords:
        cooldown[kw] = now_iso
    _save_state({"seen": seen, "cooldown": cooldown})
    print(f"[fast-watch] fetched {len(articles)}, {len(matched)} matched, {sent} alerted, {len(seen)} tracked")


if __name__ == "__main__":
    asyncio.run(main())
