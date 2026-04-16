import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from src.fetch import fetch_all
from src.scrape import scrape_all
from src.summarise import summarise_all

DOCS_DIR = ROOT / "docs"
DATA_DIR = DOCS_DIR / "data"


def save_json(articles: list):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "articles.json"
    payload = {
        "updated": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT"),
        "articles": articles,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = path.stat().st_size // 1024
    print(f"[build] Saved: {path} ({size_kb} KB, {len(articles)} articles)")


async def main():
    print("=== rss-news build start ===")
    articles = await fetch_all()
    articles = await scrape_all(articles)
    articles = await summarise_all(articles)
    articles.sort(key=lambda x: x.get("date", ""), reverse=True)
    save_json(articles)
    print("=== done ===")


if __name__ == "__main__":
    asyncio.run(main())
