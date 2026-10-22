"""Publisher identities from documented URL shapes; never infer from titles."""
import hashlib
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


def canonical_url(url):
    parts = urlsplit(str(url or ''))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ''))


def publisher_identity(article):
    parts = urlsplit(str(article.get('url') or ''))
    host, path = (parts.hostname or '').removeprefix('www.'), unquote(parts.path)
    patterns = {
        'news.tvb.com': r'^/tc/(\d+)(?:-|/|$)',
        'hk01.com': r'^/[^/]+/(\d+)(?:/|$)',
        'stheadline.com': r'^/[^/]+/(\d+)(?:/|$)',
        'am730.com.hk': r'^/[^/]+/(\d+)(?:/|$)',
        'news.rthk.hk': r'/component/k2/(\d+)-',
        'hk.on.cc': r'/(bkn-[^/]+)\.html$',
        'cnbeta.com.tw': r'^/articles/[^/]+/(\d+)\.htm',
        'hkepc.com': r'^/(\d+)/',
        'weekendhk.com': r'-(\d+)/?$',
        'gotrip.hk': r'-(\d+)/?$',
        'huffpost.com': r'_n_([a-f0-9]+)$',
    }
    match = re.search(patterns[host], path) if host in patterns else None
    if match:
        return host, match[1]
    if host == 'news.now.com':
        news_id = dict(parse_qsl(parts.query)).get('newsId', '')
        if news_id.isdigit():
            return host, news_id
    if host in {'hkcourtnews.com', 'thecollectivehk.com'} and article.get('publisher_article_id'):
        return host, str(article['publisher_article_id'])
    return host, None


def identity_key(article):
    publisher, aid = publisher_identity(article)
    return f'{publisher}:{aid}' if aid else canonical_url(article.get('url'))


def memberships(article):
    rows = list(article.get('memberships') or []) + [
        {'source': article.get('source'), 'category': article.get('category')}]
    return list({(r.get('source'), r.get('category')): r for r in rows
                 if isinstance(r, dict) and r.get('source')}.values())


def reconcile(articles, previous=()):
    """Retain published IDs/aliases through slug changes and feed memberships.

    Existing IDs remain device-state keys. New recognized publisher IDs are
    deterministic; unknown URL/GUID semantics remain URL identities.
    """
    old = {}
    old_groups = {}
    old_urls = {}
    for row in previous:
        old.setdefault(identity_key(row), row)
        old_groups.setdefault(identity_key(row), []).append(row)
        for url in [row.get('url'), *(row.get('url_aliases') or [])]:
            old_urls.setdefault(canonical_url(url), row)
    result = {}
    for incoming in articles:
        row = dict(incoming)
        key = identity_key(row)
        prior = old.get(key) or old_urls.get(canonical_url(row.get('url')))
        priors = old_groups.get(identity_key(prior), [prior]) if prior else []
        publisher, publisher_aid = publisher_identity(row)
        row['publisher_id'] = publisher
        if publisher_aid:
            row['publisher_article_id'] = publisher_aid
        original_id = row['id']
        row['id'] = prior['id'] if prior else (
            hashlib.md5(key.encode()).hexdigest()[:12] if publisher_aid else original_id)
        row['canonical_url'] = row['url']
        row['id_aliases'] = sorted(set([original_id, *[p['id'] for p in priors],
                                       *[alias for p in priors for alias in p.get('id_aliases') or []]]) - {row['id']})
        row['url_aliases'] = sorted(set([row['url'], *[url for p in priors
            for url in [p.get('url'), *(p.get('url_aliases') or [])] if url]]))[-64:]
        row['memberships'] = memberships(row) + (memberships(prior) if prior else [])
        for prior_member in priors:
            row['memberships'] += memberships(prior_member)
        if key in result:
            kept = result[key]
            kept['memberships'] += row['memberships']
            kept['id_aliases'] = sorted(set(kept['id_aliases'] + row['id_aliases'] + [row['id']]) - {kept['id']})
        else:
            result[key] = row
    for row in result.values():
        row['memberships'] = memberships(row)
    return list(result.values())
