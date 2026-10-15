"""Deterministic media boundaries; no networking or provider calls."""
import re
import hashlib
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


def repair_inline_images(article):
    """Omit rejected source image URLs before strict inventory; retain text/order.

    No request, alternate route or fabricated replacement image is involved.
    Leave unchanged bodies byte-identical, and disclose omissions in quality.
    """
    content = article.get('content')
    if not content:
        return 0
    soup = BeautifulSoup(content, 'html.parser')
    changed = False
    omitted = 0
    for tag in soup.find_all('img'):
        # Publishers sometimes emit an unescaped space inside a filename.
        # Encode that literal byte, then require the unchanged strict guard;
        # never encode controls/quotes or accept a rejected destination.
        candidate = str(tag.get('src') or '').strip().replace(' ', '%20')
        valid = image_url(candidate, article.get('url') or '')
        if not valid:
            tag.decompose()
            omitted += 1
            changed = True
        elif tag.get('src') != valid:
            tag['src'] = valid
            changed = True
    if changed:
        article['content'] = str(soup)
        if article.get('content_hash'):
            article['content_hash'] = hashlib.sha256(article['content'].encode('utf-8')).hexdigest()
    if omitted:
        quality = dict(article.get('content_quality') or {})
        quality['omitted_invalid_images'] = int(quality.get('omitted_invalid_images') or 0) + omitted
        quality['images'] = len(soup.find_all('img'))
        article['content_quality'] = quality
    return omitted


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
