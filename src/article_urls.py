"""Dependency-free outbound article-link boundary, without network requests."""
import re
from urllib.parse import urlsplit


def valid_article_url(value):
    if not isinstance(value, str) or re.search(r'[\x00-\x20\x7f\\]', value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme in {'http', 'https'} and bool(url.hostname)
                and '@' not in url.netloc and bool(url.port is None or 0 < url.port < 65536))
    except ValueError:
        return False
