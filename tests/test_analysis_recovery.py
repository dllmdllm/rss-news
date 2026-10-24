"""Recovery and admission regressions without provider or notification calls."""
import asyncio
import json

import pytest

import build
import src.analyse as analyse
from src.summary_contract import normalise_points


def article(aid):
    return {'id': aid, 'title': '車站服務安排', 'source': 'Fixture',
            'date': '2026-10-23T09:00:00+00:00', 'url': 'https://example.org/' + aid,
            'content': '<p>車站今晚提早關門，旅客需要改乘巴士。</p>'}


def result():
    return dict(score=6, tags=[], sentiment='neutral', headline_fit=None,
                topic='', event_type='', entities={}, key_sentences=[], upcoming_events=[],
                version=analyse.ANALYSIS_VERSION,
                **normalise_points([{'sentences': ['車站今晚提早關門。', '旅客需要改乘巴士。'],
                    'quotes': ['車站今晚提早關門，旅客需要改乘巴士。'] * 2}], True))


def published(row, *, empty=False):
    fields = result() if not empty else dict(score=5, **normalise_points([], True))
    return dict(row, **{key: value for key, value in fields.items() if key != 'version'},
                analysis_version=analyse.ANALYSIS_VERSION,
                analysis_input_hash=analyse.analysis_input_hash(row))


def offline(monkeypatch, tmp_path):
    path = tmp_path / 'analyses.json'
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline-test')
    monkeypatch.setattr(analyse, 'CACHE_PATH', path)
    return path


def test_cancel_flushes_completed_analysis_below_incremental_threshold(monkeypatch, tmp_path):
    path = offline(monkeypatch, tmp_path)
    row = article('completed')

    async def scenario():
        completed = asyncio.Event()

        async def interrupted(session, batch, sem, cache, lock, counter):
            await analyse._apply_results(batch, [result()], cache, lock, counter)
            completed.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(analyse, '_analyse_batch', interrupted)
        task = asyncio.create_task(analyse.analyse_all([row]))
        await asyncio.wait_for(completed.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    cache = json.loads(path.read_text(encoding='utf-8'))
    assert cache[row['id']]['summary'] == row['summary']
    assert not analyse._needs_full_analysis(cache[row['id']], row)


@pytest.mark.parametrize('empty', [False, True])
def test_valid_published_result_repairs_missing_cache_without_generation(monkeypatch, tmp_path, empty):
    path = offline(monkeypatch, tmp_path)
    row = published(article('published'), empty=empty)

    async def forbidden(*args):
        pytest.fail('matching published result must not be generated again')

    monkeypatch.setattr(analyse, '_analyse_batch', forbidden)
    asyncio.run(analyse.analyse_all([row]))
    cache = json.loads(path.read_text(encoding='utf-8'))
    assert not analyse._needs_full_analysis(cache[row['id']], row)
    assert row['analysis_status'] == 'available'
    assert 'content' not in cache[row['id']]


def test_published_result_of_corrected_input_is_not_restored(monkeypatch, tmp_path):
    path = offline(monkeypatch, tmp_path)
    row = published(article('correction'))
    row['content'] = '<p>車站將維持正常服務。</p>'
    calls = []

    async def failed(session, batch, *args):
        calls.extend(a['id'] for a in batch)

    monkeypatch.setattr(analyse, '_analyse_batch', failed)
    asyncio.run(analyse.analyse_all([row]))
    assert calls == ['correction']
    assert row['summary'] == ''
    assert json.loads(path.read_text(encoding='utf-8')) == {}


def test_missing_summary_uses_source_cap_without_regenerating_valid_legacy(monkeypatch, tmp_path):
    path = offline(monkeypatch, tmp_path)
    legacy, missing = article('legacy'), article('missing')
    cached = dict(summary='・車站今晚提早關門', score=6, tags=[], sentiment='neutral',
                  version='p-deadbeef',
                  input_hash=analyse.analysis_input_hash(legacy, version='p-deadbeef'))
    path.write_text(json.dumps({'legacy': cached}), encoding='utf-8')
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 1)
    calls = []

    async def failed(session, batch, *args):
        calls.extend(a['id'] for a in batch)

    monkeypatch.setattr(analyse, '_analyse_batch', failed)
    asyncio.run(analyse.analyse_all([legacy, missing]))
    assert calls == ['missing']
    assert legacy['summary'] == cached['summary']
    assert legacy['analysis_status'] == 'available'
    assert missing['analysis_status'] == 'failed'


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('compact', [False, True])
def test_previous_publication_can_repair_cache_after_fresh_fetch(monkeypatch, tmp_path, empty, compact):
    path = offline(monkeypatch, tmp_path)
    previous = published(article('fetched'), empty=empty)
    if compact:
        previous.update(summary_format=1, summary_status='limited' if empty else 'short',
                        summary='' if empty else '・車站提早關門', summary_evidence=[])
        previous.pop('summary_points', None)
    incoming = article('fetched')
    build._apply_fallback_summaries([incoming], [previous])

    async def forbidden(*args):
        pytest.fail('verified previous output must repair the cache without generation')

    monkeypatch.setattr(analyse, '_analyse_batch', forbidden)
    asyncio.run(analyse.analyse_all([incoming]))
    assert incoming['summary'] == previous['summary']
    assert incoming['analysis_status'] == 'available'
    assert json.loads(path.read_text(encoding='utf-8'))['fetched']['summary'] == previous['summary']
