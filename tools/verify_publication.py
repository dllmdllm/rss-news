"""Bounded read-only Pages verification of a pinned, already-pushed revision.

Never rebuild, dispatch, roll back, call paid providers or send notifications.
The existing update job owns those decisions and reports verification failure.
"""
import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.public_http import MAX_RESPONSE_BYTES, UnsafeDestination, public_connector, public_get, read_public_body
from tools.site_snapshot import _files, _git

SITE = "https://dllmdllm.github.io/rss-news/"
MAX_ATTEMPTS = 6
BUDGET_SECONDS = 90
REQUEST_SECONDS = 8
CONCURRENCY = 6
INDEX = "data/articles_index.json"
CORE_PATHS = (
    "index.html", "article.html", "sw.js", "css/categories.css",
    "js/common.js", "js/index.js", "js/article.js", "js/article-reading.js",
    "js/article-evidence.js", "js/reader-state.js", "js/event-reading.js",
    "data/articles.json", INDEX, "data/build_status.json",
)
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")


class PermanentFailure(ValueError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def expected_snapshot(repo, revision):
    """Read only explicit public paths from a full commit SHA, never the worktree."""
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError("a full lowercase commit SHA is required")

    def read(path):
        raw = subprocess.check_output(_git(repo) + ["show", revision + ":docs/" + path],
                                      timeout=20, stderr=subprocess.DEVNULL)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("expected public file exceeds response bound")
        return raw

    full = read("data/articles.json")
    payload = json.loads(full)
    if not isinstance(payload, dict) or payload.get("schema_version") != 2 or not payload.get("articles"):
        raise ValueError("expected current, nonempty article snapshot")
    articles = payload["articles"]
    if not isinstance(articles, list) or any(not isinstance(row, dict) or
            not isinstance(row.get("id"), str) or not SAFE_ID.fullmatch(row["id"]) for row in articles):
        raise ValueError("expected safe article identities")
    # Deterministic newest, evidence-backed and partial-body samples. This is a
    # transport check, not a claim to have downloaded every published sidecar.
    def partial(row):
        quality = row.get("content_quality")
        fallback = quality.get("fallback") if isinstance(quality, dict) else None
        return (row.get("evidence_status") == "partial" or isinstance(fallback, str)
                and (fallback.startswith("rss-") or fallback == "minimal"))

    selected = list(dict.fromkeys(
        [row["id"] for row in articles[:3]] +
        [row["id"] for row in articles if row.get("summary_evidence")][:1] +
        [row["id"] for row in articles if partial(row)][:1]
    ))[:5]
    paths = dict.fromkeys(CORE_PATHS + tuple("data/content/" + aid + ".json" for aid in selected))
    # One bounded archive command avoids an unbounded sequence of slow Git reads.
    raw = subprocess.check_output(_git(repo) + ["archive", "--format=zip", revision] +
                                  ["docs/" + path for path in paths],
                                  timeout=20, stderr=subprocess.DEVNULL)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if any(entry.file_size > MAX_RESPONSE_BYTES for entry in archive.infolist()):
            raise ValueError("expected public file exceeds response bound")
        files = _files(archive)
    expected = {path: digest(files["docs/" + path]) for path in paths}
    sources = payload.get("sources") or {}
    if not isinstance(sources, dict):
        raise ValueError("expected source health object")
    health = {name: {key: row.get(key) for key in
              ("fetch_status", "fresh_count", "effective_count", "collection_method")}
              for name, row in sources.items() if isinstance(row, dict)}
    return expected, health


async def public_bytes(session, path, revision):
    """Fixed public site, consistent identity, guarded DNS/TLS/redirect/body."""
    url = SITE + path + "?publication_sha=" + revision
    try:
        async with public_get(session, url, timeout=aiohttp.ClientTimeout(total=REQUEST_SECONDS),
                              headers={"User-Agent": "rss-news/publication-check (+https://github.com/dllmdllm/rss-news)",
                                       "Cache-Control": "no-cache"}) as response:
            if response.status in {401, 403, 406, 429, 451}:
                raise PermanentFailure(f"{path}: HTTP {response.status}")
            if response.status != 200:
                raise ValueError(f"{path}: HTTP {response.status}")
            return await read_public_body(response)
    except (UnsafeDestination, ssl.SSLCertVerificationError,
            aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError) as exc:
        raise PermanentFailure(f"{path}: {type(exc).__name__}") from exc
    except (aiohttp.ClientError, OSError) as exc:
        raise ValueError(f"{path}: {type(exc).__name__}") from exc


async def verify(expected, fetch, *, budget=BUDGET_SECONDS, clock=time.monotonic, sleep=asyncio.sleep):
    """Poll deployment bytes, never re-run the producer after a mismatch."""
    if not expected or INDEX not in expected or not 0 < budget <= BUDGET_SECONDS:
        raise ValueError("bounded expected snapshot and positive verification budget required")
    started = clock()
    attempts, reason = 0, "verification budget exhausted"
    while attempts < MAX_ATTEMPTS and clock() - started < budget:
        attempts += 1
        remaining = budget - (clock() - started)
        try:
            async with asyncio.timeout(remaining):
                raw_index = await fetch(INDEX)
                if digest(raw_index) != expected[INDEX]:
                    raise ValueError("articles_index.json: deployed bytes differ from pushed revision")
                semaphore = asyncio.Semaphore(CONCURRENCY)

                async def check(path):
                    async with semaphore:
                        raw = await fetch(path)
                    if digest(raw) != expected[path]:
                        raise ValueError(f"{path}: deployed bytes differ from pushed revision")

                # Drain/cancel sibling requests before a retry or return.
                tasks = [asyncio.create_task(check(path)) for path in expected if path != INDEX]
                try:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    failures = [value for value in results if isinstance(value, BaseException)]
                    if failures:
                        # A denial/TLS error wins over a simultaneous stale file;
                        # never schedule another polling round after a denial.
                        raise next((value for value in failures if isinstance(value, PermanentFailure)), failures[0])
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                return {"status": "verified", "attempts": attempts,
                        "checked_files": sorted(expected), "reason": None}
        except PermanentFailure as exc:
            return {"status": "unverified", "attempts": attempts,
                    "checked_files": [], "reason": str(exc)}
        except (ValueError, TimeoutError) as exc:
            reason = str(exc) or "verification budget exhausted"
        remaining = budget - (clock() - started)
        if attempts >= MAX_ATTEMPTS or remaining <= 0:
            break
        await sleep(min(5 * attempts, 15, remaining))
    return {"status": "unverified", "attempts": attempts, "checked_files": [], "reason": reason}


async def verify_publication(repo, revision):
    expected, health = expected_snapshot(repo, revision)
    async with aiohttp.ClientSession(connector=public_connector(limit=CONCURRENCY)) as session:
        result = await verify(expected, lambda path: public_bytes(session, path, revision))
    return {**result, "revision": revision, "site": SITE,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "expected_sha256": expected, "source_health": health}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.result.resolve()
    if out.is_relative_to(repo / "docs"):
        parser.error("verification report must remain outside published docs")
    try:
        result = asyncio.run(verify_publication(repo, args.revision))
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        result = {"status": "unverified", "revision": args.revision,
                  "attempts": 0, "checked_files": [], "reason": type(exc).__name__}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Pages publication {result['status']}: {result['revision']} ({result['attempts']} attempts)")
    summary = f"### Public Pages verification\n\nRevision: `{result['revision']}`\n\nStatus: **{result['status']}**; {len(result['checked_files'])} selected public files; {result['attempts']} attempts.\n"
    if result.get("reason"):
        summary += f"\nReason: {result['reason']}\n"
    if os_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    return 0 if result["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
