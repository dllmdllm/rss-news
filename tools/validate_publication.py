"""Read-only checks of generated data, evidence and actual analysis inputs."""
import argparse
import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.analysis_input import input_hash
from src.article_urls import valid_article_url
from src.index_projection import project_article
from src.summary_contract import validate_record as validate_summary_record

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
SENTIMENTS = {"positive", "negative", "neutral"}
TIMES = ("date", "published_at", "source_modified_at", "fetched_at", "content_updated_at")


def source_outlet(source):
    """Known publisher feed groups; unknown names retain their own identity.

    Keep aligned with src.article_evidence.source_outlet without importing its
    third-party/API dependencies into this offline publication gate.
    """
    source = normalise(str(source or ""))
    if source in {"星島頭條", "星島 即時中國", "星島 即時國際"}:
        return "星島"
    return re.sub(r"^(RTHK|明報|HK01|東網|HuffPost|Now) .+", r"\1", source)


def normalise(value):
    return re.sub(r"\s+", " ", value).strip()


class BodyText(HTMLParser):
    """Concatenate inline text without inventing boundaries or hidden evidence."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = []
        self.media = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden.append(tag)
        if not self.hidden and tag == "img" and dict(attrs).get("src", "").strip():
            self.media = True

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(content):
    parser = BodyText()
    parser.feed(content)
    return normalise("".join(parser.parts)), parser.media


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"{path.name}: cannot read valid JSON: {exc}") from exc


def safe_id(value, context):
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ValueError(f"{context}: unsafe or missing article id")
    return value


def timestamps(record, context):
    for field in TIMES:
        value = record.get(field)
        if value is None:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
            if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timezone required")
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{context}: {field} must be an aware ISO timestamp or null") from exc


def article_map(payload, context):
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ValueError(f"{context}: expected object with articles list")
    result = {}
    for article in payload["articles"]:
        if not isinstance(article, dict):
            raise ValueError(f"{context}: article must be an object")
        aid = safe_id(article.get("id"), context)
        if aid in result:
            raise ValueError(f"{context}: duplicate article id {aid}")
        result[aid] = article
        timestamps(article, f"{context}/{aid}")
    return result


def summary_points(value):
    if not isinstance(value, str):
        return set()
    parts = re.split(r"\n+|\s+(?=[・•●▪◦])", value.replace("\\n", "\n"))
    return {normalise(re.sub(r"^[\s・•●▪◦\-*]+", "", part)) for part in parts} - {""}


def quote_supported(quote, aid, bodies, context, fallbacks=None):
    safe_id(aid, context)
    if aid not in bodies:
        raise ValueError(f"{context}: evidence references inactive article {aid}")
    if (fallbacks or {}).get(aid) == "minimal":
        raise ValueError(f"{context}: generated minimal placeholder is not source evidence for {aid}")
    if not isinstance(quote, str) or not normalise(quote) or normalise(quote) not in bodies[aid]:
        raise ValueError(f"{context}: quote is not exact current body evidence for {aid}")


def validate_site(docs: Path, require_current=False):
    """Raise descriptive ValueError before publishing an inconsistent snapshot."""
    data = Path(docs) / "data"
    full = read_json(data / "articles.json")
    index = read_json(data / "articles_index.json")
    articles = article_map(full, "articles.json")
    indexed = article_map(index, "articles_index.json")
    for label, payload in (("articles.json", full), ("articles_index.json", index)):
        version = payload.get("schema_version")
        if require_current and version != 2:
            raise ValueError(f"{label}: production requires schema_version 2")
        if version is not None and version != 2:
            raise ValueError(f"{label}: unsupported schema_version {version}")
    if set(articles) != set(indexed):
        raise ValueError("full/index article IDs differ")
    for field in ('updated', 'sources', 'trending_topics', 'display_window_hours', 'retention_window_hours'):
        if full.get(field) != index.get(field):
            raise ValueError(f'full/index {field} contract mismatch')
    if require_current and not articles:
        raise ValueError("production snapshot has zero articles")
    for aid, article in articles.items():
        if (require_current or 'url' in article) and not valid_article_url(article.get('url')):
            raise ValueError(f'{aid}: invalid article URL')
        if 'url' in indexed[aid] and not valid_article_url(indexed[aid]['url']):
            raise ValueError(f'{aid}: invalid index article URL')
        if type(article.get("score")) is not int or not isinstance(article.get("tags"), list) or article.get("sentiment") not in SENTIMENTS:
            raise ValueError(f"{aid}: invalid score/tags/sentiment analysis contract")
        if not all(isinstance(tag, str) for tag in article["tags"]):
            raise ValueError(f"{aid}: tags must contain strings")
        if index.get("schema_version") == 2 and indexed[aid].get("sentiment") != article["sentiment"]:
            raise ValueError(f"{aid}: index sentiment contract mismatch")
    content_dir = data / "content"
    sidecars = {path.stem: path for path in content_dir.glob("*.json")}
    if set(sidecars) != set(articles):
        raise ValueError(f"content sidecar IDs differ: missing={sorted(set(articles)-set(sidecars))[:5]}, stale={sorted(set(sidecars)-set(articles))[:5]}")
    bodies = {}
    hashes = {}
    fallbacks = {}
    for aid, path in sidecars.items():
        if path.resolve().parent != content_dir.resolve():
            raise ValueError(f"{aid}: sidecar escapes content directory")
        record = read_json(path)
        if not isinstance(record, dict) or type(record.get("version")) is not int or record["version"] != 1:
            raise ValueError(f"{aid}: sidecar requires version 1")
        content = record.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{aid}: empty or invalid sidecar content")
        text, media = body_text(content)
        if not text and not media:
            raise ValueError(f"{aid}: sidecar has no real content")
        timestamps(record, f"sidecar/{aid}")
        bodies[aid] = text
        hashes[aid] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if record.get('content_hash') != hashes[aid] or articles[aid].get('content_hash') != hashes[aid]:
            raise ValueError(f'{aid}: metadata/sidecar content_hash must equal actual body SHA256')
        article = articles[aid]
        if article.get('summary_format') in (1, 2):
            version = article.get('analysis_version')
            if (not isinstance(version, str) or not re.fullmatch(r'p-[a-f0-9]{8,64}', version)
                    or input_hash(dict(article, content=content), version) != article.get('analysis_input_hash')):
                raise ValueError(f'{aid}: analysis input hash must match actual published body and metadata')
        quality = record.get("quality")
        fallbacks[aid] = quality.get("fallback") if isinstance(quality, dict) else None
    for aid, article in articles.items():
        validate_summary_record(article)
        for quote in article.get('key_sentences') or []:
            quote_supported(quote, aid, bodies, f'{aid}/key_sentences', fallbacks)
        if article.get('key_sentences') and article.get('key_sentences_content_hash') != hashes[aid]:
            raise ValueError(f'{aid}: key sentences require current body hash')
        if "summary_evidence" not in article:
            continue
        evidence = article["summary_evidence"]
        if not isinstance(evidence, list):
            raise ValueError(f"{aid}: summary_evidence must be a list")
        points = summary_points(article.get("summary"))
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("point"), str) or normalise(item["point"]) not in points:
                raise ValueError(f"{aid}: evidence point is not a current summary point")
            if item.get("article_id", aid) != aid:
                raise ValueError(f"{aid}: summary evidence must reference its own article")
            quote_supported(item.get("quote"), aid, bodies, f"{aid}/summary_evidence", fallbacks)
            if 'quotes' in item:
                if not isinstance(item['quotes'], list) or not 1 <= len(item['quotes']) <= 2:
                    raise ValueError(f'{aid}: invalid summary sentence evidence')
                for quote in item['quotes']:
                    quote_supported(quote, aid, bodies, f'{aid}/summary_evidence', fallbacks)
    for aid, article in articles.items():
        for key, value in project_article(article).items():
            # The prior published index predates source-quote/translation
            # projection. Keep it readable during an approved source rollout;
            # the new writer's explicit24h contract requires every new field.
            if (full.get('display_window_hours') is None and key not in indexed[aid]
                    and key in {'summary_evidence', 'translation_status', 'translation_reason',
                                'title_translation_status', 'analysis_status', 'body_status'}):
                continue
            if indexed[aid].get(key, 'unavailable' if key == 'summary_evidence_status' else None) != value:
                raise ValueError(f'{aid}: index {key} contract mismatch')
    panel_path = data / "panel_digests.json"
    if panel_path.exists():
        panels = read_json(panel_path)
        if not isinstance(panels, dict):
            raise ValueError("panel_digests.json: expected object")
        for cid, entry in panels.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("digest"), dict):
                raise ValueError(f"panel/{cid}: expected digest object")
            for field in ("shared_facts", "new_developments", "differing_accounts"):
                if field not in entry["digest"]:
                    continue
                claims = entry["digest"][field]
                if not isinstance(claims, list):
                    raise ValueError(f"panel/{cid}/{field}: expected claims list")
                for claim in claims:
                    if not isinstance(claim, dict) or not isinstance(claim.get("text"), str) or not claim["text"].strip() or not isinstance(claim.get("evidence"), list) or not claim["evidence"]:
                        raise ValueError(f"panel/{cid}/{field}: claim requires text and evidence")
                    sources = set()
                    for support in claim["evidence"]:
                        if not isinstance(support, dict):
                            raise ValueError(f"panel/{cid}/{field}: evidence must be an object")
                        quote_supported(support.get("quote"), support.get("article_id"), bodies, f"panel/{cid}/{field}", fallbacks)
                        if support.get("verified") is not True or support.get("content_hash") != hashes[support["article_id"]]:
                            raise ValueError(f"panel/{cid}/{field}: support requires verified current body hash")
                        timestamps(support, f"panel/{cid}/{field}")
                        source_article = articles[support["article_id"]]
                        if source_article.get("source"):
                            sources.add(source_outlet(source_article["source"]))
                        for identity in ("source", "url"):
                            if identity in support and support[identity] != source_article.get(identity):
                                raise ValueError(f"panel/{cid}/{field}: evidence {identity} differs from active article")
                    statement = normalise(claim["text"])
                    quotes = [normalise(support["quote"]) for support in claim["evidence"]]
                    if statement not in quotes or any(statement not in quote for quote in quotes):
                        raise ValueError(f"panel/{cid}/{field}: statement must be verbatim in every support quote and equal one quote")
                    if field == "shared_facts" and len(sources) < 2:
                        raise ValueError(f"panel/{cid}/{field}: shared fact needs two distinct active sources")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docs", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "docs")
    parser.add_argument("--require-current", action="store_true")
    args = parser.parse_args()
    try:
        validate_site(args.docs, args.require_current)
    except ValueError as exc:
        parser.exit(1, f"Publication validation failed: {exc}\n")
    print("Publication snapshot validated")


if __name__ == "__main__":
    main()
