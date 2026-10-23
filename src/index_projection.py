"""Shared writer/validator contract for the homepage metadata projection."""
INDEX_FIELDS = (
    'id', 'title', 'source', 'date', 'category', 'thumbnail', 'summary', 'score',
    'tags', 'sentiment', 'topic', 'cluster_size', 'cluster_id', 'event_type',
    'duplicate_of', 'duplicate_count', 'headline_fit', 'published_at', 'fetched_at',
    'content_updated_at', 'content_hash', 'evidence_status', 'content_quality',
    'scrape_error', 'content_retention', 'summary_evidence_status', 'summary_evidence', 'publisher_id',
    'publisher_article_id', 'canonical_url', 'url_aliases', 'id_aliases', 'memberships', 'source_modified_at',
    'translation_status', 'translation_reason', 'title_translation_status', 'analysis_status',
    'body_status',
    'summary_points', 'summary_format', 'summary_status',
)


def project_article(article):
    return {key: article.get(key, 'unavailable' if key == 'summary_evidence_status' else None)
            for key in INDEX_FIELDS}
