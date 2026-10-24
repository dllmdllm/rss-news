"""Offline richer summaries: no padding, unsupported facts or migration data loss."""
import asyncio
import copy
import json

import pytest

from src import analyse
from src.article_evidence import validate_summary_evidence
from src.summary_contract import normalise_points, validate_record


SENTENCES = [
    ['政府今日公布新的交通安排。', '新安排將於明日正式開始實施。'],
    ['巴士公司將增加繁忙時段班次。', '新增班次會先服務主要幹線。'],
    ['相關學校表示會維持正常上課。', '校方將通知家長留意交通變動。'],
    ['道路工程會於周末晚上進行。', '警方會在現場實施臨時改道。'],
    ['市民可以在官方網站查閱路線。', '部門將按實際情況更新指引。'],
]


def raw_points():
    return [dict(sentences=point[:], quotes=point[:]) for point in SENTENCES]


def article():
    return dict(id='a', title='交通安排', date='2026-10-23T00:00:00Z', source='來源',
                url='https://example.org/a', content_quality={'fallback': 'none'},
                content='<p>' + ''.join(sum(SENTENCES, [])) + '</p>')


def parsed(points=None, limited=False):
    return analyse._parse_analysis(json.dumps(dict(summary_points=raw_points() if points is None else points,
        summary_limited=limited, score=7, tags=[], sentiment='neutral', topic='交通')), require_rich=True)


def test_five_points_keep_two_full_sentences_through_parser_and_application():
    result = parsed()
    current = article(); cache = {}
    asyncio.run(analyse._apply_results([current], [result], cache, asyncio.Lock(), [0]))
    assert current['summary_points'] == SENTENCES
    assert current['summary_status'] == 'complete'
    assert len(current['summary'].splitlines()) == 5
    assert all(len(e['quotes']) == 2 for e in current['summary_evidence'])
    validate_record(current)
    assert not analyse._needs_full_analysis(cache['a'], current)
    assert current['content'] == article()['content']


@pytest.mark.parametrize('change', ['missing', 'fragment', 'duplicate', 'three', 'quote-count', 'over-five'])
def test_format_misses_keep_valid_points_without_padding(change):
    points = raw_points()
    if change == 'missing': points.pop()
    if change == 'fragment': points[0]['sentences'][0] = '政府新交通安排'
    if change == 'duplicate': points[1]['sentences'][0] = points[0]['sentences'][0]
    if change == 'three': points[0]['sentences'].append('不得額外加入第三句話。')
    if change == 'quote-count': points[0]['quotes'].pop()
    if change == 'over-five': points.append(copy.deepcopy(points[0]))
    result = parsed(points)
    assert result['summary_status'] == 'partial'
    assert len(result['summary_points']) == (5 if change == 'over-five' else 4)
    validate_record(result)


def test_generation_rejects_legacy_fragments_but_legacy_parser_remains_readable():
    raw = json.dumps({'summary': '・短摘要', 'score': 5})
    assert analyse._parse_analysis(raw) is not None
    assert analyse._parse_analysis(raw, require_rich=True) is None


def test_short_complete_sentence_is_not_padded_to_an_arbitrary_length():
    point = {'sentences': ['無人受傷。', '警方已經封閉現場調查。'],
             'quotes': ['事件中現場無人受傷。', '警方已經封閉現場調查。']}
    result = normalise_points([point], True)
    assert result['summary_points'][0][0] == '無人受傷。'


def test_rich_snapshot_validates_both_sentence_quotes_and_projection(tmp_path):
    import hashlib
    from src.index_projection import project_article
    from tools.validate_publication import validate_site
    current = article(); cache = {}
    asyncio.run(analyse._apply_results([current], [parsed()], cache, asyncio.Lock(), [0]))
    current['content_hash'] = hashlib.sha256(current['content'].encode()).hexdigest()
    docs = tmp_path/'docs'; data = docs/'data'; (data/'content').mkdir(parents=True)
    def save():
        payload = dict(schema_version=2, articles=[current])
        (data/'articles.json').write_text(json.dumps(payload), encoding='utf-8')
        (data/'articles_index.json').write_text(json.dumps({**payload, 'articles': [project_article(current)]}), encoding='utf-8')
        (data/'content/a.json').write_text(json.dumps(dict(version=1, content=current['content'], content_hash=current['content_hash'], quality=current['content_quality'])), encoding='utf-8')
    save(); validate_site(docs, require_current=True)
    current['summary_evidence'][0]['quotes'][1] = '這段逐字引文在來源裏並不存在。'
    save()
    with pytest.raises(ValueError, match='quote'):
        validate_site(docs, require_current=True)


@pytest.mark.parametrize('points', [[], [{'sentences': SENTENCES[0][:1], 'quotes': SENTENCES[0][:1]}]])
def test_source_limit_never_pads_or_retries_to_meet_counts(points):
    result = parsed(points, True)
    assert result is not None and result['summary_status'] == 'limited'
    current = article(); cache = {}
    asyncio.run(analyse._apply_results([current], [result], cache, asyncio.Lock(), [0]))
    assert len(current['summary_points']) == len(points)
    assert not analyse._needs_full_analysis(cache['a'], current)


def test_missing_second_sentence_support_discards_whole_point():
    points = raw_points(); points[0]['quotes'][1] = '這句原文根本不存在，不能充當支持。'
    result = parsed(points); current = article()
    asyncio.run(analyse._apply_results([current], [result], {}, asyncio.Lock(), [0]))
    assert len(current['summary_points']) == 4
    assert current['summary_status'] == 'partial'
    assert SENTENCES[0][0] not in current['summary']


def test_source_quote_outside_supplied_prefix_is_not_generation_support():
    current = article(); current['content'] = '<p>' + '無關的已提供文字。' * 350 + ''.join(sum(SENTENCES, [])) + '</p>'
    asyncio.run(analyse._apply_results([current], [parsed()], {}, asyncio.Lock(), [0]))
    assert not current.get('summary') and not current.get('summary_points')
    assert current.get('analysis_status') != 'available'


def test_second_quote_is_validated_internally():
    current = article(); point = ''.join(SENTENCES[0]); current['summary'] = '・' + point
    assert not validate_summary_evidence(current, [{'point': point, 'quote': SENTENCES[0][0],
        'quotes': [SENTENCES[0][0], '這句原文根本不存在，不能充當支持。']}])


@pytest.mark.parametrize('key_enabled', [False, True])
def test_prompt_migration_preserves_unchanged_old_summary_and_cache(tmp_path, monkeypatch, key_enabled):
    current = article(); old_version = 'p-0123456789ab'
    old = dict(summary='・原有摘要內容', score=7, tags=[], version=old_version,
               input_hash=analyse.analysis_input_hash(current, version=old_version))
    path = tmp_path/'analyses.json'; path.write_text(json.dumps({'a': old}), encoding='utf-8')
    monkeypatch.setattr(analyse, 'CACHE_PATH', path)
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', 'offline-mock' if key_enabled else '')
    monkeypatch.setattr(analyse, 'legacy_source_limit', lambda source: 0)
    current.update(summary=old['summary'], analysis_version=old_version, analysis_input_hash=old['input_hash'])
    asyncio.run(analyse.analyse_all([current]))
    assert current['summary'] == old['summary'] and current['summary_status'] == 'legacy'
    assert json.loads(path.read_text(encoding='utf-8'))['a']['version'] == old_version
    assert current['content'] == article()['content']


def test_corrected_input_cannot_reuse_old_prompt_summary(tmp_path, monkeypatch):
    current = article(); old_version = 'p-0123456789ab'
    current.update(summary='・舊資料摘要', analysis_version=old_version,
        analysis_input_hash=analyse.analysis_input_hash(dict(current, title='舊標題'), version=old_version))
    monkeypatch.setattr(analyse, 'MINIMAX_API_KEY', '')
    asyncio.run(analyse.analyse_all([current]))
    assert current['summary'] == '' and 'summary_points' not in current


def test_published_structure_rejects_string_and_support_divergence():
    result = normalise_points(raw_points())
    validate_record(result)
    result['summary'] += '沒有根據的額外一句。'
    with pytest.raises(ValueError): validate_record(result)


def test_last_updated_never_uses_fetch_or_publication_clock():
    import subprocess
    from pathlib import Path
    from test_frontend import _extract_js_function
    root = Path(__file__).resolve().parents[1]
    fn = _extract_js_function((root/'docs/js/common.js').read_text(encoding='utf-8'), 'timeLabel')
    script = fn + '''
      const assert=require('node:assert/strict');
      Date.now=()=>Date.parse('2026-10-23T03:00:00Z');
      assert.equal(timeLabel({date:'2026-10-23T03:00:00Z',fetched_at:'2026-10-23T03:00:00Z'}),'最後更新：未提供');
      assert.equal(timeLabel({content_updated_at:'2026-10-23T00:00:00Z',date:'2026-10-23T03:00:00Z',fetched_at:'2026-10-23T03:00:00Z'}),'最後更新：3 小時前');
      assert.equal(timeLabel({content_updated_at:'2026-10-23T00:00:00'}),'最後更新：未提供');
      assert.equal(timeLabel({content_updated_at:'bad'}),'最後更新：未提供');
    '''
    subprocess.run(['node', '-e', script], check=True, cwd=root)
