"""More collected metadata cannot silently enlarge paid AI admission."""
import asyncio
import copy

import build
from src import analyse
from src.enrichment_budget import legacy_source_limit
from src.summary_contract import normalise_points


def rows(count=5):
    return [dict(id=f'a{i}', title=f'Story {i}', source='Test source', date='2026-10-22T10:00:00Z',
                 url=f'https://example.com/{i}', content='<p>Original source paragraph.</p>') for i in range(count)]


def test_analysis_budget_counts_only_uncached_jobs_and_advances_backlog(monkeypatch):
    store, requested = {}, []
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline-fake')
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 2)
    monkeypatch.setattr(analyse, 'load_cache', lambda: copy.deepcopy(store))
    monkeypatch.setattr(analyse, 'save_cache', lambda value: (store.clear(), store.update(copy.deepcopy(value))))

    async def fake_batch(session, batch, sem, cache, lock, counter):
        requested.extend(a['id'] for a in batch)
        parsed = [dict(**normalise_points([], True), score=5, tags=[], sentiment='neutral', topic='',
                       event_type='', headline_fit=None, version=analyse.ANALYSIS_VERSION) for _ in batch]
        await analyse._apply_results(batch, parsed, cache, lock, counter)

    monkeypatch.setattr(analyse, '_analyse_batch', fake_batch)
    first = asyncio.run(analyse.analyse_all(rows()))
    assert requested == ['a0', 'a1']
    assert len(first) == 5
    assert [a['analysis_status'] for a in first] == ['available', 'available', 'pending-budget', 'pending-budget', 'pending-budget']
    assert all(not a['summary'] for a in first[2:])
    second = asyncio.run(analyse.analyse_all(rows()))
    assert requested == ['a0', 'a1', 'a2', 'a3']
    assert second[-1]['analysis_status'] == 'pending-budget'
    third = asyncio.run(analyse.analyse_all(rows()))
    assert requested == ['a0', 'a1', 'a2', 'a3', 'a4']
    assert all(a['analysis_status'] == 'available' for a in third)


def test_legacy_budgets_are_not_changed_by_expanded_collection():
    from src.feeds import MAX_ITEMS_PER_FEED, RSS_FEEDS
    for feed in RSS_FEEDS:
        assert legacy_source_limit(feed['name']) <= feed.get('max_items', MAX_ITEMS_PER_FEED)


def test_metadata_only_fallback_defers_ai_without_spending(monkeypatch):
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline-fake')
    monkeypatch.setattr(analyse, 'load_cache', lambda: {})
    monkeypatch.setattr(analyse, 'save_cache', lambda value: None)
    async def forbidden(*args, **kwargs):
        raise AssertionError('minimal fallback must not cause a paid request')
    monkeypatch.setattr(analyse, '_analyse_batch', forbidden)
    articles = rows(1)
    articles[0]['content_quality'] = {'fallback': 'minimal'}
    result = asyncio.run(analyse.analyse_all(articles))
    assert result[0]['analysis_status'] == 'pending-source'
    assert result[0]['summary'] == ''


def test_build_records_partial_translation_without_success(monkeypatch, tmp_path):
    from test_build import _isolate_core
    _isolate_core(monkeypatch, tmp_path)

    async def partial(articles):
        return {'ok': False, 'status': 'partial', 'eligible': 2, 'cached': 1,
                'translated': 0, 'failed': 0, 'deferred': 1, 'unavailable': 0, 'partial': 0}

    monkeypatch.setattr(build, 'translate_english_content', partial)
    asyncio.run(build.main())
    step = build._build_status['translate']
    assert step['ok'] is False and step['status'] == 'partial'
    assert step['cached'] == 1 and step['deferred'] == 1
