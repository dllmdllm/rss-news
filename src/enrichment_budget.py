"""Keep paid enrichment admission at the previous per-source article budget.

Collection limits no longer decide which source records exist. Cache hits are
free to reuse; callers count only admitted cache misses against these unchanged
legacy caps. This module makes no network calls and changes no provider quota.
"""
from src.feeds import MAX_ITEMS_PER_FEED, RSS_FEEDS


def legacy_source_limit(source):
    limits = [int(feed.get('max_items', MAX_ITEMS_PER_FEED))
              for feed in RSS_FEEDS if feed.get('name') == source]
    return max(1, min(limits)) if limits else MAX_ITEMS_PER_FEED
