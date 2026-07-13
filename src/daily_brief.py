"""每日 AI 早報。

每日（HKT）06:00 之後嘅第一個 build，用 MiniMax 綜合過去 24 小時嘅
top stories 寫一段早報 + 重點列表：

  - 輸出 docs/data/daily_brief.json（前端 index 頁「今日早報」卡 + TTS 用）
  - 生成成功後推一次 Telegram（重用 breaking_alert 嘅 bot 設定）

冪等：daily_brief.json 內嘅 date 等於今日（HKT）就直接 skip，
所以每日只會生成同推送一次。
"""
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from src.minimax_client import MINIMAX_API_KEY, post_messages, should_retry
from src.breaking_alert import TELEGRAM_BOT_TOKEN, _send_telegram

OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "data" / "daily_brief.json"

HKT = timezone(timedelta(hours=8))
# 早過呢個鐘數唔生成——等夜晚嘅新聞沉澱，趕喺返工前出街。
GENERATE_AFTER_HOUR = 6
TOP_N = 15

SYSTEM_PROMPT = (
    "你係香港新聞編輯，用香港廣東話書面語寫每日早報。"
    "根據輸入嘅新聞列表，輸出一個 JSON object，唔好有任何其他文字或 markdown：\n"
    '{"title":"今日焦點一句總結，唔超過25字",'
    '"text":"早報正文，150至250字，一段過，概括今日最重要嘅3至5單新聞同其影響",'
    '"highlights":[{"point":"一句重點，唔超過30字","id":"對應輸入新聞嘅id"}]}\n'
    "highlights 要有 4 至 6 條，揀最重要、唔同範疇嘅新聞；id 必須照抄輸入。"
)


def _load_existing() -> dict:
    if OUTPUT_PATH.exists():
        try:
            data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save(payload: dict):
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(OUTPUT_PATH.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUTPUT_PATH)


def should_generate(existing: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(HKT)
    if now.hour < GENERATE_AFTER_HOUR:
        return False
    return existing.get("date") != now.strftime("%Y-%m-%d")


def select_top_articles(articles: list, now: datetime | None = None, top_n: int = TOP_N) -> list:
    """過去 24 小時、每個 cluster 一篇、按 score+新鮮度排嘅 top N。"""
    now = now or datetime.now(HKT)
    cutoff = (now - timedelta(hours=24)).isoformat()
    seen_clusters: set = set()
    picked = []
    ranked = sorted(
        (a for a in articles if not a.get("duplicate_of") and str(a.get("date", "")) >= cutoff),
        key=lambda a: (int(a.get("score") or 0), str(a.get("date", ""))),
        reverse=True,
    )
    for a in ranked:
        cid = a.get("cluster_id")
        if cid and cid in seen_clusters:
            continue
        if cid:
            seen_clusters.add(cid)
        picked.append(a)
        if len(picked) >= top_n:
            break
    return picked


def _build_user_text(picked: list) -> str:
    lines = []
    for a in picked:
        summary = str(a.get("summary") or "").replace("\n", " ")[:120]
        lines.append(
            f"id={a['id']} 分類={a.get('category', '')} 來源={a.get('source', '')}\n"
            f"標題：{a.get('title', '')}\n摘要：{summary}"
        )
    return "\n---\n".join(lines)


def _parse_brief(raw: str, valid_ids: set) -> dict | None:
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip())
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()[:40]
    body = str(data.get("text") or "").strip()
    highlights = []
    for h in (data.get("highlights") or [])[:6]:
        if not isinstance(h, dict):
            continue
        point = str(h.get("point") or "").strip()[:60]
        hid = str(h.get("id") or "").strip()
        if point:
            highlights.append({"point": point, "id": hid if hid in valid_ids else ""})
    if not body or not highlights:
        return None
    return {"title": title, "text": body, "highlights": highlights}


def _telegram_text(brief: dict, date_label: str) -> str:
    # 淨係標題 + bullets——嗰段 150-250 字 paragraph（brief["text"]）留喺網站
    # 早報卡度。Telegram 度兩樣一齊出會重複晒同一批新聞，喺手機屏幕變成
    # 一大堵字（2026-07-13 用戶反映排版亂）。
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = [f"🌅 <b>今日早報 · {date_label}</b>"]
    if brief.get("title"):
        lines.append(esc(brief["title"]))
    lines.append("")
    lines.extend(f"・{esc(h['point'])}" for h in brief["highlights"])
    return "\n".join(lines)


async def generate_daily_brief(articles: list) -> None:
    if not MINIMAX_API_KEY:
        print("[brief] Skipped — set MINIMAX_API_KEY")
        return
    now = datetime.now(HKT)
    existing = _load_existing()
    if not should_generate(existing, now):
        print(f"[brief] Skipped — already generated for {existing.get('date')} "
              f"(or before {GENERATE_AFTER_HOUR}:00 HKT)")
        return

    picked = select_top_articles(articles, now)
    if len(picked) < 5:
        print(f"[brief] Skipped — only {len(picked)} articles in the last 24h")
        return

    user_text = _build_user_text(picked)
    valid_ids = {a["id"] for a in picked}
    brief = None
    async with aiohttp.ClientSession() as session:
        for attempt, backoff in enumerate((0, 10)):
            if backoff:
                await asyncio.sleep(backoff)
            raw, err, status = await post_messages(
                session,
                system=SYSTEM_PROMPT,
                user_text=user_text,
                max_tokens=1200,
                timeout=60,
                thinking={"type": "disabled"},
            )
            if err:
                print(f"[brief] API error (attempt {attempt + 1}): {status} {err.get('type')}")
                if should_retry(err, status):
                    continue
                return
            brief = _parse_brief(raw, valid_ids)
            if brief:
                break
            print(f"[brief] Unparseable output (attempt {attempt + 1})")
        if not brief:
            print("[brief] Failed — keeping previous daily_brief.json")
            return

        payload = {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            **brief,
        }
        _save(payload)
        print(f"[brief] Generated for {payload['date']}: {brief['title'][:30]}")

        if TELEGRAM_BOT_TOKEN:
            date_label = f"{now.month}月{now.day}日"
            try:
                status = await _send_telegram(session, _telegram_text(brief, date_label))
                print(f"[brief] Telegram: {status}")
            except Exception as exc:
                print(f"[brief] Telegram failed: {exc!r}")
