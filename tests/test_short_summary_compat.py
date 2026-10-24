"""Local-only compact generation and retained rich-cache compatibility."""
import asyncio
import copy
import hashlib
import json

import pytest

from src import analyse
from src.summary_contract import validate_record
from test_analysis_recovery import article, offline
from test_summary_contract import article as rich_article, parsed, raw_points


def short_reply(**changes):
    return dict(summary='・車站提早關門\n・旅客改乘巴士', score=6, **changes)


def test_short_style_does_not_require_five_points_or_two_sentences():
    result = analyse._parse_analysis(json.dumps(short_reply()))
    assert result['summary_format'] == 1 and result['summary_status'] == 'short'
    assert result['summary'].splitlines() == ['・車站提早關門', '・旅客改乘巴士']
    assert 'summary_points' not in result
    validate_record(result)
    assert '5至8個重點' in analyse.SYSTEM_PROMPT and '唔需要每點兩句' in analyse.SYSTEM_PROMPT


def test_empty_short_result_requires_an_explicit_source_limit():
    assert analyse._parse_analysis(json.dumps({'summary': '', 'score': 5})) is None
    result = analyse._parse_analysis(json.dumps({'summary': '', 'score': 5, 'summary_limited': True}))
    assert result['summary_status'] == 'limited'
    validate_record(result)


def test_normal_batch_accepts_short_reply_and_second_cycle_uses_cache(tmp_path, monkeypatch):
    path = offline(monkeypatch, tmp_path)
    calls = []
    async def respond(*args):
        calls.append(1)
        return json.dumps([dict(idx=1, **short_reply())]), {}, 200
    monkeypatch.setattr(analyse, '_post_messages', respond)
    row = article('short')
    asyncio.run(analyse.analyse_all([row]))
    assert row['summary_status'] == 'short' and calls == [1]
    saved = json.loads(path.read_text(encoding='utf-8'))['short']
    asyncio.run(analyse.analyse_all([article('short')]))
    assert calls == [1] and json.loads(path.read_text(encoding='utf-8'))['short'] == saved


@pytest.mark.parametrize('style', ['short', 'legacy', 'complete', 'partial', 'limited-empty'])
def test_unchanged_prior_version_cache_is_retained_without_format_regeneration(tmp_path, monkeypatch, style):
    path = offline(monkeypatch, tmp_path)
    row = rich_article()
    if style in {'short', 'legacy'}:
        entry = analyse._parse_analysis(json.dumps(short_reply()))
        if style == 'legacy':
            entry.pop('summary_format'); entry.pop('summary_status')
    else:
        points = [] if style == 'limited-empty' else raw_points()[:3] if style == 'partial' else raw_points()
        entry = parsed(points, style == 'limited-empty')
        row['summary'] = entry['summary']
        entry['summary_evidence'] = analyse.validate_summary_evidence(row, entry['summary_evidence'])
    entry.update(version='p-0123456789ab', input_hash=analyse.analysis_input_hash(row, version='p-0123456789ab'))
    path.write_text(json.dumps({'a': entry}), encoding='utf-8')
    async def forbidden(*args): pytest.fail('valid prior-version input must not be regenerated')
    monkeypatch.setattr(analyse, '_post_messages', forbidden)
    asyncio.run(analyse.analyse_all([row]))
    assert row['summary'] == entry['summary'] and row['analysis_version'] == entry['version']
    assert row['analysis_input_hash'] == entry['input_hash'] and row['analysis_status'] == 'available'
    saved = json.loads(path.read_text(encoding='utf-8'))['a']
    assert saved['summary'] == entry['summary'] and saved['version'] == entry['version']
    assert saved['input_hash'] == entry['input_hash']


@pytest.mark.parametrize('field,value', [('title', '更正關門時間'), ('content', '<p>車站改為照常開放。</p>'),
    ('date', '2026-10-24T09:00:00Z'), ('source', '另一來源'), ('url', 'https://example.org/other')])
def test_material_input_change_does_not_reuse_the_old_short_summary(tmp_path, monkeypatch, field, value):
    path = offline(monkeypatch, tmp_path)
    row = article('changed')
    entry = dict(analyse._parse_analysis(json.dumps(short_reply())),
        input_hash=analyse.analysis_input_hash(row))
    path.write_text(json.dumps({'changed': entry}), encoding='utf-8')
    row.update({field: value})
    assert analyse._needs_full_analysis(entry, row)
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 0)
    asyncio.run(analyse.analyse_all([row]))
    assert not row['summary'] and row['analysis_status'] == 'pending-budget'


@pytest.mark.parametrize('content,quality', [('<img src="https://example.org/a.jpg">', {}),
    ('<p>暫時未能擷取全文。</p>', {'fallback': 'minimal'}), ('', {})])
def test_unavailable_source_stays_honest_without_generation(tmp_path, monkeypatch, content, quality):
    offline(monkeypatch, tmp_path)
    row = article('missing'); row.update(content=content, content_quality=quality)
    async def forbidden(*args): pytest.fail('unavailable source must not reach the provider')
    monkeypatch.setattr(analyse, '_post_messages', forbidden)
    asyncio.run(analyse.analyse_all([row]))
    assert row['summary'] == '' and row['analysis_status'] == 'pending-source'


def test_unverified_optional_short_quotes_are_not_labelled_as_verified():
    row = article('quoted')
    entry = analyse._parse_analysis(json.dumps(short_reply(summary_evidence=[
        {'point': '車站提早關門', 'quote': '這句話並不存在於來源正文之中。'}])))
    asyncio.run(analyse._apply_results([row], [entry], {}, asyncio.Lock(), [0]))
    analyse._ensure_analysis_defaults([row])
    assert row['summary'] and row['summary_evidence'] == []
    assert row['summary_evidence_status'] == 'unavailable'


def test_new_short_reply_cannot_downgrade_an_existing_valid_long_summary():
    row = rich_article(); cache = {}
    asyncio.run(analyse._apply_results([row], [parsed()], cache, asyncio.Lock(), [0]))
    before = copy.deepcopy(row)
    short = analyse._parse_analysis(json.dumps(short_reply()))
    asyncio.run(analyse._apply_results([row], [short], cache, asyncio.Lock(), [0]))
    assert row == before


def test_published_valid_long_summary_wins_over_a_matching_short_cache(tmp_path, monkeypatch):
    path = offline(monkeypatch, tmp_path)
    row = rich_article()
    asyncio.run(analyse._apply_results([row], [parsed()], {}, asyncio.Lock(), [0]))
    before = row['summary']
    short = analyse._parse_analysis(json.dumps(short_reply()))
    short.update(input_hash=analyse.analysis_input_hash(row))
    path.write_text(json.dumps({'a': short}), encoding='utf-8')
    async def forbidden(*args): pytest.fail('published valid long summary must not be generated again')
    monkeypatch.setattr(analyse, '_post_messages', forbidden)
    asyncio.run(analyse.analyse_all([row]))
    assert row['summary'] == before and row['summary_status'] == 'complete'
    assert json.loads(path.read_text(encoding='utf-8'))['a']['summary'] == before


def test_new_short_snapshot_requires_the_actual_body_input_hash(tmp_path):
    from src.index_projection import project_article
    from tools.validate_publication import validate_site
    row = article('a'); cache = {}
    asyncio.run(analyse._apply_results([row], [analyse._parse_analysis(json.dumps(short_reply()))], cache, asyncio.Lock(), [0]))
    row['content_hash'] = hashlib.sha256(row['content'].encode()).hexdigest()
    data = tmp_path/'docs/data'; (data/'content').mkdir(parents=True)
    def save():
        payload = dict(schema_version=2, articles=[row])
        (data/'articles.json').write_text(json.dumps(payload), encoding='utf-8')
        (data/'articles_index.json').write_text(json.dumps({**payload, 'articles':[project_article(row)]}), encoding='utf-8')
        (data/'content/a.json').write_text(json.dumps(dict(version=1, content=row['content'],
            content_hash=row['content_hash'], quality={})), encoding='utf-8')
    save(); validate_site(tmp_path/'docs', require_current=True)
    row['analysis_input_hash'] = '0'*64
    save()
    with pytest.raises(ValueError, match='analysis input hash'):
        validate_site(tmp_path/'docs', require_current=True)
