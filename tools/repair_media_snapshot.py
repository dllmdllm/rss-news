"""Explicit, deterministic repair of the approved audit findings; no network/AI.

Only selected captured originals may replace bodies. Snapshot/fetch timestamps
remain unchanged; capture time and repair provenance are recorded separately.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.media_fields import repair_thumbnail, clean_unit_markers
from src.media_qa import atomic_save, inventory
from src.article_evidence import validate_summary_evidence, verified_quote
from src.scrape import _process_html_sync, _fix_graphic_tags, _remove_relative_images, _remove_leading_title, content_quality
from src.translate_content import _translation_units, _source_hash

CAPTURE_IDS = {'47b9f09a024d', '6d37f02a65f7', 'd1ccf0639b4f'}
MARKER_ID = 'f7bef5e0ede8'


def repair(docs, captures=None):
    data = Path(docs) / 'data'
    full = json.loads((data / 'articles.json').read_text(encoding='utf-8'))
    index = json.loads((data / 'articles_index.json').read_text(encoding='utf-8'))
    indexed = {a['id']: a for a in index['articles']}
    originals = {}
    if captures:
        audit = json.loads((Path(captures) / 'source-audit.json').read_text(encoding='utf-8'))
        originals = {s['original_sample']['id']: s['original_sample'] for s in audit['sources'] if s.get('original_sample')}
    now = datetime.now(timezone.utc).isoformat()
    changes = []
    staged_sidecars = []
    for article in full['articles']:
        aid = article['id']
        path = data / 'content' / (aid + '.json')
        sidecar = json.loads(path.read_text(encoding='utf-8'))
        original_body = sidecar['content']
        if article.get('content_hash') != hashlib.sha256(original_body.encode('utf-8')).hexdigest():
            raise ValueError('refuse inconsistent old body ' + aid)
        row = dict(article, content=original_body)
        old_thumbnail = row.get('thumbnail')
        repair_thumbnail(row)
        kinds = []
        if old_thumbnail != row.get('thumbnail'):
            kinds.append('same-article-thumbnail-repair')
        if aid in CAPTURE_IDS and captures:
            capture = Path(captures) / (aid + '-original.html')
            # Preserve CRLF when comparing the original capture hash.
            raw = capture.read_bytes().decode('utf-8')
            evidence = originals.get(aid) or {}
            if evidence.get('url') != article.get('url') or hashlib.sha256(raw.encode('utf-8')).hexdigest() != evidence.get('original_sha256'):
                raise ValueError('capture identity/hash mismatch ' + aid)
            body, og = _process_html_sync(raw, article['url'], not row.get('thumbnail'))
            if not body:
                raise ValueError('approved capture did not extract ' + aid)
            body = _remove_leading_title(_remove_relative_images(_fix_graphic_tags(body)), article['title'])
            row['content'] = body
            if not row.get('thumbnail'):
                row['thumbnail'] = og
            repair_thumbnail(row)
            row['content_quality'] = content_quality(body, source=article['source'], fallback='none')
            # A known public capture is not a new Windows observation.
            row['content_quality']['origin'] = 'public-audit-capture'
            row['content_quality']['completeness'] = 'extracted; original scope not universally certified'
            row['content_capture'] = {'original_hash': evidence['original_sha256'],
                'observed_at': datetime.fromtimestamp(capture.stat().st_mtime, timezone.utc).isoformat(),
                'audit_started_at': audit['checked_at'], 'executor': 'Mac'}
            row.pop('scrape_error', None)
            row.pop('content_retention', None)
            kinds.append('approved-captured-original-parser-repair')
        if aid == MARKER_ID and row.get('source') == '9to5Mac':
            cleaned, changed = clean_unit_markers(row['content'])
            if changed:
                row['content'] = cleaned
                row['content_quality'] = content_quality(cleaned, source=article['source'], fallback=(article.get('content_quality') or {}).get('fallback', 'unknown'))
                row['analysis_reuse_reason'] = 'unit-labels removed deterministically; analysis not regenerated'
                kinds.append('confirmed-unit-label-cleanup')
        if article['source'] in {'法庭線', 'The Collective HK'}:
            quality = dict(row.get('content_quality') or {})
            quality.update(origin='wordpress-public-posts', completeness='unverified')
            if quality != row.get('content_quality'):
                row['content_quality'] = quality
                kinds.append('public-api-completeness-label')
        if not kinds:
            continue
        row['summary_evidence'] = validate_summary_evidence(row, row.get('summary_evidence'))
        row['summary_evidence_status'] = 'available' if row['summary_evidence'] else 'unavailable'
        row['key_sentences'] = [s for s in row.get('key_sentences', []) if verified_quote(s, row)]
        row['content_hash'] = hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
        if row['content'] != original_body:
            row['content_repaired_at'] = now
            row['content_repair'] = kinds
        row['evidence_status'] = 'full' if row['content_quality'].get('fallback') == 'none' else 'partial'
        sidecar.update(content=row['content'], quality=row['content_quality'], content_hash=row['content_hash'],
                       evidence_status=row['evidence_status'], content_repaired_at=row.get('content_repaired_at'),
                       content_capture=row.get('content_capture'), scrape_error=row.get('scrape_error'), content_retention=row.get('content_retention'))
        staged_sidecars.append((path, sidecar))
        row.pop('content')
        article.clear(); article.update(row)
        indexed[aid].update({key: row[key] for key in indexed[aid] if key in row})
        for key in ('scrape_error', 'content_retention'):
            if key not in row:
                indexed[aid].pop(key, None)
        for key in ('content_repaired_at', 'content_capture', 'content_repair'):
            if key in row:
                indexed[aid][key] = row[key]
        changes.append({'id': aid, 'kinds': kinds})
    # Prevalidate all proposed bodies before mutating any generated file.
    changed = {path.stem: value for path, value in staged_sidecars}
    inventory([dict(a, content=(changed.get(a['id']) or json.loads((data/'content'/(a['id']+'.json')).read_text(encoding='utf-8')))['content']) for a in full['articles']])
    for path, sidecar in staged_sidecars:
        atomic_save(path, sidecar)
    atomic_save(data/'articles.json', full)
    atomic_save(data/'articles_index.json', index)
    cache_path = data/'translated_content.json'
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
        record = cache.get(MARKER_ID)
        if record:
            clean, changed = clean_unit_markers(record.get('content') or '')
            if changed:
                record['content'] = clean
                record['translated_hash'] = _source_hash([t.get_text(' ', strip=True) for t in _translation_units(clean)[1]])
                record['deterministic_unit_cleanup'] = True
                atomic_save(cache_path, cache)
    return changes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('docs')
    parser.add_argument('--captures')
    args = parser.parse_args()
    print(json.dumps(repair(args.docs, args.captures), ensure_ascii=False, indent=2))
