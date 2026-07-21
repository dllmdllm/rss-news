"""Google Trends（香港）熱門搜尋 — 自動並入 keyword watch 清單。

trends.google.com 冇官方支援「過去一小時」granularity 嘅 trending API；
呢度用官方支援嘅 daily trending RSS feed（geo=HK，公開、免 auth，唔算
unofficial scraping——同要 scrape 網頁 HTML 嘅 pytrends 唔同）：

    https://trends.google.com/trending/rss?geo=HK

用戶要求（2026-07-21）：Google 熱門字自動加入監控清單，match 到就當普通
keyword alert 送（唔開獨立 channel、唔額外標記）——keyword_alert.py /
fast_watch.py 淨係將 load_trending_keywords() 嘅結果加入原本 WATCH_KEYWORDS
一齊比對，_matched_keyword 就係嗰個熱門詞本身，用戶睇 alert 已經睇到邊個字
中，唔使額外註明「呢個係 Google trending」。

呢個 feed 個名叫 "daily trends"，但實測（2026-07-21）item 嘅 pubDate 相隔
10-30 分鐘，成個榜好快會轉勻——即係話「日更新」淨係個 endpoint 嘅命名，
唔係實際更新頻率。原本呢度跟 daily_brief.py 嗰種「每日一次」冪等 pattern
淨係 sync 一次，會漏晒成日嘅 intraday trending 變化；跟用戶確認之後改做
跟返 build.py 主 pipeline 同頻率（~20 分鐘一次），每次 build 都真.去
fetch，唔再有「今日 sync 過就 skip」嘅 gate。每次成功都完全覆寫舊清單
（唔似 WATCH_KEYWORDS 咁累積），避免舊嘅 trending 詞賴喺個清單度。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import aiohttp
import zhconv

from src.feeds import HTTP_HEADERS

CONFIG_PATH = Path(__file__).parent.parent / "config" / "trending_keywords.txt"
TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=HK"

HKT = timezone(timedelta(hours=8))
FETCH_TIMEOUT = 15
MAX_KEYWORDS = 20
# 單字（例如「金」）撞正太多常見詞（金融/現金/基金/黃金……），substring
# match 誤鳴風險太高，過濾走。
MIN_KEYWORD_LEN = 2

# 純粹俾人睇「上次成功 fetch 幾時」，冇任何 gating 邏輯食呢個值。
_SYNCED_PREFIX = "# synced "


def _parse_rss_titles(xml_text: str) -> list[str]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    titles = []
    for item in root.iter("item"):
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            t = title_el.text.strip()
            if t:
                titles.append(t)
    return titles


def _clean_keywords(raw_titles: list[str]) -> list[str]:
    # 網站內文一律香港繁體（zhconv "zh-hk"），Google Trends 有時返簡體
    # （例如「沃伦·巴伦」），唔轉嘅話成隻詞永遠 match 唔中任何文章。
    out: dict[str, None] = {}
    for t in raw_titles:
        t = zhconv.convert(t, "zh-hk").strip()
        if len(t) < MIN_KEYWORD_LEN:
            continue
        out.setdefault(t, None)
        if len(out) >= MAX_KEYWORDS:
            break
    return list(out)


def _write_config(keywords: list[str], timestamp: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{_SYNCED_PREFIX}{timestamp}", *keywords]
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_trending_keywords() -> list[str]:
    if not CONFIG_PATH.exists():
        return []
    try:
        lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    return [line for line in lines if line and not line.startswith("#")]


async def fetch_trending_keywords(session: aiohttp.ClientSession) -> list[str]:
    async with session.get(
        TRENDS_RSS_URL, headers=HTTP_HEADERS, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    ) as resp:
        resp.raise_for_status()
        text = await resp.text()
    return _clean_keywords(_parse_rss_titles(text))


async def sync_trending_keywords(now: datetime | None = None) -> list[str]:
    """每次 call 都真.去 fetch（跟 build.py 主 pipeline 同頻率，~20 分鐘
    一次）。Fetch 失敗（網絡 / Google 改版 / 被擋）就保留舊清單，唔會令
    呢一輪 build 冧。"""
    now = now or datetime.now(HKT)
    try:
        async with aiohttp.ClientSession() as session:
            keywords = await fetch_trending_keywords(session)
    except Exception as exc:
        print(f"[trends] fetch failed: {exc!r}")
        return load_trending_keywords()
    _write_config(keywords, now.isoformat())
    print(f"[trends] synced {len(keywords)} trending keywords at {now.isoformat()}")
    return keywords
