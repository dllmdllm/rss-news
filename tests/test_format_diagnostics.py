"""Root-aware parsing and bounded content-free diagnostics; no provider calls."""
import asyncio
import copy
import json

import pytest
from src import analyse, minimax_client
from src.analysis_statistics import collect_analysis_statistics
from src.summary_contract import normalise_points
from src import analysis_diagnostics as diagnostic
from src.summary_contract import ContractError
from src.minimax_client import post_messages as provider_post_messages


def complete():
    return {'idx': 1, 'score': 6, 'summary_limited': False, 'summary_points': [
        {'sentences': [f'部門公布第{i}項交通安排。', f'第{i}項措施將於明天開始實施。'],
         'quotes': [f'部門公布第{i}項交通安排。', f'第{i}項措施將於明天開始實施。']}
        for i in range(1, 6)]}


def changed(kind):
    obj = copy.deepcopy(complete())
    if kind == 'four-points': obj['summary_points'].pop()
    if kind == 'over-five': obj['summary_points'].append(copy.deepcopy(obj['summary_points'][0]))
    if kind == 'points-type': obj['summary_points'] = {}
    if kind == 'flag-type': obj['summary_limited'] = 'false'
    if kind == 'quote-count': obj['summary_points'][0]['quotes'].pop()
    if kind == 'quote-type': obj['summary_points'][0]['quotes'] = 'text'
    if kind == 'sentence-count': obj['summary_points'][0]['sentences'].pop()
    if kind == 'sentence-type': obj['summary_points'][0]['sentences'] = 'text'
    if kind == 'one-limited':
        obj['summary_points'] = obj['summary_points'][:1]
        obj['summary_limited'] = True
    if kind == 'empty-limited':
        obj['summary_points'] = []
        obj['summary_limited'] = True
    return obj


def article():
    return {'id': 'synthetic-only', 'title': '離線合成測試', 'source': 'Synthetic',
            'url': 'https://example.org/synthetic', 'date': '2026-10-24T00:00:00Z',
            'content': '<p>' + ''.join(s for p in complete()['summary_points'] for s in p['sentences']) + '</p>',
            'content_quality': {'fallback': 'none'}}



@pytest.fixture(autouse=True)
def forbid_providers(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('diagnosis must not call a provider')
    monkeypatch.setattr(analyse, '_post_messages', forbidden)
    monkeypatch.setattr(minimax_client, 'post_messages', forbidden)


@pytest.mark.parametrize('root', ['array', 'object'])
def test_complete_five_by_two_contract_stays_accepted(root):
    obj = complete()
    raw = json.dumps([obj] if root == 'array' else obj)
    parsed = analyse._parse_batch(raw, 1, require_rich=True)
    assert parsed and parsed[0]
    assert len(parsed[0]['summary_points']) == 5
    assert all(len(sentences) == 2 for sentences in parsed[0]['summary_points'])


def test_valid_limited_bare_object_is_not_mistaken_for_its_nested_array():
    # Existing source-insufficiency exception; not automatic flag coercion.
    obj = changed('one-limited')
    normalise_points(obj['summary_points'], True)
    parsed = analyse._parse_batch(json.dumps(obj), 1, require_rich=True)
    assert parsed and parsed[0], 'decode the root object before nested arrays'
    assert parsed[0]['summary_status'] == 'limited'


@pytest.mark.parametrize('kind,reason', [('four-points', 'point_count'),
    ('quote-count', 'quote_count'), ('sentence-count', 'sentence_count')])
def test_decoded_object_contract_rejection_is_not_a_json_decode_failure(kind, reason):
    raw = json.dumps(changed(kind))
    assert isinstance(json.loads(raw), dict)
    with collect_analysis_statistics() as stats:
        parsed = analyse._parse_batch(raw, 1, require_rich=True)
    assert not parsed or not parsed[0]
    snapshot = stats.snapshot()
    assert snapshot['validation_rejections'][reason] == 1
    assert snapshot['validation_rejections'].get('batch_shape_or_json', 0) == 0
    assert snapshot['results']['whole_batch_rejections'] == 0


@pytest.mark.parametrize('kind', ['four-points', 'over-five', 'points-type',
    'flag-type', 'quote-count', 'quote-type', 'sentence-count', 'sentence-type'])
def test_invalid_contracts_remain_rejected_without_padding_or_coercion(kind):
    parsed = analyse._parse_batch(json.dumps([changed(kind)]), 1, require_rich=True)
    assert not parsed or not parsed[0]


def test_malformed_json_is_still_rejected():
    with collect_analysis_statistics() as stats:
        parsed = analyse._parse_batch('[{"idx":1', 1, require_rich=True)
    assert parsed is None
    assert stats.snapshot()['validation_rejections']['batch_shape_or_json'] == 1


@pytest.mark.parametrize('root', ['array', 'object'])
def test_out_of_range_index_cannot_bind_a_result_to_another_article(root):
    obj = complete(); obj['idx'] = 2
    parsed = analyse._parse_batch(json.dumps([obj] if root == 'array' else obj), 1, require_rich=True)
    assert not parsed or not parsed[0]


def test_explicit_limited_empty_remains_valid_without_inventing_facts():
    parsed = analyse._parse_batch(json.dumps([changed('empty-limited')]), 1, require_rich=True)
    assert parsed[0]['summary_status'] == 'limited' and parsed[0]['summary'] == ''


def test_parser_correction_cannot_bypass_verbatim_source_support():
    obj = complete(); obj['summary_points'][0]['quotes'][1] = '這條引文不在提供的原文裏面。'
    parsed = analyse._parse_batch(json.dumps([obj]), 1, require_rich=True)
    row = article(); original_body = row['content']
    asyncio.run(analyse._apply_results([row], parsed, {}, asyncio.Lock(), [0]))
    assert len(row['summary_points']) == 4 and row['summary_status'] == 'limited'
    assert row['content'] == original_body
    assert obj['summary_points'][0]['sentences'][0] not in row['summary']


@pytest.mark.parametrize('wrap', ['fence', 'prose', 'quoted-brackets'])
def test_outer_document_preserves_safe_wrappers_and_quoted_brackets(wrap):
    obj = complete()
    if wrap == 'quoted-brackets': obj['tags'] = ['[text] {text}']
    raw = json.dumps([obj])
    if wrap == 'fence': raw = '```json\n' + raw + '\n```'
    if wrap == 'prose': raw = '分析結果如下：' + raw + ' 希望對你有幫助'
    assert analyse._parse_batch(raw, 1, require_rich=True)[0]


@pytest.mark.parametrize('suffix', [' {}', ' []', ' 42', ' true', ' null', ' "text"'])
def test_multiple_documents_are_not_silently_discarded(suffix):
    assert analyse._parse_batch(json.dumps([complete()]) + suffix, 1, require_rich=True) is None


@pytest.mark.parametrize('raw', ['42 []', '{"data":[]', '"broken [{"idx":1}]', 'null'])
def test_nested_or_scalar_data_cannot_replace_the_outer_document(raw):
    assert analyse._parse_batch(raw, 1, require_rich=True) is None


def test_parser_has_a_response_resource_guard_without_changing_output_budget():
    with collect_analysis_statistics() as stats:
        assert analyse._parse_batch(' ' * (analyse.MAX_ANALYSIS_RESPONSE_CHARS + 1), 1) is None
    assert stats.snapshot()['format_diagnostics']['counts']['envelope']['response_excess'] == 1


@pytest.mark.parametrize('idx', [True, False, 1.0, 1.9, None, '1.0', 0, 6])
def test_only_integral_in_range_article_indices_bind(idx):
    obj = complete(); obj['idx'] = idx
    assert analyse._parse_batch(json.dumps(obj), 1, require_rich=True) == [None]


def test_existing_single_object_and_integer_string_compatibility():
    obj = complete(); obj.pop('idx')
    assert analyse._parse_batch(json.dumps(obj), 1, require_rich=True)[0]
    assert analyse._parse_batch(json.dumps([obj]), 1, require_rich=True) == [None]
    obj['idx'] = '1'
    assert analyse._parse_batch(json.dumps([obj]), 1, require_rich=True)[0]


@pytest.mark.parametrize('kind,detail', [('four-points', 'complete_point_count'),
    ('over-five', 'points_range'), ('points-type', 'points_type'), ('flag-type', 'limited_type'),
    ('quote-count', 'quote_count'), ('quote-type', 'quote_type'),
    ('sentence-count', 'sentence_count'), ('sentence-type', 'sentence_structure')])
def test_distinct_contract_details_preserve_rejection_policy(kind, detail):
    with collect_analysis_statistics() as stats:
        assert analyse._parse_batch(json.dumps([changed(kind)]), 1, require_rich=True) == [None]
    report = stats.snapshot()['format_diagnostics']
    assert report['counts']['validation'] == {detail: 1}
    assert report['classification_version'] == 2


def test_four_retries_of_one_article_are_four_events_but_one_unique_rejection(monkeypatch):
    async def fake_post(*args):
        analyse.RESPONSE_METADATA.get().update(stop_reason='end_turn', text_blocks=1)
        return json.dumps([changed('four-points')]), {}, 200
    monkeypatch.setattr(analyse, '_post_messages', fake_post)
    monkeypatch.setattr(analyse, 'MAX_BACKOFF_BUDGET', 0)
    async def run():
        with collect_analysis_statistics() as stats:
            await analyse._analyse_one(None, article(), asyncio.Semaphore(5), {}, asyncio.Lock(), [0])
        return stats.snapshot()
    report = asyncio.run(run()); details = report['format_diagnostics']
    assert report['requests']['attempted'] == report['requests']['returned'] == 4
    assert report['queue']['started'] == 1
    assert details['counts']['validation']['complete_point_count'] == 4
    assert details['unique_articles']['contract_rejected'] == 1
    assert details['unique_contract_rejections'] == {'complete_point_count': 1}
    assert details['counts']['parse_outcomes'] == {'fallback|end_turn|contract_rejected': 4}
    assert details['counts']['point_count'] == {'4': 4}


def test_batch_and_fallback_unique_counts_do_not_guess_invalid_index_ownership():
    a, b = dict(article(), id='PRIVATE_A'), dict(article(), id='PRIVATE_B')
    bad = changed('four-points'); bad['idx'] = 2
    with collect_analysis_statistics() as stats:
        with diagnostic.observe_attempt([a, b], 'batch'):
            assert analyse._parse_batch(json.dumps([complete(), bad]), 2, require_rich=True)[1] is None
        with diagnostic.observe_attempt([b], 'fallback'):
            analyse._parse_batch(json.dumps([changed('four-points')]), 1, require_rich=True)
        with diagnostic.observe_attempt([a, b], 'batch'):
            invalid = complete(); invalid['idx'] = 99
            analyse._parse_batch(json.dumps([invalid, bad]), 2, require_rich=True)
        with diagnostic.observe_attempt([a, b], 'batch'):
            analyse._parse_batch('not JSON PRIVATE_SOURCE', 2, require_rich=True)
    report = stats.snapshot(); details = report['format_diagnostics']
    assert details['unique_articles']['contract_rejected'] == 1  # B, never guess A from idx99
    assert details['unique_articles']['binding_affected'] == 2
    assert details['unique_articles']['envelope_affected'] == 2
    assert 'PRIVATE_' not in json.dumps(report)


def test_one_article_can_have_two_distinct_reasons_without_becoming_two_articles():
    with collect_analysis_statistics() as stats:
        for kind in ['four-points', 'flag-type', 'four-points']:
            with diagnostic.observe_attempt([article()], 'fallback'):
                analyse._parse_batch(json.dumps([changed(kind)]), 1, require_rich=True)
    details = stats.snapshot()['format_diagnostics']
    assert details['unique_articles']['contract_rejected'] == 1
    assert details['unique_contract_rejections'] == {'complete_point_count': 1, 'limited_type': 1}


def test_cancelled_request_records_one_terminal_outcome_and_restores_context(monkeypatch):
    async def run():
        entered = asyncio.Event()
        async def blocked(*args):
            entered.set(); await asyncio.Event().wait()
        monkeypatch.setattr(analyse, '_post_messages', blocked)
        async def call():
            with diagnostic.observe_attempt([article()], 'fallback'):
                await analyse._observed_post(None, [article()], '', 1200, 30, 'fallback')
        with collect_analysis_statistics() as stats:
            task = asyncio.create_task(call()); await entered.wait(); task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        assert diagnostic.ATTEMPT.get() is None
        return stats.snapshot()
    report = asyncio.run(run())
    assert report['requests']['cancelled'] == 1
    assert report['format_diagnostics']['counts']['parse_outcomes'] == {'fallback|unknown|cancelled': 1}


def test_diagnostics_do_not_serialize_content_ids_or_untrusted_labels(monkeypatch):
    with collect_analysis_statistics() as stats:
        with diagnostic.observe_attempt([dict(article(), id='PRIVATE_ID')], 'fallback'):
            diagnostic.returned('PRIVATE_RESPONSE', {}, {'text_blocks': 2, 'stop_reason': 'PRIVATE_REASON'})
            obj = changed('four-points'); obj['PRIVATE_KEY'] = 'PRIVATE_VALUE'
            analyse._parse_batch(json.dumps(obj), 1, require_rich=True)
        stats.diagnostics.add('PRIVATE_GROUP', 'PRIVATE_LABEL')
        stats.diagnostics.add('validation', {'PRIVATE_VALUE': 1})
        stats.diagnostics.add('parse_outcomes', 'fallback|unknown|PRIVATE_OUTCOME')
    report = stats.snapshot(); encoded = json.dumps(report)
    assert 'PRIVATE_' not in encoded
    assert report['format_diagnostics']['dropped_metadata_events'] == 3
    assert report['format_diagnostics']['counts']['text_blocks'] == {'2+': 1}
    assert len(json.dumps(report['format_diagnostics']).encode()) <= 32 * 1024
    monkeypatch.setattr(diagnostic, 'MAX_SERIALIZED_BYTES', 128)
    assert stats.diagnostics.snapshot() == {'classification_version': 2, 'truncated': True}


def test_unique_tracking_overflow_is_explicit_and_does_not_limit_articles(monkeypatch):
    monkeypatch.setattr(diagnostic, 'MAX_TRACKED_IDS', 2)
    stats = diagnostic.Diagnostics()
    stats.mark('contract_rejected', ['PRIVATE_A', 'PRIVATE_B', 'PRIVATE_C'])
    report = stats.snapshot()
    assert report['unique_articles']['contract_rejected'] == 2
    assert report['unique_counts_are_lower_bounds'] is True
    assert 'PRIVATE_' not in json.dumps(report)


def test_multiple_provider_text_blocks_are_counted_without_changing_extraction():
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self, **kwargs):
            return {'content': [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': 'second'}]}
    class Session:
        def post(self, *args, **kwargs): return Response()
    metadata = {}
    result = asyncio.run(provider_post_messages(Session(), system='', user_text='',
        max_tokens=1200, timeout=30, metadata=metadata))
    assert result == ('first', {}, 200) and metadata['text_blocks'] == 2
