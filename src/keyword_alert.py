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

WATCH_KEYWORDS is user-editable via an Obsidian vault note (VAULT_PATH),
not by touching this file. sync_watch_keywords_from_vault(), called once
near the top of build.py's main() on the self-hosted runner (the only
place with filesystem access to the vault), copies the vault's plain-text
list into the repo-tracked CONFIG_PATH, which both this module and
fast_watch.py (running on a separate, vault-blind GitHub-hosted runner)
read at import time. Off that one machine (tests, CI) VAULT_PATH simply
doesn't exist and the sync is a silent no-op — CONFIG_PATH (or, failing
that, _DEFAULT_KEYWORDS) is still the source of truth everywhere else.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from src.breaking_alert import TELEGRAM_BOT_TOKEN, _send_telegram

STATE_PATH  = Path(__file__).parent.parent / "docs" / "data" / "keyword_alerts.json"
CONFIG_PATH = Path(__file__).parent.parent / "config" / "watch_keywords.txt"
VAULT_PATH  = Path(r"C:\Users\Nary\Documents\LifeOS_Workspace\10_Projects\Active\RSS_News\RSS News - Watch Keywords.md")

# 純粹係 CONFIG_PATH 都讀唔到（未 sync 過／檔案損毀）時嘅安全網，
# 唔係日常改嘅位——日常改請用 VAULT_PATH 嗰個 vault 檔案。
_DEFAULT_KEYWORDS: list[str] = [
    "OpenAI", "ChatGPT", "GPT-", "Sam Altman", "奧特曼",
    "Anthropic", "Claude AI",
    "Google", "Gemini AI", "DeepMind",
    "Meta AI", "Llama",
    "Apple", "庫克", "Tim Cook",
    "Microsoft", "微軟", "Satya Nadella",
    "Nvidia", "輝達", "黃仁勳",
]

FRESHNESS_HOURS      = 2    # 只揀呢個窗口內先出嘅文，避免每次 build 重新掃全部舊文
MAX_ALERTS_PER_BUILD = 5    # 防止太闊嘅關鍵字（例如「香港」）一次過洗版
STATE_TTL_HOURS      = 48   # prune 舊 alerted record

HKT = timezone(timedelta(hours=8))
QUIET_HOURS_HKT = range(0, 7)   # 00:00–07:00 HKT，跟 fast_watch.py 一致，唔想瞓緊覺俾嘢吵醒


def _in_quiet_hours(now: datetime) -> bool:
    return now.astimezone(HKT).hour in QUIET_HOURS_HKT


_KEYWORD_SECTION_MARKERS = ("## 關鍵字清單", "## keywords")


def _skip_frontmatter(lines: list[str]) -> int:
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1
    return i


def _find_marker_index(lines: list[str], start: int) -> int | None:
    for idx in range(start, len(lines)):
        if lines[idx].strip().lower() in _KEYWORD_SECTION_MARKERS:
            return idx + 1
    return None


def _extract_keyword_lines(lines: list[str], start: int) -> list[str]:
    out = []
    for line in lines[start:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("-*").strip()
        if line:
            out.append(line)
    return out


def _parse_keyword_lines(text: str) -> list[str]:
    """畀 config/watch_keywords.txt 嗰種冇說明文字嘅純清單用：跳過 YAML
    frontmatter，若見到 "## 關鍵字清單" 標題就淨處理佢之後嘅內容，搵唔到
    就當全個 body 都係關鍵字（一行一個，`#` 開頭當 comment）。"""
    lines = text.splitlines()
    start = _skip_frontmatter(lines)
    marker = _find_marker_index(lines, start)
    return _extract_keyword_lines(lines, marker if marker is not None else start)


def _parse_vault_keyword_lines(text: str) -> list[str] | None:
    """畀 vault note 用——一定要搵到 "## 關鍵字清單" 標題先算有效輸入，
    搵唔到就 return None（唔會將標題之前自由寫嘅說明文字當成關鍵字），
    等 sync_watch_keywords_from_vault() 可以安全咁拒絕同步、保留舊 config，
    而唔係將成段中文說明寫咗落去（試過一次：標題漏咗，dry-run test 冧咗
    真 config file）。"""
    lines = text.splitlines()
    start = _skip_frontmatter(lines)
    marker = _find_marker_index(lines, start)
    if marker is None:
        return None
    return _extract_keyword_lines(lines, marker)


def _load_keywords_from_config() -> list[str]:
    try:
        return _parse_keyword_lines(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[keyword] config load failed: {exc!r}")
        return list(_DEFAULT_KEYWORDS)


def sync_watch_keywords_from_vault() -> None:
    """喺 self-hosted runner 讀 Obsidian vault 嘅關鍵字清單，同步落
    CONFIG_PATH（下次 build 同 fast-watch checkout 都會用到）。VAULT_PATH
    淨係存在自己部機，喺其他環境見唔到就靜靜 skip，唔會累個 build 死。"""
    if not VAULT_PATH.exists():
        return
    try:
        text = VAULT_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[keyword] vault read failed: {exc!r}")
        return

    keywords = _parse_vault_keyword_lines(text)
    if keywords is None:
        print(f"[keyword] vault missing '## 關鍵字清單' marker — skipping sync, check {VAULT_PATH.name} wasn't edited wrong")
        return
    if not keywords:
        print("[keyword] vault keyword section empty — keeping existing config")
        return

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("\n".join(keywords) + "\n", encoding="utf-8")
    global WATCH_KEYWORDS
    WATCH_KEYWORDS = keywords
    print(f"[keyword] synced {len(keywords)} keywords from vault")


WATCH_KEYWORDS: list[str] = _load_keywords_from_config()


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
    if len(matches) > MAX_ALERTS_PER_BUILD:
        # 之前完全靜默 drop——冇任何 log 分辨「因為 cap 被截走」定係「本身
        # 冇咁多 match」，持續高於 5/build 嘅關鍵字（例如加咗突發事件字眼
        # 之後）舊 match 會連續幾輪輸俾新 match，最終過咗 FRESHNESS_HOURS
        # 靜靜哋消失，用戶完全唔知（2026-07-21 audit finding）。
        dropped = len(matches) - MAX_ALERTS_PER_BUILD
        print(f"[keyword] {dropped} match(es) dropped by MAX_ALERTS_PER_BUILD cap this cycle")
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


async def send_keyword_alerts(articles: list, *, now: datetime | None = None) -> None:
    """Alert Telegram for articles matching WATCH_KEYWORDS."""
    state = _load_state()
    now = now or datetime.now(timezone.utc)

    if not TELEGRAM_BOT_TOKEN or not WATCH_KEYWORDS:
        _save_state(state)   # ensure the file always exists
        return

    if _in_quiet_hours(now):
        # 靜靜 skip、唔 mark alerted——如果嗰篇文喺 quiet hours 完咗之後
        # 仲喺 FRESHNESS_HOURS 窗口內，下個 build 會照樣揀返嚟推送。
        print("[keyword] quiet hours (00:00-07:00 HKT) — skip send")
        _save_state(state)
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
