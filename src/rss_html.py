"""Inert RSS fragments: preserve publisher text/media without active HTML."""
import ipaddress
import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

RSS_HTML_VERSION = 1
_ALLOWED = set('p h1 h2 h3 h4 h5 h6 ul ol li blockquote strong em b i br figure figcaption img a hr table thead tbody tr td th'.split())
_DROP = set('script style iframe object embed form input button textarea select svg math template noscript head'.split())
_BLOCKS = _ALLOWED - {'strong', 'em', 'b', 'i', 'br', 'img', 'a'}


def safe_http_url(value: str, base_url: str = '') -> str | None:
    value = str(value or '').strip()
    if not value or value.startswith('#') or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(urljoin(base_url, value))
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        if parsed.port not in {None, 80, 443}:
            return None
        host = parsed.hostname.lower()
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}', host):
                return None
            if host.endswith(('.local', '.localhost', '.internal')):
                return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, quote(parsed.path, safe="/%:@!$&'()*+,;=-._~"), quote(parsed.query, safe="%:@!$&'()*+,;=/?-._~"), ''))
    except (ValueError, UnicodeError):
        return None


def _image_url(tag, base_url):
    for attr in ('data-src', 'data-original', 'data-lazy-src', 'src'):
        url = safe_http_url(tag.get(attr), base_url)
        if url:
            return url
    # Emit one validated URL; do not copy untrusted srcset syntax into HTML.
    for candidate in str(tag.get('data-srcset') or tag.get('srcset') or '').split(','):
        parts = candidate.strip().split()
        if parts and (len(parts) == 1 or (len(parts) == 2 and re.fullmatch(r'\d+(?:\.\d+)?[wx]', parts[1]))):
            url = safe_http_url(parts[0], base_url)
            if url:
                return url
    return None


def sanitize_rss_html(raw: str, base_url: str = '') -> str:
    soup = BeautifulSoup(raw or '', 'html.parser')
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for tag in list(soup.find_all(_DROP)):
        if tag.name:
            tag.decompose()
    seen_images = set()
    for tag in list(soup.find_all(True)):
        if not tag.name:
            continue
        if tag.name == 'div' and not tag.find(list(_BLOCKS)):
            tag.name = 'p'
        if tag.name not in _ALLOWED:
            tag.unwrap()
            continue
        attrs = dict(tag.attrs)
        tag.attrs = {}
        if tag.name == 'img':
            if 'wp-smiley' in (attrs.get('class') or []):
                tag.replace_with(str(attrs.get('alt') or ''))
                continue
            if any(str(attrs.get(k) or '').isdigit() and int(attrs[k]) <= 2 for k in ('width', 'height')):
                tag.decompose()
                continue
            # Resolve lazy attributes before emitting only an inert img src.
            probe = soup.new_tag('img', attrs=attrs)
            url = _image_url(probe, base_url)
            if not url or url in seen_images:
                tag.decompose()
                continue
            seen_images.add(url)
            tag['src'] = url
            tag['alt'] = str(attrs.get('alt') or '')
            tag['loading'] = 'lazy'
            tag['decoding'] = 'async'
            for key in ('width', 'height'):
                if str(attrs.get(key) or '').isdigit() and 2 < int(attrs[key]) <= 20000:
                    tag[key] = str(attrs[key])
        elif tag.name == 'a':
            url = safe_http_url(attrs.get('href'), base_url)
            if url:
                tag['href'] = url
                tag['target'] = '_blank'
                tag['rel'] = 'noopener noreferrer'
    return str(soup)


def first_rss_image(raw: str, base_url: str = '') -> str | None:
    image = BeautifulSoup(sanitize_rss_html(raw, base_url), 'html.parser').find('img')
    return image.get('src') if image else None
