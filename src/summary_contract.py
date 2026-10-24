"""Bounded summary structure; no inference, networking or source-text mutation."""
import re

SUMMARY_FORMAT = 2
MAX_POINTS = 5


class ContractError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalise(value):
    return re.sub(r'\s+', ' ', value).strip() if isinstance(value, str) else ''


def sentence(value):
    text = normalise(value)
    if not 4 <= len(text) <= 180 or not re.search(r'[。！？.!?][」』”\"\']*$', text):
        raise ContractError('sentence_incomplete', 'summary needs a complete sentence')
    # Decimals and Latin abbreviations are not Chinese sentence boundaries.
    if re.search(r'[。！？].+[。！？]', text):
        raise ContractError('sentence_boundaries', 'one sentence per structured slot')
    if re.match(r'^[・•●▪◦\-*]', text):
        raise ContractError('sentence_bullet', 'sentence must not contain a bullet marker')
    return text


def normalise_points(raw, limited=False):
    if not isinstance(raw, list):
        raise ContractError('points_type', 'invalid summary point count')
    if len(raw) > MAX_POINTS:
        raise ContractError('points_range', 'invalid summary point count')
    if type(limited) is not bool:
        raise ContractError('limited_type', 'invalid summary point count')
    if not limited and len(raw) != MAX_POINTS:
        raise ContractError('complete_point_count', 'five points required unless source is limited')
    points, support, seen = [], [], set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get('sentences'), list):
            raise ContractError('sentence_structure', 'structured sentences required')
        values = item['sentences']
        quotes = item.get('quotes')
        if len(values) not in ({1, 2} if limited else {2}):
            raise ContractError('sentence_count', 'two sentences required unless source is limited')
        if not isinstance(quotes, list):
            raise ContractError('quote_type', 'every sentence needs source support')
        if len(quotes) != len(values):
            raise ContractError('quote_count', 'every sentence needs source support')
        values = [sentence(value) for value in values]
        quotes = [normalise(quote) for quote in quotes]
        if any(not 8 <= len(quote) <= 500 for quote in quotes):
            raise ContractError('quote_length', 'invalid source quote')
        for value in values:
            identity = re.sub(r'[\W_]+', '', value).casefold()
            if identity in seen:
                raise ContractError('duplicate_sentence', 'repeated summary sentence')
            seen.add(identity)
        point = ''.join(values)
        points.append(values)
        support.append({'point': point, 'quote': quotes[0], 'quotes': quotes})
    return {'summary': '\n'.join('・' + ''.join(point) for point in points),
            'summary_points': points, 'summary_format': SUMMARY_FORMAT,
            'summary_status': 'limited' if limited else 'complete',
            'summary_evidence': support}


def normalise_generated_points(raw, limited=False):
    """Keep valid points without mistaking a formatting miss for source scarcity.

    Publication/cache validation stays strict. Generation may omit or damage a
    point; it must not erase independently usable siblings or invent padding.
    Quotes stay paired inside their original point, never guessed/reassigned.
    """
    if not isinstance(raw, list):
        raise ContractError('points_type', 'invalid summary point count')
    if not raw:
        return normalise_points(raw, limited)
    points, support, seen = [], [], set()
    repaired = type(limited) is not bool or len(raw) != MAX_POINTS
    for item in raw[:MAX_POINTS * 2]:
        if len(points) == MAX_POINTS:
            break
        if (not isinstance(item, dict) or not isinstance(item.get('sentences'), list)
                or not isinstance(item.get('quotes'), list)):
            repaired = True
            continue
        values, quotes = [], []
        repaired |= len(item['sentences']) != 2 or len(item['quotes']) != len(item['sentences'])
        for value, quote in zip(item['sentences'][:2], item['quotes'][:2]):
            try:
                parsed = normalise_points([{'sentences': [value], 'quotes': [quote]}], True)
            except ContractError:
                repaired = True
                continue
            value = parsed['summary_points'][0][0]
            identity = re.sub(r'[\W_]+', '', value).casefold()
            if identity in seen:
                repaired = True
                continue
            seen.add(identity)
            values.append(value)
            quotes.extend(parsed['summary_evidence'][0]['quotes'])
        if not values:
            repaired = True
            continue
        points.append(values)
        support.append({'point': ''.join(values), 'quote': quotes[0], 'quotes': quotes})
        repaired |= len(values) != 2
    if not points:
        raise ContractError('sentence_structure', 'no usable structured summary points')
    return {'summary': '\n'.join('・' + ''.join(point) for point in points),
            'summary_points': points, 'summary_format': SUMMARY_FORMAT,
            'summary_status': ('limited' if limited is True else
                               'partial' if repaired or len(points) != MAX_POINTS else 'complete'),
            'summary_evidence': support}


def validate_record(record):
    """New records must remain structurally honest; legacy records stay readable."""
    if record.get('summary_format') == 1:
        status, summary = record.get('summary_status'), record.get('summary')
        if (status not in {'short', 'limited'} or not isinstance(summary, str)
                or not summary.strip() and status != 'limited' or record.get('summary_points')):
            raise ValueError('invalid short summary status or content')
        return
    if record.get('summary_format') != SUMMARY_FORMAT:
        return
    points = record.get('summary_points')
    status = record.get('summary_status')
    if status not in {'complete', 'limited', 'partial'} or not isinstance(points, list):
        raise ValueError('invalid rich summary status')
    if status == 'partial' and not points:
        raise ValueError('partial summary requires usable content')
    evidence = record.get('summary_evidence')
    if not isinstance(evidence, list) or len(evidence) != len(points):
        raise ValueError('rich summary requires support for every point')
    raw = []
    for point, support in zip(points, evidence):
        if not isinstance(point, list) or not all(isinstance(value, str) for value in point) or not isinstance(support, dict):
            raise ValueError('invalid rich summary point')
        if support.get('point') != ''.join(point):
            raise ValueError('summary support is not bound to its point')
        raw.append({'sentences': point, 'quotes': support.get('quotes')})
        if not isinstance(support.get('quotes'), list) or not support['quotes'] or support.get('quote') != support['quotes'][0]:
            raise ValueError('summary primary quote differs from sentence support')
    canonical = normalise_points(raw, status != 'complete')
    if record.get('summary') != canonical['summary']:
        raise ValueError('summary string differs from structured points')
