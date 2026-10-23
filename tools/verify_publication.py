"""Bounded read-only Pages verification of a pinned, already-pushed revision.

Never rebuild, dispatch, roll back, call paid providers or send notifications.
The existing update job owns those decisions and reports verification failure.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.public_http import MAX_RESPONSE_BYTES, UnsafeDestination, public_connector, public_get, read_public_body
from tools.site_snapshot import _git

SITE = "https://dllmdllm.github.io/rss-news/"
MAX_ATTEMPTS = 12
BUDGET_SECONDS = 90
REQUEST_SECONDS = 8
CONCURRENCY = 6
INDEX = "data/articles_index.json"
AGGREGATE_PATHS = frozenset({"data/articles.json", INDEX})
AGGREGATE_MAX_BYTES = 32 * 1024 * 1024
SNAPSHOT_MAX_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
GIT_BUDGET_SECONDS = 40  # Same total ceiling as the prior two20s reads.
CORE_PATHS = (
    "index.html", "article.html", "sw.js", "css/categories.css",
    "js/common.js", "js/index.js", "js/summary-player.js", "css/summary-player.css", "js/article.js", "js/article-reading.js",
    "js/article-evidence.js", "js/reader-state.js", "js/event-reading.js",
    "js/story-groups.js", "js/reader-updates.js", "js/source-evidence.js", "css/reader-loop.css",
    "data/articles.json", INDEX, "data/build_status.json",
)
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SHA = re.compile(r"[a-f0-9]{40}\Z")


class PermanentFailure(ValueError):
    pass


class SnapshotError(ValueError):
    """Safe diagnostics about explicit repository paths and resource bounds."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_limit(path):
    return AGGREGATE_MAX_BYTES if path in AGGREGATE_PATHS else MAX_RESPONSE_BYTES


def _expected_files(repo, revision):
    """Read bounded immutable metadata; stream canonical Git files for hashes."""
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise ValueError("a full lowercase commit SHA is required")

    deadline = time.monotonic() + GIT_BUDGET_SECONDS

    def timeout():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SnapshotError("expected snapshot: Git read budget exhausted")
        return min(20, remaining)

    def sizes_for(paths):
        queries = ''.join(revision + ':docs/' + path + '\n' for path in paths).encode('utf-8')
        raw = subprocess.check_output(_git(repo) + ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
                                      input=queries, timeout=timeout(), stderr=subprocess.DEVNULL)
        lines = raw.decode('ascii').splitlines()
        if len(lines) != len(paths):
            raise SnapshotError("expected snapshot: missing file sizes")
        sizes = {}
        for path, line in zip(paths, lines):
            parts = line.split()
            if len(parts) != 3 or parts[1] != 'blob' or not parts[2].isdigit():
                raise SnapshotError(f"{path}: expected tracked blob missing")
            size = int(parts[2])
            if size > file_limit(path):
                raise SnapshotError(f"{path}: expected {size} bytes exceeds {file_limit(path)} byte limit")
            sizes[path] = size
        if sum(sizes.values()) > SNAPSHOT_MAX_BYTES:
            raise SnapshotError(f"expected selected snapshot exceeds {SNAPSHOT_MAX_BYTES} byte limit")
        return sizes

    metadata_path = 'data/articles.json'
    metadata_size = sizes_for([metadata_path])[metadata_path]

    def read(path):
        raw = subprocess.check_output(_git(repo) + ["show", revision + ":docs/" + path],
                                      timeout=timeout(), stderr=subprocess.DEVNULL)
        if len(raw) != metadata_size:
            raise SnapshotError(f"{path}: Git bytes differ from expected length")
        return raw

    payload = json.loads(read(metadata_path))
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
    sizes = sizes_for(list(paths))
    # One bounded archive command avoids an unbounded sequence of slow Git reads.
    # git archive applies the executor's autocrlf/eol settings. Pages receives
    # canonical repository bytes, so disable only this invocation's conversion;
    # never change the runner's persisted Git configuration or normalize HTTP.
    expected = {}
    names = {'docs/' + path: path for path in paths}
    directories = {str(parent) + '/' for name in names for parent in PurePosixPath(name).parents
                   if str(parent) != '.'}
    with tempfile.TemporaryFile() as staged:
        subprocess.run(_git(repo) + ["-c", "core.autocrlf=false", "-c", "core.eol=lf",
                       "archive", "--format=zip", revision] + list(names),
                       stdout=staged, timeout=timeout(), stderr=subprocess.DEVNULL, check=True)
        if staged.tell() > SNAPSHOT_MAX_BYTES + 1024 * 1024:
            raise SnapshotError("expected archive exceeds selected snapshot bound")
        staged.seek(0)
        with zipfile.ZipFile(staged) as archive:
            entries = archive.infolist()
            if len(entries) > len(names) + len(directories):
                raise SnapshotError("expected archive has unexpected entries")
            seen = set()
            for entry in entries:
                name = entry.orig_filename
                if name in seen or stat.S_ISLNK(entry.external_attr >> 16):
                    raise SnapshotError("expected archive has duplicate or symlink entries")
                seen.add(name)
                if entry.is_dir():
                    if name not in directories:
                        raise SnapshotError("expected archive has an unexpected directory")
                    continue
                if name not in names or entry.file_size != sizes[names[name]]:
                    raise SnapshotError("expected archive path or size differs from Git")
                hasher, total = hashlib.sha256(), 0
                with archive.open(entry) as stream:
                    while chunk := stream.read(CHUNK_BYTES):
                        total += len(chunk)
                        if total > sizes[names[name]]:
                            raise SnapshotError("expected archive exceeds Git length")
                        hasher.update(chunk)
                if total != sizes[names[name]]:
                    raise SnapshotError("expected archive is truncated")
                expected[names[name]] = hasher.hexdigest()
            if set(expected) != set(paths):
                raise SnapshotError("expected archive is missing selected files")
    sources = payload.get("sources") or {}
    if not isinstance(sources, dict):
        raise ValueError("expected source health object")
    health = {name: {key: row.get(key) for key in
              ("fetch_status", "fresh_count", "effective_count", "collection_method")}
              for name, row in sources.items() if isinstance(row, dict)}
    return expected, sizes, health


def expected_snapshot(repo, revision):
    """Compatibility API for callers that need canonical hashes and health."""
    expected, _, health = _expected_files(repo, revision)
    return expected, health


@asynccontextmanager
async def _public_response(session, path, revision, *, streaming=False):
    url = SITE + path + "?publication_sha=" + revision
    try:
        headers = {"User-Agent": "rss-news/publication-check (+https://github.com/dllmdllm/rss-news)",
                   "Cache-Control": "no-cache"}
        kwargs = {}
        if streaming:
            headers['Accept-Encoding'] = 'identity'
            kwargs['auto_decompress'] = False
        async with public_get(session, url, timeout=aiohttp.ClientTimeout(total=REQUEST_SECONDS),
                              headers=headers, **kwargs) as response:
            if response.status in {401, 403, 406, 429, 451}:
                raise PermanentFailure(f"{path}: HTTP {response.status}")
            if response.status != 200:
                raise ValueError(f"{path}: HTTP {response.status}")
            yield response
    except (UnsafeDestination, ssl.SSLCertVerificationError,
            aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError) as exc:
        raise PermanentFailure(f"{path}: {type(exc).__name__}") from exc
    except (aiohttp.ClientError, OSError) as exc:
        raise ValueError(f"{path}: {type(exc).__name__}") from exc


async def public_bytes(session, path, revision):
    """Legacy byte reader retains the unchanged generic8MiB limit."""
    async with _public_response(session, path, revision) as response:
        return await read_public_body(response)


async def public_digest(session, path, revision, expected_size):
    """Hash decoded public bytes without buffering; both lengths are bounded."""
    if (not isinstance(path, str) or not isinstance(revision, str)
            or path not in CORE_PATHS and not re.fullmatch(r'data/content/[A-Za-z0-9_-]{1,128}\.json', path)
            or not SHA.fullmatch(revision) or type(expected_size) is not int
            or not 0 <= expected_size <= file_limit(path)):
        raise SnapshotError("invalid expected publication path, revision or size")
    async with _public_response(session, path, revision, streaming=True) as response:
        encoding = response.headers.get('Content-Encoding', 'identity').lower().strip()
        if encoding in ('', 'identity'):
            decoder = None
        elif encoding in ('gzip', 'deflate'):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == 'gzip' else zlib.MAX_WBITS)
        else:
            raise PermanentFailure(f"{path}: unsupported content encoding")
        stream = getattr(response, 'content', None)
        if stream is None or not callable(getattr(stream, 'read', None)):
            raise PermanentFailure(f"{path}: streaming response unavailable")
        hasher, wire, decoded = hashlib.sha256(), 0, 0

        def accept(chunk):
            nonlocal decoded
            decoded += len(chunk)
            if decoded > expected_size:
                raise ValueError(f"{path}: deployed length exceeds expected {expected_size} bytes")
            hasher.update(chunk)

        while chunk := await stream.read(min(CHUNK_BYTES, (file_limit(path) - wire if decoder else expected_size - decoded) + 1)):
            wire += len(chunk)
            if wire > file_limit(path):
                raise PermanentFailure(f"{path}: encoded body exceeds {file_limit(path)} byte limit")
            if decoder is None:
                accept(chunk)
                continue
            try:
                pending = chunk
                while True:
                    bound = min(CHUNK_BYTES, expected_size - decoded + 1)
                    expanded = decoder.decompress(pending, bound)
                    accept(expanded)
                    if decoder.unused_data:
                        raise ValueError(f"{path}: trailing compressed data")
                    pending = decoder.unconsumed_tail
                    if not pending and (len(expanded) < bound or decoder.eof):
                        break
            except zlib.error as exc:
                raise ValueError(f"{path}: invalid compressed stream") from exc
        if decoder is not None and not decoder.eof:
            raise ValueError(f"{path}: truncated compressed stream")
        if decoded != expected_size:
            raise ValueError(f"{path}: deployed length {decoded} differs from expected {expected_size} bytes")
        return hasher.hexdigest()


async def verify_digests(expected, fetch, *, budget=BUDGET_SECONDS, clock=time.monotonic, sleep=asyncio.sleep):
    """Poll streamed digests, never re-run the producer after a mismatch."""
    if not expected or INDEX not in expected or not 0 < budget <= BUDGET_SECONDS:
        raise ValueError("bounded expected snapshot and positive verification budget required")
    started = clock()
    attempts, reason = 0, "verification budget exhausted"
    while attempts < MAX_ATTEMPTS and clock() - started < budget:
        attempts += 1
        remaining = budget - (clock() - started)
        try:
            async with asyncio.timeout(remaining):
                if await fetch(INDEX) != expected[INDEX]:
                    raise ValueError("articles_index.json: deployed bytes differ from pushed revision")
                semaphore = asyncio.Semaphore(CONCURRENCY)

                async def check(path):
                    async with semaphore:
                        actual = await fetch(path)
                    if actual != expected[path]:
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
        # Poll more often near the deadline; starting the final observation
        # just 10ms before expiry leaves no time to read the remaining assets.
        interval = 5 if remaining <= 2 * REQUEST_SECONDS else min(5 * attempts, 15)
        await sleep(min(interval, remaining))
    return {"status": "unverified", "attempts": attempts, "checked_files": [], "reason": reason}


async def verify(expected, fetch, **kwargs):
    """Compatibility API for small byte-returning fixture and diagnostic readers."""
    async def hashed(path):
        return digest(await fetch(path))
    return await verify_digests(expected, hashed, **kwargs)


async def verify_publication(repo, revision):
    expected, sizes, health = _expected_files(repo, revision)
    async with aiohttp.ClientSession(connector=public_connector(limit=CONCURRENCY)) as session:
        result = await verify_digests(expected, lambda path: public_digest(session, path, revision, sizes[path]))
    return {**result, "revision": revision, "site": SITE,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "expected_sha256": expected, "expected_sizes": sizes, "source_health": health}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD", help="full commit SHA; defaults to the selected checkout HEAD")
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.result.resolve()
    if out.is_relative_to(repo / "docs"):
        parser.error("verification report must remain outside published docs")
    try:
        if args.revision == "HEAD":
            args.revision = subprocess.check_output(_git(repo) + ["rev-parse", "HEAD"],
                                                    timeout=20, stderr=subprocess.DEVNULL).decode("ascii").strip()
        result = asyncio.run(verify_publication(repo, args.revision))
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        result = {"status": "unverified", "revision": args.revision,
                  "attempts": 0, "checked_files": [],
                  "reason": str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__}
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
