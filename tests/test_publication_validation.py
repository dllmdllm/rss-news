"""Generated snapshot validation never calls publishers or paid services."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.validate_publication import validate_site


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Construct internally consistent bodies; individual corruption tests
    # explicitly supply a bad hash rather than accidentally breaking fixtures.
    if path.parent.name == 'content' and isinstance(data, dict) and 'content_hash' not in data and isinstance(data.get('content'), str):
        data = dict(data, content_hash=hashlib.sha256(data['content'].encode()).hexdigest())
        for filename in ('articles.json', 'articles_index.json'):
            target = path.parent.parent / filename
            if target.exists():
                payload = json.loads(target.read_text(encoding='utf-8'))
                for article in payload['articles']:
                    if article['id'] == path.stem:
                        article['content_hash'] = data['content_hash']
                target.write_text(json.dumps(payload), encoding='utf-8')
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def snapshot(tmp_path):
    docs = tmp_path / 'docs'
    article = dict(id='a_1', url='https://example.com/news', summary='• Genuine summary point', score=5, tags=['topic'],
                   sentiment='neutral', date='2026-10-12T12:00:00+08:00',
                   summary_evidence=[dict(point='Genuine summary point', quote='Exact inline quote',
                                          article_id='a_1', verified=True)])
    full = dict(schema_version=2, articles=[article])
    save(docs/'data/articles.json', full)
    save(docs/'data/articles_index.json', full)
    save(docs/'data/content/a_1.json', dict(version=1, content='<p>Exact <strong>inline</strong> quote</p>',
                                          fetched_at=None, content_updated_at=None, quality={'fallback':'rss-html'}))
    return docs


def mutate(docs, filename, callback):
    path = docs/'data'/filename
    data = json.loads(path.read_text(encoding="utf-8"))
    before = copy.deepcopy(data)
    callback(data)
    if filename.startswith('content/') and data.get('content') != before.get('content'):
        data.pop('content_hash', None)
    save(path, data)


def test_valid_current_partial_content_and_unknown_times_are_accepted(snapshot):
    validate_site(snapshot, require_current=True)
    record = json.loads((snapshot/'data/content/a_1.json').read_text(encoding="utf-8"))
    assert record['fetched_at'] is None and record['content_updated_at'] is None


@pytest.mark.parametrize('failure', ['sidecar-hash', 'metadata-hash-missing', 'index-title', 'index-hash'])
def test_publication_and_media_gates_reject_cross_file_corruption(snapshot, failure):
    from tools.check_media_inventory import check
    filename = 'content/a_1.json' if failure == 'sidecar-hash' else ('articles.json' if failure == 'metadata-hash-missing' else 'articles_index.json')
    path = snapshot/'data'/filename
    data = json.loads(path.read_text(encoding='utf-8'))
    if failure == 'sidecar-hash': data['content_hash'] = '0'*64
    elif failure == 'metadata-hash-missing': data['articles'][0].pop('content_hash')
    elif failure == 'index-title': data['articles'][0]['title'] = 'Wrong generation'
    else: data['articles'][0]['content_hash'] = '0'*64
    path.write_text(json.dumps(data), encoding='utf-8')
    for gate in (validate_site, check):
        with pytest.raises(ValueError, match='hash|index title'):
            gate(snapshot)


def test_legacy_absent_evidence_and_schema_are_available_for_review(snapshot):
    for file in ('articles.json', 'articles_index.json'):
        mutate(snapshot, file, lambda data: (data.pop('schema_version'), data['articles'][0].pop('summary_evidence')))
    validate_site(snapshot)
    with pytest.raises(ValueError, match='schema_version 2'):
        validate_site(snapshot, require_current=True)


def test_missing_and_stale_sidecars_fail(snapshot):
    (snapshot/'data/content/a_1.json').unlink()
    with pytest.raises(ValueError, match='missing'):
        validate_site(snapshot)
    save(snapshot/'data/content/a_1.json', dict(version=1, content='<p>Exact inline quote</p>'))
    save(snapshot/'data/content/stale.json', dict(version=1, content='stale'))
    with pytest.raises(ValueError, match='stale'):
        validate_site(snapshot)


@pytest.mark.parametrize('edit,match', [
    (lambda data:data['articles'].clear(), 'IDs differ'),
    (lambda data:data['articles'].append(copy.deepcopy(data['articles'][0])), 'duplicate'),
    (lambda data:data['articles'][0].update(id='../escape'), 'unsafe'),
    (lambda data:data['articles'][0].pop('sentiment'), 'sentiment'),
])
def test_index_contract(snapshot, edit, match):
    mutate(snapshot, 'articles_index.json', edit)
    with pytest.raises(ValueError, match=match):
        validate_site(snapshot)


@pytest.mark.parametrize('content', ['', '   ', '<p></p>', '<script>Exact inline quote</script>', '<template>Exact inline quote</template>'])
def test_empty_or_hidden_body_is_not_real_content(snapshot, content):
    mutate(snapshot, 'content/a_1.json', lambda data:data.update(content=content))
    with pytest.raises(ValueError, match='content'):
        validate_site(snapshot)


def test_malformed_json_fails_descriptively(snapshot):
    (snapshot/'data/content/a_1.json').write_text('{', encoding='utf-8')
    with pytest.raises(ValueError, match='valid JSON'):
        validate_site(snapshot)


@pytest.mark.parametrize('edit,match', [
    (lambda data:data['articles'][0].update(score=True), 'analysis contract'),
    (lambda data:data['articles'][0].update(tags=['okay', 12]), 'tags'),
    (lambda data:data['articles'][0].update(date='2026-10-12'), 'aware ISO'),
    (lambda data:data['articles'][0]['summary_evidence'][0].update(point='Old summary'), 'summary point'),
    (lambda data:data['articles'][0]['summary_evidence'][0].update(quote='Invented evidence'), 'exact current body'),
    (lambda data:data['articles'][0]['summary_evidence'][0].update(article_id='../escape'), 'own article'),
])
def test_bad_analysis_times_or_summary_support(snapshot, edit, match):
    mutate(snapshot, 'articles.json', edit)
    with pytest.raises(ValueError, match=match):
        validate_site(snapshot)


def test_title_and_summary_cannot_supply_body_evidence(snapshot):
    mutate(snapshot, 'articles.json', lambda data:data['articles'][0].update(title='Claim in title', summary_evidence=[dict(point='Genuine summary point', quote='Genuine summary point')]))
    with pytest.raises(ValueError, match='exact current body'):
        validate_site(snapshot)


def test_panel_evidence_is_bound_to_active_body_and_safe_id(snapshot):
    panel = {'c1': {'digest': {'new_developments': [dict(text='Exact inline quote', evidence=[dict(article_id='a_1', quote='Exact inline quote')])]}}}
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    validate_site(snapshot)
    panel['c1']['digest']['new_developments'][0]['evidence'][0]['article_id'] = '../escape'
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='unsafe'):
        validate_site(snapshot)
    panel['c1']['digest']['new_developments'][0]['evidence'][0] = dict(article_id='a_1',quote='Invented quote')
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='exact current body'):
        validate_site(snapshot)


def test_zero_production_snapshot_is_not_published(snapshot):
    for file in ('articles.json','articles_index.json'):
        mutate(snapshot, file, lambda data:data['articles'].clear())
    (snapshot/'data/content/a_1.json').unlink()
    validate_site(snapshot)
    with pytest.raises(ValueError, match='zero articles'):
        validate_site(snapshot, require_current=True)


def test_real_image_only_partial_body_does_not_invent_text(snapshot):
    for file in ('articles.json','articles_index.json'):
        mutate(snapshot, file, lambda data:data['articles'][0].pop('summary_evidence'))
    mutate(snapshot, 'content/a_1.json', lambda data:data.update(content='<img src="https://example.com/image.jpg">'))
    validate_site(snapshot)


def test_panel_shared_fact_requires_two_real_sources(snapshot):
    panel = {'c1': {'digest': {'shared_facts': [dict(text='Exact inline quote', evidence=[dict(article_id='a_1', quote='Exact inline quote')])]}}}
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='two distinct'):
        validate_site(snapshot)
    for file in ('articles.json','articles_index.json'):
        def add_second(data):
            data['articles'][0]['source'] = 'Source A'
            other = copy.deepcopy(data['articles'][0])
            other.update(id='a_2', source='Source B')
            other['summary_evidence'][0]['article_id'] = 'a_2'
            data['articles'].append(other)
        mutate(snapshot, file, add_second)
    save(snapshot/'data/content/a_2.json', dict(version=1, content='<p>Exact inline quote</p>'))
    panel['c1']['digest']['shared_facts'][0]['evidence'].append(dict(article_id='a_2', quote='Exact inline quote', source='Source B'))
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    validate_site(snapshot)
    panel['c1']['digest']['shared_facts'][0]['evidence'][1]['source'] = 'Invented publisher'
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='source differs'):
        validate_site(snapshot)


def test_panel_real_quote_cannot_support_unrelated_statement(snapshot):
    panel = {'c1': {'digest': {'differing_accounts': [dict(text='Invented unrelated statement', evidence=[dict(article_id='a_1', quote='Exact inline quote')])]}}}
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='statement must be verbatim'):
        validate_site(snapshot)


def test_panel_shared_fact_needs_independent_outlets_and_every_quote_supports_statement(snapshot):
    for file in ('articles.json', 'articles_index.json'):
        def add_second(data):
            data['articles'][0]['source'] = 'RTHK 本地'
            second = copy.deepcopy(data['articles'][0])
            second.update(id='a_2', source='RTHK 國際')
            second['summary_evidence'][0]['article_id'] = 'a_2'
            data['articles'].append(second)
        mutate(snapshot, file, add_second)
    save(snapshot/'data/content/a_2.json', dict(version=1, content='<p>Exact inline quote. Other unrelated real quote.</p>'))
    panel = {'c1': {'digest': {'shared_facts': [dict(text='Exact inline quote', evidence=[
        dict(article_id='a_1', quote='Exact inline quote'), dict(article_id='a_2', quote='Exact inline quote')])]}}}
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='two distinct'):
        validate_site(snapshot)
    for file in ('articles.json', 'articles_index.json'):
        mutate(snapshot, file, lambda data:data['articles'][1].update(source='HK01 國際'))
    validate_site(snapshot)
    panel['c1']['digest']['shared_facts'][0]['evidence'][1]['quote'] = 'Other unrelated real quote.'
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='statement must be verbatim'):
        validate_site(snapshot)


def test_generated_minimal_placeholder_cannot_support_summary_or_panel(snapshot):
    mutate(snapshot, 'content/a_1.json', lambda data:data.update(quality={'fallback':'minimal'}))
    with pytest.raises(ValueError, match='minimal placeholder'):
        validate_site(snapshot)
    for file in ('articles.json', 'articles_index.json'):
        mutate(snapshot, file, lambda data:data['articles'][0].pop('summary_evidence'))
    validate_site(snapshot)  # Readable fallback is permitted without false support.
    panel = {'c1': {'digest': {'differing_accounts': [dict(text='Exact inline quote', evidence=[dict(article_id='a_1', quote='Exact inline quote')])]}}}
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='minimal placeholder'):
        validate_site(snapshot)
    mutate(snapshot, 'content/a_1.json', lambda data:data.update(quality={'fallback':'rss-html'}))
    validate_site(snapshot)


def test_only_exact_legacy_star_daily_feed_names_share_outlet():
    from tools.validate_publication import source_outlet
    assert {source_outlet(name) for name in ('星島頭條','星島 即時中國','星島 即時國際')} == {'星島'}
    assert source_outlet('星島其他機構') == '星島其他機構'


def attest_panel(docs, panels):
    """Mirror backend attestation from exact sidecar bytes in positive fixtures."""
    for panel in panels.values():
        for field in ('shared_facts', 'new_developments', 'differing_accounts'):
            for claim in panel['digest'].get(field, []):
                for evidence in claim['evidence']:
                    aid = evidence['article_id']
                    if not isinstance(aid, str) or '/' in aid or '\\' in aid:
                        continue
                    path = docs/'data/content'/f'{aid}.json'
                    if path.exists():
                        evidence['verified'] = True
                        evidence['content_hash'] = hashlib.sha256(json.loads(path.read_text(encoding="utf-8"))['content'].encode('utf-8')).hexdigest()


def test_panel_hash_and_verified_flag_bind_support_to_current_body(snapshot):
    panel = {'c1': {'digest': {'differing_accounts': [dict(text='Exact inline quote', evidence=[dict(article_id='a_1', quote='Exact inline quote')])]}}}
    attest_panel(snapshot, panel)
    save(snapshot/'data/panel_digests.json', panel)
    validate_site(snapshot)
    mutate(snapshot, 'content/a_1.json', lambda data:data.update(content='<p>Exact inline quote</p><p>Updated material.</p>'))
    with pytest.raises(ValueError, match='verified current body hash'):
        validate_site(snapshot)
    attest_panel(snapshot, panel)
    panel['c1']['digest']['differing_accounts'][0]['evidence'][0]['verified'] = False
    save(snapshot/'data/panel_digests.json', panel)
    with pytest.raises(ValueError, match='verified current body hash'):
        validate_site(snapshot)
