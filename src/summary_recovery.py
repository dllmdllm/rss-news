"""Offline, input-bound summary repair. No IO, inference or automatic restore."""
import copy
import hashlib
import json
import re

from src.analysis_input import input_hash, source_text
from src.article_evidence import validate_summary_evidence, verified_quote
from src.publisher_identity import identity_key
from src.source_content import text_status
from src.summary_contract import validate_record

SUMMARY_FIELDS = ('summary', 'summary_points', 'summary_format', 'summary_status',
                  'summary_evidence', 'summary_evidence_status', 'analysis_input_hash', 'analysis_version')


def body_valid(article):
    body = article.get('content')
    return (isinstance(body, str) and bool(body) and article.get('content_hash') ==
            hashlib.sha256(body.encode('utf-8')).hexdigest())


def checked_summary(record, article):
    """Check actual reader/input bytes, not just cached hash equality."""
    version = record.get('analysis_version', record.get('version'))
    digest = record.get('analysis_input_hash', record.get('input_hash'))
    if (not body_valid(article) or text_status(article) != 'available' or
            not isinstance(version, str) or not re.fullmatch(r'p-[a-f0-9]{8,64}', version) or
            digest != input_hash(article, version) or record.get('summary_format') != 2):
        return False
    try:
        validate_record(record)
    except (ValueError, TypeError, KeyError):
        return False
    supplied = source_text(article)
    for evidence in record.get('summary_evidence') or []:
        if evidence.get('article_id', article['id']) != article['id']:
            return False
        for quote in evidence.get('quotes', [evidence.get('quote')]):
            if not verified_quote(quote, article) or quote not in supplied:
                return False
    return bool(record.get('summary'))


def complete_metadata(record, article):
    """Correct a conservative partial label only after full revalidation.

    An explicit limited decision is not erased by a five-by-two shape.
    """
    points = record.get('summary_points')
    if (record.get('summary_status') != 'partial' or not isinstance(points, list) or len(points) != 5 or
            any(not isinstance(point, list) or len(point) != 2 for point in points) or
            not checked_summary(record, article)):
        return None
    result = copy.deepcopy(record)
    result['summary_status'] = 'complete'
    try:
        validate_record(result)
    except (ValueError, TypeError, KeyError):
        return None
    return result


def fingerprint(article):
    fields = {key:value for key,value in article.items() if key != 'content'}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def recovery_action(current, historical=None):
    """Return a reviewable action; never change either supplied article."""
    if historical:
        if (current.get('id') != historical.get('id') or identity_key(current) != identity_key(historical) or
                not checked_summary(historical, historical) or
                historical.get('summary_status') != 'complete'):
            return None
        # Full current content and explicit limited decisions are left alone
        # unless the reviewed action is restoring a historical full result.
        if current.get('summary_status') == 'complete' and checked_summary(current, current):
            return None
        if not checked_summary(historical, current):
            return None
        result = historical
        kind = 'historical-full'
    else:
        if current.get('summary_status') != 'partial':
            return None
        result = complete_metadata(current, current)
        if result is None:
            return None
        kind = 'completeness-label'
    replacement = {key:copy.deepcopy(result[key]) for key in SUMMARY_FIELDS if key in result}
    replacement['summary_evidence'] = validate_summary_evidence(dict(current, **replacement), result['summary_evidence'])
    replacement['summary_evidence_status'] = 'available'
    return {'id':current['id'], 'kind':kind, 'expected':fingerprint(current), 'replacement':replacement}


def apply_action(article, cache, action):
    """Apply only to an offline staging copy; reject changed input/manual edits."""
    if (article.get('id') != action.get('id') or fingerprint(article) != action.get('expected') or
            not body_valid(article)):
        raise ValueError('recovery target changed')
    candidate = dict(article, **copy.deepcopy(action['replacement']))
    if candidate.get('summary_status') != 'complete' or not checked_summary(candidate, candidate):
        raise ValueError('recovery evidence invalid')
    article.update(copy.deepcopy(action['replacement']))
    article['analysis_status'] = 'available'
    entry = cache.setdefault(article['id'], {})
    for key in SUMMARY_FIELDS:
        if key in article:
            entry[{'analysis_input_hash':'input_hash', 'analysis_version':'version'}.get(key,key)] = copy.deepcopy(article[key])
    entry.pop('analysis_input_hash', None)
    entry.pop('analysis_version', None)
