"""No-provider regressions for final inputs, fair recovery and honest counters."""
import asyncio
import hashlib
import json
import re

import pytest

import build
from src import analyse, minimax_client
from src.analysis_input import input_hash
from src.analysis_statistics import collect_analysis_statistics
from tools.validate_publication import validate_site
from test_analysis_recovery import article, offline, result


def raw(count, *, invalid=False):
    if invalid:
        return json.dumps([{'idx': n + 1, 'summary': 'SECRET unstructured text'} for n in range(count)])
    return json.dumps([{'idx': n + 1, 'score': 6, 'summary_limited': True,
        'summary_points': [{'sentences': ['車站今晚提早關門。', '旅客需要改乘巴士。'],
                            'quotes': ['車站今晚提早關門，旅客需要改乘巴士。'] * 2}]} for n in range(count)])


@pytest.mark.parametrize('content,quality', [
    ('<img src="https://example.org/photo.jpg">', {'fallback': 'rss-budget'}),
    ('<p>暫時未能擷取全文。</p>', {'fallback': 'minimal'}),
    ('<script>not source text</script>', {'fallback': 'none'}),
    (None, {}),
])
def test_empty_or_minimal_input_never_calls_provider(monkeypatch, tmp_path, content, quality):
    path = offline(monkeypatch, tmp_path)
    row = article('empty'); row.update(content=content, content_quality=quality)
    async def forbidden(*args): pytest.fail('no source body must not spend analysis')
    monkeypatch.setattr(analyse, '_post_messages', forbidden)
    with collect_analysis_statistics() as stats:
        asyncio.run(analyse.analyse_all([row]))
    assert row['summary'] == '' and row['analysis_status'] == 'pending-source'
    assert stats.snapshot()['requests']['attempted'] == 0
    assert stats.snapshot()['queue']['admitted'] == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_image_only_finalization_is_before_analysis_and_idempotent(monkeypatch, tmp_path):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(build, 'CONTENT_DIR', tmp_path / 'content')
    build.CONTENT_DIR.mkdir()
    row = article('hero')
    row.update(content='<img src="https://example.org/hero.jpg">', thumbnail='https://example.org/hero.jpg',
               content_quality={'fallback': 'rss-budget'})
    row['content_hash'] = hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
    before = analyse.analysis_input_hash(row)
    flags = build._finalize_article_body(row)
    after = analyse.analysis_input_hash(row)
    assert before != after and row['content_quality']['fallback'] == 'minimal'
    assert flags['minimal'] and flags['deduped']
    build.media_inventory([row])
    async def forbidden(*args): pytest.fail('finalized placeholder must not reach provider')
    monkeypatch.setattr(analyse, '_post_messages', forbidden)
    asyncio.run(analyse.analyse_all([row]))
    body = row['content']
    build._write_content_sidecars([row])
    assert row['content'] == body and analyse.analysis_input_hash(row) == after
    assert row['summary'] == ''


def test_caption_and_image_order_survive_repeated_preparation(monkeypatch, tmp_path):
    monkeypatch.setattr(build, 'CONTENT_DIR', tmp_path / 'content')
    row = article('photo')
    row.update(thumbnail='https://example.org/one.jpg',
        content='<figure><img src="https://example.org/one.jpg"><figcaption>原站圖說。</figcaption></figure>'
                '<p>正文繼續。</p><img src="https://example.org/two.jpg">', content_quality={'fallback': 'none'})
    original = row['content']
    build._finalize_article_body(row); first = row['content']
    build._finalize_article_body(row)
    assert first == row['content'] == original


def test_fair_admission_rotates_sources_and_old_new_without_more_slots(monkeypatch, tmp_path):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 2)
    rows = []
    for source in ('A', 'B'):
        for backlog in (False, True):
            for n in range(3):
                row = article(f'{source}-{backlog}-{n}')
                row.update(source=source, _analysis_backlog=backlog)
                rows.append(row)
    calls = []
    async def capture(session, batch, *args): calls.extend(a['id'] for a in batch)
    monkeypatch.setattr(analyse, '_analyse_batch', capture)
    with collect_analysis_statistics() as stats:
        asyncio.run(analyse.analyse_all(rows))
    assert calls == ['A-True-0', 'B-True-0', 'A-False-0', 'B-False-0']
    assert stats.snapshot()['admission']['missing'] == 4
    assert sum(r['analysis_status'] == 'pending-budget' for r in rows) == 8


def test_fallback_gets_a_slot_before_queued_original_batches(monkeypatch, tmp_path):
    offline(monkeypatch, tmp_path)
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 100)
    rows = [article(f'a{n}') for n in range(100)]
    events = []
    async def scenario():
        fallback_started = asyncio.Event()
        async def post(session, text, max_tokens, timeout):
            count = int(re.search(r'分析以下 (\d+) 篇', text)[1])
            events.append('fallback' if count == 1 else 'batch')
            if count == 1:
                fallback_started.set()
            elif 'https://' in text:
                pytest.fail('the prompt should not need a source URL')
            elif '### 第 1 篇' in text and '標題：車站服務安排' in text and len(events) == 1:
                # Compact summaries are now valid. Use an actual prose/schema
                # failure so this still exercises bounded fallback scheduling.
                broken = json.loads(raw(count, invalid=True))
                for item in broken: item['summary'] *= 4
                return json.dumps(broken), {}, 200
            else:
                await fallback_started.wait()
            await asyncio.sleep(0)
            return raw(count), {}, 200
        monkeypatch.setattr(analyse, '_post_messages', post)
        with collect_analysis_statistics() as stats:
            await asyncio.wait_for(analyse.analyse_all(rows), 2)
            report = stats.snapshot()
        return report
    report = asyncio.run(scenario())
    assert events.index('fallback') <= 5
    assert report['peak_active_requests'] <= analyse.ANALYSE_CONCURRENCY
    assert report['results']['applied'] == 100 and report['results']['with_summary'] == 100
    assert report['results']['rejected_slots'] == 5
    assert report['requests']['attempted'] == report['requests']['returned'] == 25
    assert report['queue']['completed'] == 100
    assert all(r.get('summary') for r in rows)


def test_cancellation_drains_requests_and_persists_completed_tail(monkeypatch, tmp_path):
    path = offline(monkeypatch, tmp_path)
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 100)
    rows = [article(f'a{n}') for n in range(60)]
    async def scenario():
        applied = asyncio.Event()
        original = analyse._apply_results
        first = [True]
        async def apply(*args):
            await original(*args)
            applied.set()
        async def post(session, text, max_tokens, timeout):
            if first[0]:
                first[0] = False
                return raw(5), {}, 200
            await asyncio.Event().wait()
        monkeypatch.setattr(analyse, '_apply_results', apply)
        monkeypatch.setattr(analyse, '_post_messages', post)
        with collect_analysis_statistics() as stats:
            task = asyncio.create_task(analyse.analyse_all(rows))
            await asyncio.wait_for(applied.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            return stats.snapshot()
    report = asyncio.run(scenario())
    cache = json.loads(path.read_text(encoding="utf-8"))
    assert len(cache) == 5 and report['results']['applied'] == 5
    assert report['requests']['cancelled'] > 0 and report['queue']['not_started'] > 0
    assert report['requests']['attempted'] == report['requests']['returned'] + report['requests']['cancelled']
    assert all(not analyse._needs_full_analysis(cache[r['id']], r) for r in rows[:5])


def test_statistics_explain_invalid_slots_without_storing_raw_output():
    with collect_analysis_statistics() as stats:
        assert analyse._parse_batch(raw(5, invalid=True), 5, require_rich=True) == [None] * 5
        assert analyse._parse_batch('PRIVATE invalid JSON', 5, require_rich=True) is None
        report = stats.snapshot()
    assert report['results']['rejected_slots'] == 5
    assert report['results']['whole_batch_rejections'] == 1
    assert report['validation_rejections']['missing_rich_points'] == 5
    assert 'SECRET' not in json.dumps(report) and 'PRIVATE' not in json.dumps(report)


def test_provider_metadata_is_bounded_and_allowlisted():
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self, **kwargs):
            return {'stop_reason': 'PRIVATE secret', 'usage': {'input_tokens': 12, 'output_tokens': True,
                'untrusted': 'PRIVATE', 'cache_read_input_tokens': 999}, 'content': [{'type': 'text', 'text': 'data'}]}
    class Session:
        def post(self, *args, **kwargs): return Response()
    metadata = {}
    assert asyncio.run(minimax_client.post_messages(Session(), system='prompt', user_text='body', max_tokens=1200,
        timeout=30, metadata=metadata)) == ('data', {}, 200)
    assert metadata == {'http_status': 200, 'stop_reason': 'other', 'input_tokens': 12, 'text_blocks': 1}


def test_timeout_is_counted_without_logging_exception_text(monkeypatch):
    async def timeout(*args): raise TimeoutError('PRIVATE token')
    monkeypatch.setattr(analyse, '_post_messages', timeout)
    async def scenario():
        with collect_analysis_statistics() as stats:
            with pytest.raises(TimeoutError):
                await analyse._observed_post(None, [article('a')], '', 1200, 30, 'fallback')
            return stats.snapshot()
    report = asyncio.run(scenario())
    assert report['requests']['attempted'] == report['requests']['timed_out'] == 1
    assert report['requests']['returned'] == 0 and 'PRIVATE' not in json.dumps(report)


def test_published_rich_input_hash_is_recomputed_not_just_copied(monkeypatch, tmp_path):
    monkeypatch.setattr(build, 'DATA_DIR', tmp_path / 'data')
    monkeypatch.setattr(build, 'CONTENT_DIR', tmp_path / 'data/content')
    row = article('bound')
    build._finalize_article_body(row)
    asyncio.run(analyse._apply_results([row], [result()], {}, asyncio.Lock(), [0]))
    build.save_json([row], {})
    validate_site(tmp_path, require_current=True)
    assert input_hash(row, row['analysis_version']) == row['analysis_input_hash']
    for file in ('articles.json', 'articles_index.json'):
        path = tmp_path / 'data' / file
        payload = json.loads(path.read_text(encoding="utf-8")); payload['articles'][0]['title'] = 'Correction'
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match='analysis input hash'):
        validate_site(tmp_path, require_current=True)


def test_intentional_media_cleanup_rebinds_body_but_preserves_verified_text(monkeypatch, tmp_path):
    monkeypatch.setattr(build, 'CONTENT_DIR', tmp_path / 'content')
    row = article('fresh')
    row.update(thumbnail='https://example.org/hero.jpg', content_quality={'fallback':'none'})
    row['content'] = '<img src="https://example.org/hero.jpg">' + row['content']
    asyncio.run(analyse._apply_results([row], [result()], {}, asyncio.Lock(), [0]))
    before = hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
    row['content_hash'] = before
    row['key_sentences'] = ['車站今晚提早關門，旅客需要改乘巴士。']
    row['key_sentences_content_hash'] = before
    digest, summary = row['analysis_input_hash'], row['summary']
    build._finalize_article_body(row)
    build.media_inventory([row])
    assert row['content_hash'] != before
    assert row['analysis_input_hash'] == digest == analyse.analysis_input_hash(row)
    assert row['summary'] == summary and row['summary_evidence']
    assert row['key_sentences'] and row['key_sentences_content_hash'] == row['content_hash']
    stable = row['content_hash']
    build._finalize_article_body(row)
    assert row['content_hash'] == stable


def test_body_preparation_rejects_corrupt_cache_before_rebinding(monkeypatch, tmp_path):
    monkeypatch.setattr(build, 'CONTENT_DIR', tmp_path / 'content')
    row = article('corrupt'); row['content_hash'] = '0' * 64
    original = dict(row)
    with pytest.raises(ValueError, match='body hash mismatch'):
        build._finalize_article_body(row)
    assert row == original


def test_reusing_missing_body_rejects_corrupt_saved_record(monkeypatch):
    row = article('corrupt-saved'); row['content'] = None
    monkeypatch.setattr(build, '_load_old_content_record', lambda aid:
        {'content':'<p>saved source</p>', 'content_hash':'0' * 64})
    with pytest.raises(ValueError, match='retained body hash mismatch'):
        build._finalize_article_body(row)
    assert row['content'] is None and 'content_hash' not in row


def test_main_validates_fresh_prepared_body_before_analysis(monkeypatch, tmp_path):
    from test_build import _isolate_core
    _isolate_core(monkeypatch, tmp_path)
    async def fresh(rows):
        for row in rows:
            row['thumbnail'] = 'https://example.org/hero.jpg'
            row['content'] = '<img src="https://example.org/hero.jpg"><p>fresh source text</p>'
            row['content_quality'] = {'fallback': 'none'}
            row['content_hash'] = hashlib.sha256(row['content'].encode('utf-8')).hexdigest()
        return rows
    monkeypatch.setattr(build, 'scrape_all', fresh)
    asyncio.run(build.main())
    assert build._build_status['analyse']['ok']


@pytest.mark.parametrize('cached', [False, True])
def test_main_translates_hashed_body_then_analyzes_and_publishes_it(monkeypatch, tmp_path, cached):
    from test_build import _isolate_core
    from src import translate_content as tc
    _isolate_core(monkeypatch, tmp_path)
    monkeypatch.setattr(tc, 'MINIMAX_API_KEY', 'offline-mock')
    monkeypatch.setattr(tc, 'CACHE_PATH', tmp_path / 'translations.json')
    source = 'The station closes early tonight. Passengers must take a bus.'
    translated = '車站今晚提早關門，旅客需要改乘巴士。'
    async def fresh(rows):
        for row in rows:
            row.update(source='9to5Mac', content='<p>' + source + '</p>',
                       content_quality={'fallback': 'none'})
            row['content_hash'] = hashlib.sha256(row['content'].encode()).hexdigest()
        return rows
    if cached:
        tc.save_cache({'partial': {'version': tc.TRANSLATE_VERSION,
            'source_hash': tc._source_hash([source]), 'content': '<p>' + translated + '</p>'}})
    async def provider(*args, **kwargs):
        assert not cached
        return json.dumps([translated]), {}, 200
    async def analyzed(rows):
        assert translated in rows[0]['content']
        build.media_inventory(rows)
        await analyse._apply_results(rows, [result()], {}, asyncio.Lock(), [0])
        return rows
    monkeypatch.setattr(tc, 'post_messages', provider)
    monkeypatch.setattr(build, 'scrape_all', fresh)
    monkeypatch.setattr(build, 'translate_english_content', tc.translate_english_content)
    monkeypatch.setattr(build, 'analyse_all', analyzed)
    asyncio.run(build.main())
    payload = json.loads((build.DATA_DIR / 'articles.json').read_text(encoding='utf-8'))
    row = payload['articles'][0]
    sidecar = json.loads((build.CONTENT_DIR / 'partial.json').read_text(encoding='utf-8'))
    row['content'] = sidecar['content']
    assert row['summary'] and row['summary_evidence']
    assert row['analysis_input_hash'] == analyse.analysis_input_hash(row, version=row['analysis_version'])
    assert row['content_hash'] == sidecar['content_hash'] == hashlib.sha256(row['content'].encode()).hexdigest()
    assert build._core_saved
