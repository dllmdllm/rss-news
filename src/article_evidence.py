"""Deterministic article provenance and quote verification, with no IO/API calls."""
import hashlib
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup


def bind_current_body_hash(article: dict) -> None:
    """Bind an intentionally materialized body, never repair an unchanged cache.

    Callers must first select a fresh extraction/fallback or validate the saved
    record being restored. Unchanged cached/deferred bytes keep their expected
    hash so the strict inventory can still reject corruption.
    """
    digest = hashlib.sha256(article['content'].encode('utf-8')).hexdigest()
    if article.get('content_hash') != digest:
        article.pop('key_sentences_content_hash', None)
        article['summary_evidence'] = []
        article['summary_evidence_status'] = 'unavailable'
    article['content_hash'] = digest


def body_text(content: str) -> str:
    soup = BeautifulSoup(content or "", "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text("", strip=False)


def normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def source_outlet(value: str) -> str:
    """Only known publisher families merge; unrelated names remain distinct."""
    name = normalise_text(value)
    if name in {"星島頭條", "星島 即時中國", "星島 即時國際"}:
        return "星島"
    return re.sub(r"^(RTHK|明報|HK01|東網|HuffPost|Now) .+", r"\1", name)


def timestamp_order(value) -> datetime:
    stamp = valid_timestamp(value)
    return (datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
            if stamp else datetime.max.replace(tzinfo=timezone.utc))


def summary_points(value: str) -> list[str]:
    value = str(value or "").replace("\\n", "\n")
    parts = re.split(r"\n+|\s+(?=[・•●▪◦])", value)
    return [normalise_text(re.sub(r"^[\s・•●▪◦\-*]+", "", part))
            for part in parts if normalise_text(re.sub(r"^[\s・•●▪◦\-*]+", "", part))]


def verified_quote(quote, article: dict, *, limit: int = 500, supplied_limit: int | None = None) -> str | None:
    if not isinstance(quote, str):
        return None
    quote = normalise_text(quote)
    if (not 8 <= len(quote) <= limit or not article.get("content")
            or (article.get("content_quality") or {}).get("fallback") == "minimal"):
        return None
    text = body_text(article["content"])
    if supplied_limit is not None:
        text = text[:supplied_limit]
    return quote if quote in normalise_text(text) else None


def validate_summary_evidence(article: dict, raw) -> list[dict]:
    points = set(summary_points(article.get("summary", "")))
    if not isinstance(raw, list):
        return []
    result, seen = [], set()
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        point = normalise_text(item.get("point"))
        quote = verified_quote(item.get("quote"), article)
        aid = str(item.get("article_id") or article.get("id") or "")
        if point not in points or point in seen or not quote or aid != article.get("id"):
            continue
        result.append({"point": point, "quote": quote, "article_id": aid, "verified": True})
        seen.add(point)
    return result


def valid_timestamp(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


def update_provenance(article: dict, old_record: dict | None = None, *, now: str | None = None) -> dict:
    """A rewrite/observation does not imply a body update. Legacy age stays unknown."""
    old_record = old_record or {}
    content = article.get("content") or ""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
    old_digest = old_record.get("content_hash")
    if old_digest is None and old_record.get("content"):
        old_digest = hashlib.sha256(old_record["content"].encode("utf-8")).hexdigest()
    observed = valid_timestamp(article.get("fetched_at"))
    if digest == old_digest:
        changed_at = valid_timestamp(old_record.get("content_updated_at"))
    elif digest and not article.get("content_retention"):
        changed_at = observed or valid_timestamp(now)
    else:
        changed_at = valid_timestamp(old_record.get("content_updated_at"))
    fallback = (article.get("content_quality") or {}).get("fallback", "unknown")
    provenance = {
        "published_at": valid_timestamp(article.get("date")),
        "fetched_at": observed or valid_timestamp(old_record.get("fetched_at")),
        "content_updated_at": changed_at,
        "content_hash": digest,
        "evidence_status": "full" if fallback == "none" else "partial" if fallback.startswith("rss-") or fallback == "minimal" else "unknown",
    }
    article.update(provenance)
    return provenance
