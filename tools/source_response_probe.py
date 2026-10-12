"""One guarded public GET per configured affected source; aggregate metadata only."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.feeds import HTTP_HEADERS, RSS_FEEDS
from src.public_http import public_connector, public_get, public_url, read_public_body

NAMES = ('法庭線', 'The Collective HK')
MARKERS = {
    'cloudflare-challenge': (b'cf-chl-', b'challenge-platform', b'checking your browser'),
    'captcha': (b'captcha',),
    'access-denied': (b'access denied', b'access forbidden', b'permission denied'),
    'proxy-error': (b'proxy error', b'proxy authentication', b'web filter'),
    'wordpress-error': (b'error establishing a database connection', b'critical error on this website'),
}


def safe_url(value):
    url = public_url(value)
    return str(url.with_query(None))


def classify(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        value = None
        valid_json = False
    else:
        valid_json = True
    prefix = raw[:4096].lstrip().lower()
    lower = raw.lower()
    return {
        'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'valid_json': valid_json,
        'json_shape': type(value).__name__ if valid_json else None,
        'json_items': len(value) if isinstance(value, list) else None,
        'html_markup': bool(re.search(br'<!doctype\s+html|<html\b|<head\b|<body\b', prefix)),
        # Marker presence is evidence, not proof of the author of a response.
        'markers': [name for name, patterns in MARKERS.items() if any(p in lower for p in patterns)],
    }


async def probe(feed):
    endpoint = public_url(feed['api_url'])
    if endpoint.host != public_url(feed['url']).host or endpoint.path != '/wp-json/wp/v2/posts' or endpoint.query_string:
        raise ValueError('unexpected configured endpoint')
    limit = max(1, min(int(feed.get('max_items', 30)), 40))
    endpoint = endpoint.update_query({
        'page': '1', 'per_page': str(limit), 'status': 'publish', 'context': 'view',
        'orderby': 'date', 'order': 'desc', '_embed': 'wp:featuredmedia',
        '_fields': 'id,link,date_gmt,status,title,content,excerpt,_links,_embedded',
    })
    hops = []
    trace = aiohttp.TraceConfig()

    async def ended(session, context, params):
        hops.append({'url': safe_url(params.url), 'status': params.response.status})

    trace.on_request_end.append(ended)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS,
            connector=public_connector(limit=1), cookie_jar=aiohttp.DummyCookieJar(),
            trace_configs=[trace]) as session:
        try:
            async with public_get(session, str(endpoint),
                    headers={**feed.get('headers', {}), 'Accept': 'application/json'},
                    timeout=aiohttp.ClientTimeout(total=15)) as response:
                status = response.status
                mime = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                mime = mime if re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', mime) else 'unknown'
                final = safe_url(response.url)
                # Do not read denied response bodies or retry after a denial.
                metadata = {} if status in {401, 403, 406, 429, 451} else classify(await read_public_body(response))
            return {'source': feed['name'], 'endpoint': safe_url(endpoint), 'status': status,
                    'content_type': mime, 'final_url': final, 'hops': hops, **metadata}
        except Exception as exc:
            return {'source': feed['name'], 'endpoint': safe_url(endpoint),
                    'hops': hops, 'error_class': type(exc).__name__}


async def run():
    results = []
    feeds = {row['name']: row for row in RSS_FEEDS if row['name'] in NAMES}
    for name in NAMES:
        results.append(await probe(feeds[name]))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(asyncio.wait_for(run(), timeout=40))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
