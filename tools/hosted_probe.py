#!/usr/bin/env python3
"""Probe every feed source: fetch + scrape a sample, judge per-source health.

Purpose: run the same code path on different networks (self-hosted Windows
box vs GitHub-hosted ubuntu runner) and compare which sources reject the
datacenter IP. Read-only — no docs/data writes, no MiniMax analyse calls.

Usage:
    python tools/hosted_probe.py

Exit code is always 0 (the report itself is the result); per-source verdicts:
    OK        fetch worked and sampled articles yielded full content
    DEGRADED  fetch worked but content < 200 chars (SEO preview / challenge page)
    FETCH-ERR fetch stage errored (403/timeout = likely IP-level block)
    EMPTY     fetch returned no articles and no error (quiet feed — inconclusive)
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import aiohttp  # noqa: E402

from src.feeds import HTTP_HEADERS, RSS_FEEDS  # noqa: E402
from src import fetch as fetch_mod  # noqa: E402
from src.scrape import scrape_all  # noqa: E402

SAMPLE_PER_FEED = 2
FETCH_TIMEOUT = 75  # same per-feed budget as production fetch_all()
MIN_CONTENT = 200   # below this scrape is considered preview-only


async def _fetch_feed(session, feed, cutoff):
    try:
        batch, error, _nm = await asyncio.wait_for(
            fetch_mod._fetch_one(session, feed, cutoff, {}), timeout=FETCH_TIMEOUT
        )
        return batch, (str(error) if error else "")
    except Exception as e:  # noqa: BLE001 — report, never crash the probe
        return [], f"{type(e).__name__}: {e}"


async def main() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=fetch_mod.ARTICLE_MAX_AGE_HOURS
    )
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(
        headers=HTTP_HEADERS, connector=connector
    ) as session:
        fetches = await asyncio.gather(
            *(_fetch_feed(session, f, cutoff) for f in RSS_FEEDS)
        )

    rows = []
    samples = []
    for feed, (batch, error) in zip(RSS_FEEDS, fetches):
        sample = batch[:SAMPLE_PER_FEED]
        rows.append(
            {
                "name": feed["name"],
                "fetched": len(batch),
                "fetch_error": error,
                "sample_urls": [a.get("url", "") for a in sample],
            }
        )
        samples.extend(sample)

    scraped_by_url = {}
    if samples:
        try:
            scraped = await asyncio.wait_for(scrape_all(samples), timeout=300)
        except Exception as e:  # noqa: BLE001
            print(f"[probe] scrape_all blew up: {e!r}", file=sys.stderr)
            scraped = []
        for a in scraped or []:
            scraped_by_url[a.get("url", "")] = len(a.get("content") or "")

    print(f"\n{'SOURCE':<28} {'VERDICT':<10} {'FETCH':>5}  {'CONTENT CHARS':<16} ERROR")
    print("-" * 90)
    for row in rows:
        lens = [scraped_by_url.get(u, 0) for u in row["sample_urls"]]
        if row["fetch_error"]:
            verdict = "FETCH-ERR"
        elif row["fetched"] == 0:
            verdict = "EMPTY"
        elif lens and max(lens) < MIN_CONTENT:
            verdict = "DEGRADED"
        else:
            verdict = "OK"
        lens_s = ",".join(str(n) for n in lens) or "-"
        err_s = row["fetch_error"][:40]
        print(f"{row['name']:<28} {verdict:<10} {row['fetched']:>5}  {lens_s:<16} {err_s}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
