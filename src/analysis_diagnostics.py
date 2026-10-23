"""Fixed-cardinality output diagnostics; never retain response or article text."""
import json
from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar

DIAGNOSTICS = ContextVar('rss_format_diagnostics', default=None)
ATTEMPT = ContextVar('rss_format_attempt', default=None)
MAX_SERIALIZED_BYTES = 32 * 1024
MAX_TRACKED_IDS = 8192
TYPES = {'missing', 'null', 'object', 'array', 'string', 'boolean', 'number', 'other'}
COUNTS = {'0', '1', '2', '3', '4', '5', '6+'}
STOPS = {'unknown', 'end_turn', 'max_tokens', 'stop_sequence', 'tool_use', 'pause_turn', 'refusal', 'other'}
KINDS = {'batch', 'fallback', 'other'}
OUTCOMES = {'accepted', 'partial', 'contract_rejected', 'binding_rejected',
            'json_rejected', 'envelope_rejected', 'not_parsed', 'empty', 'http_error',
            'request_timeout', 'cancelled', 'transport_error'}
VALIDATION = {'object_type', 'missing_rich_points', 'points_type', 'points_range',
              'limited_type', 'complete_point_count', 'sentence_structure',
              'sentence_count', 'quote_type', 'quote_count', 'quote_length',
              'sentence_incomplete', 'sentence_boundaries', 'sentence_bullet',
              'duplicate_sentence', 'schema_echo', 'summary_shape', 'malformed_object'}
ENVELOPES = {'missing_document', 'json_decode', 'extra_document', 'root_type',
             'batch_length', 'response_excess'}
BINDINGS = {'object_type', 'missing_index', 'invalid_index', 'duplicate_index'}
LENGTHS = {'0', '1-3', '4-80', '81-180', '181-500', '501-2000', '2001+'}
ALLOWED = {**{key: TYPES for key in ('root_type', 'point_type', 'sentence_type', 'quote_type', 'index_type', 'sentence_value_type', 'quote_value_type')},
           **{key: COUNTS for key in ('expected_count', 'root_count', 'point_count', 'sentence_count', 'quote_count')},
           **{key: LENGTHS for key in ('sentence_length', 'quote_length', 'response_length')},
           'limited': {'absent', 'true', 'false', 'wrong-type'},
           'text_blocks': {'unknown', '0', '1', '2+'},
           'validation': VALIDATION, 'envelope': ENVELOPES, 'binding': BINDINGS}
UNIQUE = {'contract_rejected', 'envelope_affected', 'binding_affected', 'grounding_removed'}
_MISSING = object()


def value_type(value):
    if value is _MISSING: return 'missing'
    if value is None: return 'null'
    return {dict: 'object', list: 'array', str: 'string', bool: 'boolean',
            int: 'number', float: 'number'}.get(type(value), 'other')


def count_bucket(number):
    return str(number) if 0 <= number <= 5 else '6+'


def length_bucket(value):
    n = len(value) if isinstance(value, str) else 0
    for limit, label in ((0, '0'), (3, '1-3'), (80, '4-80'), (180, '81-180'),
                         (500, '181-500'), (2000, '501-2000')):
        if n <= limit: return label
    return '2001+'


class Diagnostics:
    def __init__(self):
        self.counts = defaultdict(Counter)
        self.unique = {key: set() for key in UNIQUE}
        self.unique_validation = {key: set() for key in VALIDATION}
        self.tracked_ids = set()
        self.unique_truncated = False
        self.dropped = 0

    def add(self, group, label):
        if not isinstance(group, str) or not isinstance(label, str):
            self.dropped += 1
            return
        valid = label in ALLOWED.get(group, set())
        if group == 'parse_outcomes':
            parts = label.split('|')
            valid = len(parts) == 3 and parts[0] in KINDS and parts[1] in STOPS and parts[2] in OUTCOMES
        if valid:
            self.counts[group][label] += 1
        else:
            self.dropped += 1

    def mark(self, group, ids):
        if group not in UNIQUE:
            self.dropped += 1
            return
        for aid in ids:
            if aid not in self.tracked_ids:
                if len(self.tracked_ids) >= MAX_TRACKED_IDS:
                    self.unique_truncated = True
                    continue
                self.tracked_ids.add(aid)
            self.unique[group].add(aid)

    def snapshot(self):
        result = {'classification_version': 2,
                  'counts': {key: dict(value) for key, value in self.counts.items()},
                  'unique_articles': {key: len(value) for key, value in self.unique.items()},
                  'unique_contract_rejections': {key: len(value) for key, value in sorted(self.unique_validation.items()) if value},
                  'unique_counts_are_lower_bounds': self.unique_truncated,
                  'dropped_metadata_events': self.dropped, 'truncated': False}
        if len(json.dumps(result).encode('utf-8')) > MAX_SERIALIZED_BYTES:
            return {'classification_version': 2, 'truncated': True}
        return result


class Observation:
    def __init__(self, diagnostics, articles, kind):
        self.diagnostics = diagnostics
        self.ids = tuple(a['id'] for a in articles)
        self.kind = kind if kind in KINDS else 'other'
        self.stop = 'unknown'
        self.outcome = 'not_parsed'
        self.bound_index = None

    def close(self):
        if self.diagnostics:
            self.diagnostics.add('parse_outcomes', '|'.join((self.kind, self.stop, self.outcome)))


@contextmanager
def observe_attempt(articles, kind):
    observation = Observation(DIAGNOSTICS.get(), articles, kind)
    token = ATTEMPT.set(observation)
    try:
        yield observation
    finally:
        observation.close()
        ATTEMPT.reset(token)


def request_outcome(outcome):
    observation = ATTEMPT.get()
    if observation is not None:
        observation.outcome = outcome


def returned(raw, err, metadata):
    observation = ATTEMPT.get()
    diagnostics = DIAGNOSTICS.get()
    if diagnostics is None: return
    diagnostics.add('response_length', length_bucket(raw))
    blocks = metadata.get('text_blocks')
    diagnostics.add('text_blocks', 'unknown' if type(blocks) is not int or blocks < 0 else
                    str(blocks) if blocks <= 1 else '2+')
    if observation is not None:
        reason = metadata.get('stop_reason')
        observation.stop = reason if isinstance(reason, str) and reason in STOPS else 'unknown'
        observation.outcome = 'http_error' if err else 'empty' if not raw else 'not_parsed'


def root_shape(value, expected):
    diagnostics = DIAGNOSTICS.get()
    if diagnostics is None: return
    diagnostics.add('root_type', value_type(value))
    diagnostics.add('expected_count', count_bucket(expected))
    if isinstance(value, list): diagnostics.add('root_count', count_bucket(len(value)))


def object_shape(obj):
    diagnostics = DIAGNOSTICS.get()
    if diagnostics is None: return
    diagnostics.add('index_type', value_type(obj.get('idx', _MISSING)))
    points = obj.get('summary_points', _MISSING)
    diagnostics.add('point_type', value_type(points))
    limited = obj.get('summary_limited', _MISSING)
    diagnostics.add('limited', 'absent' if limited is _MISSING else
                    'true' if limited is True else 'false' if limited is False else 'wrong-type')
    if not isinstance(points, list): return
    diagnostics.add('point_count', count_bucket(len(points)))
    for point in points[:5]:
        if not isinstance(point, dict): continue
        for field, prefix in (('sentences', 'sentence'), ('quotes', 'quote')):
            values = point.get(field, _MISSING)
            diagnostics.add(prefix + '_type', value_type(values))
            if isinstance(values, list):
                diagnostics.add(prefix + '_count', count_bucket(len(values)))
                for value in values[:2]:
                    diagnostics.add(prefix + '_value_type', value_type(value))
                    diagnostics.add(prefix + '_length', length_bucket(value))


def contract_rejection(reason):
    diagnostics, observation = DIAGNOSTICS.get(), ATTEMPT.get()
    if diagnostics is None: return
    diagnostics.add('validation', reason)
    if observation is not None and observation.bound_index is not None:
        idx = observation.bound_index
        if 1 <= idx <= len(observation.ids):
            aid = observation.ids[idx - 1]
            diagnostics.mark('contract_rejected', [aid])
            if isinstance(reason, str) and reason in VALIDATION and aid in diagnostics.tracked_ids:
                diagnostics.unique_validation[reason].add(aid)


def envelope_rejection(reason):
    diagnostics, observation = DIAGNOSTICS.get(), ATTEMPT.get()
    if diagnostics is not None:
        diagnostics.add('envelope', reason)
        if observation is not None: diagnostics.mark('envelope_affected', observation.ids)
    request_outcome('json_rejected' if reason in {'missing_document', 'json_decode', 'extra_document', 'response_excess'} else 'envelope_rejected')


def binding_rejection(reason):
    diagnostics, observation = DIAGNOSTICS.get(), ATTEMPT.get()
    if diagnostics is not None:
        diagnostics.add('binding', reason)
        if observation is not None: diagnostics.mark('binding_affected', observation.ids)


def support_removed(aid):
    diagnostics = DIAGNOSTICS.get()
    if diagnostics is not None: diagnostics.mark('grounding_removed', [aid])
