"""Deterministic media boundaries; no networking or provider calls."""
import re
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from src.rss_html import safe_http_url
from src.public_http import public_url, UnsafeDestination


def image_url(value, base_url=''):
    raw = str(value or '').strip()
    if not raw or len(raw) > 8192 or any(c in raw for c in '<>"\\') or any(ord(c) < 33 for c in raw):
        return None
    url = safe_http_url(raw, base_url)
    if not url:
        return None
    try:
        public_url(url)
    except UnsafeDestination:
        return None
    path = urlsplit(url).path.lower()
    if '/_nuxt/default.' in path or path.endswith(('.html', '.htm', '.php')):
        return None
    return url


def repair_thumbnail(article):
    base = article.get('url') or ''
    valid = image_url(article.get('thumbnail'), base)
    if not valid:
        # Same-article content only, never discover an unrelated replacement.
        for tag in BeautifulSoup(article.get('content') or article.get('rss_content') or '', 'html.parser').find_all('img'):
            valid = image_url(tag.get('src') or tag.get('data-src'), base)
            if valid:
                break
    article['thumbnail'] = valid
    return valid


def clean_unit_markers(content):
    """Only strip a complete, sequential 0-based translation-unit echo."""
    soup = BeautifulSoup(content or '', 'html.parser')
    nodes = [n for n in soup.find_all(string=True) if n.strip() and not n.parent.name in {'script', 'style'}]
    matches = [re.match(r'^(\s*)\[(\d+)\]\s+', str(n)) for n in nodes]
    if len(nodes) < 3 or not all(matches) or [int(m[2]) for m in matches] != list(range(len(nodes))):
        return content, False
    for node, match in zip(nodes, matches):
        node.replace_with(match[1] + str(node)[match.end():])
    return str(soup), True
