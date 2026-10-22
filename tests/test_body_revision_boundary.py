"""Replay the published retained-RSS failure without source or provider requests."""
import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import build
from src import scrape
from src.article_evidence import update_provenance
from src.media_qa import inventory


FIXTURE = Path(__file__).parent / 'fixtures/retained_rss_revision_20261022.json'
digest = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()


@pytest.fixture
def retained(monkeypatch, tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    old = copy.deepcopy(fixture['article'])
    old['date'] = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(build, 'CONTENT_DIR', tmp_path / 'content')
    monkeypatch.setattr(build, 'DATA_DIR', tmp_path)
    build.CONTENT_DIR.mkdir()
    (build.CONTENT_DIR / (old['id'] + '.json')).write_text(
        json.dumps(fixture['sidecar']), encoding='utf-8')
    row = build._merge_missing_sources([], [old], {})[0]
    row['content_quality'] = fixture['sidecar']['quality']
    assert row['content_hash'] == digest(row['content'])
    return old, row


def deny(monkeypatch):
    calls = []
    async def blocked(session, url):
        calls.append(url)
        raise scrape.SourceAccessDenied('HTTP 403')
    monkeypatch.setattr(scrape, '_fetch_html', blocked)
    return calls


def ready_for_enrichment(rows, old):
    build._retain_verified_content(rows, [old])
    for row in rows:
        build._prepare_article_media(row)
    return inventory(rows)


def test_denied_retained_rss_without_fresh_feed_keeps_verified_bytes(retained, monkeypatch):
    old, row = retained
    before = row['content']
    calls = deny(monkeypatch)
    rows = asyncio.run(scrape.scrape_all([row]))
    assert rows[0]['content'] == before
    assert rows[0]['content_hash'] == old['content_hash']
    assert rows[0]['content_quality']['fallback'] == 'rss-blocked'
    assert rows[0]['body_status'] == 'failed'
    assert rows[0]['content_retention']['latest_error'] == 'HTTP 403'
    assert ready_for_enrichment(rows, old)['records'] == 1
    asyncio.run(scrape.scrape_all(rows))
    assert len(calls) == 1, 'an access denial must not acquire another retry'


@pytest.mark.parametrize('fresh', ['rss', 'full'])
def test_legitimate_replacement_rebinds_body_and_invalidates_old_quotes(retained, monkeypatch, fresh):
    old, row = retained
    row['content_retention'] = {'scraped_at': old['content_updated_at'], 'latest_error': 'older failure'}
    row['fetched_at'] = datetime.now(timezone.utc).isoformat()
    if fresh == 'rss':
        deny(monkeypatch)
        row['rss_content'] = '<p>來源這次提供了不同的 RSS 摘要。</p>'
    else:
        async def fetch(session, url): return '<article>New full body.</article>'
        monkeypatch.setattr(scrape, '_fetch_html', fetch)
        monkeypatch.setattr(scrape, '_process_html_sync', lambda *args: ('<p>新的完整來源正文。</p>', None))
    rows = asyncio.run(scrape.scrape_all([row]))
    current = rows[0]
    assert current['content_hash'] == digest(current['content']) != old['content_hash']
    assert not current.get('key_sentences_content_hash')
    assert not current.get('summary_evidence')
    assert current['summary_evidence_status'] == 'unavailable'
    assert 'content_retention' not in current
    provenance = update_provenance(current, {'content_hash': old['content_hash'],
                                            'content_updated_at': old['content_updated_at']})
    assert provenance['content_updated_at'] == current['fetched_at']
    assert ready_for_enrichment(rows, old)['records'] == 1


def test_unchanged_corrupt_deferred_body_still_fails_strict_inventory(retained, monkeypatch):
    old, row = retained
    row['content_hash'] = '0' * 64
    monkeypatch.setattr(scrape, 'legacy_source_limit', lambda source: 1)
    async def fetch(session, url): return '<article>New article.</article>'
    monkeypatch.setattr(scrape, '_fetch_html', fetch)
    monkeypatch.setattr(scrape, '_process_html_sync', lambda *args: ('<p>New article.</p>', None))
    first = dict(id='first', source=row['source'], url='https://example.com/first', title='First')
    rows = asyncio.run(scrape.scrape_all([first, row]))
    assert rows[1]['body_status'] == 'pending-budget'
    assert rows[1]['content_hash'] == '0' * 64
    with pytest.raises(ValueError, match='current body hash mismatch'):
        ready_for_enrichment(rows, old)


@pytest.mark.parametrize('corrupt', [False, True])
def test_retention_selects_the_saved_body_revision_without_accepting_bad_hash(retained, corrupt):
    old, row = retained
    full = '<p>Previously captured complete source body.</p>'
    record = {'version': 1, 'content': full, 'quality': {'fallback': 'none'},
              'content_hash': '0' * 64 if corrupt else digest(full)}
    (build.CONTENT_DIR / (old['id'] + '.json')).write_text(json.dumps(record), encoding='utf-8')
    old['content_hash'] = record['content_hash']
    fallback_hash = row['content_hash']
    build._retain_verified_content([row], [old])
    assert row['content_hash'] == (fallback_hash if corrupt else digest(full))
    assert row['content'] != full if corrupt else row['content'] == full
    assert inventory([row])['records'] == 1
