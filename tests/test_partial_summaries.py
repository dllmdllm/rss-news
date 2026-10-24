"""Useful supported output survives format misses; no inference or padding."""
import asyncio
import copy
import hashlib
import json

import pytest

from src import analyse
from src.article_evidence import validate_summary_evidence
from src.summary_contract import normalise_generated_points, validate_record
from test_format_diagnostics import complete, article
from test_card_details_browser import fixture_data, route_body, REMOVED
from test_browser_smoke import browser, news_data, _static_server
from test_reader_loop_browser import _route_fixture


def generate(obj):
    return analyse._parse_batch(json.dumps([obj]), 1, require_rich=True)[0]


@pytest.mark.parametrize('count', [1, 2, 3, 4, 5])
def test_supported_points_survive_missing_complete_count(count):
    obj = complete(); obj['summary_points'] = obj['summary_points'][:count]
    parsed = generate(obj); row = article(); cache = {}
    asyncio.run(analyse._apply_results([row], [parsed], cache, asyncio.Lock(), [0]))
    assert row['summary_points'] == [p['sentences'] for p in obj['summary_points']]
    assert row['summary_status'] == ('complete' if count == 5 else 'partial')
    assert row['summary'] and row['analysis_status'] == 'available'
    assert cache[row['id']]['summary'] == row['summary']
    assert not analyse._needs_full_analysis(cache[row['id']], row)
    validate_record(row)


@pytest.mark.parametrize('bad', ['missing-quote', 'invalid-sentence', 'wrong-type', 'duplicate'])
def test_one_malformed_point_does_not_remove_valid_siblings(bad):
    obj = complete()
    if bad == 'missing-quote': obj['summary_points'][0]['quotes'].pop()
    if bad == 'invalid-sentence': obj['summary_points'][0]['sentences'][0] = '無句號片語'
    if bad == 'wrong-type': obj['summary_points'][0] = 'not a point'
    if bad == 'duplicate': obj['summary_points'][0] = copy.deepcopy(obj['summary_points'][1])
    result = generate(obj)
    assert result['summary_status'] == 'partial'
    assert len(result['summary_points']) == 4
    assert len(result['summary_evidence']) == 4
    validate_record(result)


def test_partial_is_not_a_claim_that_full_source_was_unavailable():
    obj = complete(); obj['summary_limited'] = 'true'
    result = generate(obj)
    assert result['summary_status'] == 'partial'
    assert len(result['summary_points']) == 5
    obj['summary_limited'] = True
    assert generate(obj)['summary_status'] == 'limited'


def test_one_sentence_with_its_own_quote_stays_without_fabricated_second_sentence():
    obj = complete(); obj['summary_points'] = obj['summary_points'][:1]
    for key in ('sentences', 'quotes'): obj['summary_points'][0][key].pop()
    result = generate(obj)
    assert result['summary_status'] == 'partial'
    assert len(result['summary_points'][0]) == 1
    assert result['summary_points'][0][0] == obj['summary_points'][0]['sentences'][0]


def test_ungrounded_point_is_removed_without_discarding_supported_siblings():
    obj = complete(); obj['summary_points'][0]['quotes'][1] = '這條引文並不在任何提供的來源正文裏。'
    row = article(); cache = {}; original = row['content']
    asyncio.run(analyse._apply_results([row], [generate(obj)], cache, asyncio.Lock(), [0]))
    assert row['summary_status'] == 'partial' and len(row['summary_points']) == 4
    assert obj['summary_points'][0]['sentences'][0] not in row['summary']
    assert row['content'] == original
    assert len(validate_summary_evidence(row, row['summary_evidence'])) == 4


@pytest.mark.parametrize('existing', [False, True])
def test_all_ungrounded_content_cannot_publish_or_overwrite_valid_result(existing):
    row = article(); cache = {}; counter = [0]
    if existing:
        asyncio.run(analyse._apply_results([row], [generate(complete())], cache, asyncio.Lock(), counter))
    saved_row, saved_cache = copy.deepcopy(row), copy.deepcopy(cache)
    obj = complete()
    for point in obj['summary_points']:
        point['quotes'] = ['完全不在來源裏嘅第一條逐字引文。', '完全不在來源裏嘅第二條逐字引文。']
    asyncio.run(analyse._apply_results([row], [generate(obj)], cache, asyncio.Lock(), counter))
    assert row == saved_row and cache == saved_cache


def test_a_verified_complete_summary_is_not_downgraded_by_partial_refresh():
    row = article(); cache = {}
    asyncio.run(analyse._apply_results([row], [generate(complete())], cache, asyncio.Lock(), [0]))
    saved_row, saved_cache = copy.deepcopy(row), copy.deepcopy(cache)
    obj = complete(); obj['summary_points'].pop()
    asyncio.run(analyse._apply_results([row], [generate(obj)], cache, asyncio.Lock(), [0]))
    assert row == saved_row and cache == saved_cache


def test_input_bound_published_partial_repairs_missing_cache_without_generation(tmp_path, monkeypatch):
    row = article(); obj = complete(); obj['summary_points'].pop()
    asyncio.run(analyse._apply_results([row], [generate(obj)], {}, asyncio.Lock(), [0]))
    expected = row['summary']; cache = tmp_path/'analyses.json'
    monkeypatch.setattr(analyse, 'CACHE_PATH', cache)
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline-mock')
    async def forbidden(*args): pytest.fail('published partial must repair cache before admission')
    monkeypatch.setattr(analyse, '_analyse_batch', forbidden)
    asyncio.run(analyse.analyse_all([row]))
    assert json.loads(cache.read_text(encoding='utf-8'))[row['id']]['summary'] == expected
    assert row['summary_status'] == 'partial' and row['analysis_status'] == 'available'


def test_partial_cache_survives_cancellation_and_reuses_exact_input(tmp_path, monkeypatch):
    row = article(); row['id'] = 'partial-cache'
    row.update(content_hash=hashlib.sha256(row['content'].encode()).hexdigest())
    obj = complete(); obj['summary_points'].pop()
    cache = tmp_path/'analyses.json'
    monkeypatch.setattr(analyse, 'CACHE_PATH', cache)
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline-mock')
    async def cancel_at_deadline():
        entered = asyncio.Event()
        async def interrupted(session, batch, sem, records, lock, counter):
            await analyse._apply_results(batch, [generate(obj)], records, lock, counter)
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(analyse, '_analyse_batch', interrupted)
        task = asyncio.create_task(analyse.analyse_all([row]))
        await entered.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(cancel_at_deadline())
    saved = json.loads(cache.read_text(encoding='utf-8'))
    assert saved[row['id']]['summary_status'] == 'partial'
    async def forbidden(*args): pytest.fail('an unchanged partial result must not cause paid padding')
    monkeypatch.setattr(analyse, '_analyse_batch', forbidden)
    expected = row['summary']
    asyncio.run(analyse.analyse_all([row]))
    assert row['summary'] == expected and row['analysis_status'] == 'available'
    assert json.loads(cache.read_text(encoding='utf-8'))[row['id']]['summary'] == expected


def test_partial_publication_reconciles_index_body_and_source_quotes(tmp_path):
    from src.index_projection import project_article
    from tools.validate_publication import validate_site
    row = article(); obj = complete(); obj['summary_points'].pop()
    asyncio.run(analyse._apply_results([row], [generate(obj)], {}, asyncio.Lock(), [0]))
    row['content_hash'] = hashlib.sha256(row['content'].encode()).hexdigest()
    docs = tmp_path/'docs'; data = docs/'data'; (data/'content').mkdir(parents=True)
    def write():
        (data/'articles.json').write_text(json.dumps(dict(schema_version=2, articles=[row])), encoding='utf-8')
        (data/'articles_index.json').write_text(json.dumps(dict(schema_version=2, articles=[project_article(row)])), encoding='utf-8')
        (data/'content'/f"{row['id']}.json").write_text(json.dumps(dict(version=1, content=row['content'], content_hash=row['content_hash'], quality=row['content_quality'])), encoding='utf-8')
    write(); validate_site(docs, require_current=True)
    row['summary_evidence'][0]['quotes'][1] = '完全不在來源裏嘅第二條逐字引文。'
    write()
    with pytest.raises(ValueError, match='quote'): validate_site(docs, require_current=True)


@pytest.mark.parametrize('width', [390, 1280])
def test_partial_visible_in_cards_reader_search_without_claiming_source_limited(browser, news_data, width):
    data, sentences, body = fixture_data(news_data)
    row = data['articles'][0]
    row['summary_points'] = row['summary_points'][:3]
    row['summary_evidence'] = row['summary_evidence'][:3]
    row['summary'] = '\n'.join('・'+''.join(p) for p in row['summary_points'])
    row['summary_status'] = 'partial'
    raw = json.dumps({'a0': {'saved': True, 'read': False, 'position': .43, 'updated': 1}})
    with _static_server() as base:
        context = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        _route_fixture(context, base, {'payload': data})
        context.add_init_script('localStorage.setItem("rss_reader_state_v1",'+json.dumps(raw)+')')
        page = context.new_page(); page.set_default_timeout(6000)
        route_body(page, data, body)
        try:
            page.goto(base+'/index.html'); page.locator('#leadStory h1').wait_for()
            lead = page.locator('#leadStory')
            assert lead.locator('.summary-point').all_inner_texts() == [''.join(p) for p in sentences[:3]]
            assert '摘要未完整' in lead.locator('.summary-limits').inner_text()
            assert '來源資料不足' not in lead.inner_text() and '稍後補上' not in lead.inner_text()
            if width == 390: page.locator('#mobileTabs [data-view="search"]').click()
            page.locator('#search').fill(row['title'])
            assert page.locator(REMOVED).count() == 0
            page.goto(base+'/article.html?id=a0')
            page.wait_for_function('document.querySelector("#summaryBox").dataset.bodyCheck === "matched"')
            assert page.locator('#summaryBox li').all_inner_texts() == [''.join(p) for p in sentences[:3]]
            assert '摘要未完整' in page.locator('#summaryBox .summary-limits').inner_text()
            assert '稍後補上' not in page.locator('#summaryBox').inner_text()
            state = page.evaluate('RssReaderState.get("a0")')
            assert state['saved'] and state['position'] == .43
            assert page.locator(REMOVED).count() == 0
        finally: context.close()
