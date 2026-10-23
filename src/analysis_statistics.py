"""Bounded aggregate diagnostics: never store titles, bodies, URLs or model text."""
from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from src.analysis_diagnostics import DIAGNOSTICS, Diagnostics

CURRENT = ContextVar('rss_analysis_statistics', default=None)
RESPONSE_METADATA = ContextVar('rss_analysis_response_metadata', default=None)


class Statistics:
    def __init__(self):
        self.counts = defaultdict(Counter)
        self.counts['requests'].update({key: 0 for key in
            ('attempted', 'returned', 'timed_out', 'cancelled', 'transport_error')})
        self.counts['results'].update({key: 0 for key in
            ('applied', 'with_summary', 'limited_empty', 'rejected_slots',
             'whole_batch_rejections', 'unsupported_points_removed', 'batch_crashed')})
        self.started_ids = set()
        self.applied_ids = set()
        self.admitted_ids = set()
        self.active = 0
        self.peak_active = 0
        self.request_seconds = 0.0
        self.diagnostics = Diagnostics()

    def count(self, group, reason, amount=1):
        self.counts[group][reason] += amount

    def started(self, articles, kind):
        self.started_ids.update(a['id'] for a in articles)
        self.count('requests', 'attempted')
        self.count('request_kinds', kind)
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)

    def finished(self, metadata, seconds):
        self.active -= 1
        self.request_seconds += seconds
        status = metadata.get('http_status')
        if type(status) is int and 100 <= status <= 599:
            self.count('http_status', str(status))
        reason = metadata.get('stop_reason')
        if reason is not None:
            self.count('stop_reason', reason)
        for key in ('input_tokens', 'output_tokens'):
            value = metadata.get(key)
            if type(value) is int and 0 <= value <= 1_000_000_000:
                self.count('usage', key, value)
                self.count('usage', key + '_responses')

    def snapshot(self):
        return {'version': 1, **{key: dict(value) for key, value in self.counts.items()},
                'queue': {'admitted': len(self.admitted_ids), 'started': len(self.started_ids),
                          'not_started': len(self.admitted_ids - self.started_ids),
                          'completed': len(self.applied_ids),
                          'started_incomplete': len(self.started_ids - self.applied_ids)},
                'peak_active_requests': self.peak_active,
                'request_seconds_total': round(self.request_seconds, 3),
                'format_diagnostics': self.diagnostics.snapshot()}


def count(group, reason, amount=1):
    stats = CURRENT.get()
    if stats is not None:
        stats.count(group, reason, amount)


@contextmanager
def collect_analysis_statistics():
    stats = Statistics()
    token = CURRENT.set(stats)
    diagnostic_token = DIAGNOSTICS.set(stats.diagnostics)
    try:
        yield stats
    finally:
        DIAGNOSTICS.reset(diagnostic_token)
        CURRENT.reset(token)
