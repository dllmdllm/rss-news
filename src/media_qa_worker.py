"""Short-lived QA CPU worker; no network, provider calls or persistent writes."""
import base64
import json
import sys
from bs4 import BeautifulSoup
from src.media_qa import inventory, sequence, digest, decode_image, MAX_IMAGES
from src.media_fields import image_url
from src.article_evidence import valid_timestamp
from src.scrape import _process_html_sync
from src.rss_html import sanitize_rss_html


def compare(value):
    if value.get('origin') == 'wordpress-public-posts':
        posts = json.loads(value['html'])
        post = next((p for p in posts if isinstance(p, dict) and p.get('link') == value['url']), None)
        if not post or not isinstance(post.get('content'), dict) or not isinstance(post['content'].get('rendered'), str):
            return {'order_signature': '', 'stored_order_signature': digest(json.dumps(sequence(value['body']))),
                    'image_urls': [], 'embedded_schema': 'unverified', 'collection_scope': 'article not in latest public index'}
        extracted = sanitize_rss_html(post['content']['rendered'], value['url'], dedupe_images=False)
    else:
        extracted, _ = _process_html_sync(value['html'], value['url'], False)
    soup = BeautifulSoup(value['html'], 'html.parser')
    modified = soup.find('meta', attrs={'property': 'article:modified_time'})
    urls = [value.get('thumbnail')] + [tag.get('src') for tag in BeautifulSoup(value['body'], 'html.parser').find_all('img')]
    result = {'order_signature': digest(json.dumps(sequence(extracted))),
              'stored_order_signature': digest(json.dumps(sequence(value['body']))),
              'original_modified_at': valid_timestamp(modified.get('content')) if modified else None,
              'image_urls': list(dict.fromkeys(url for url in urls if image_url(url)))[:MAX_IMAGES]}
    embedded = soup.find('script', id='__NEXT_DATA__')
    if embedded:
        try:
            data = json.loads(embedded.string)
            article = data.get('props', {}).get('initialProps', {}).get('pageProps', {}).get('article') or {}
            supported = {'text', 'image', 'gallery', 'summary', 'quote', 'table', 'related', 'code', 'video', 'ads'}
            result['unsupported_editorial_blocks'] = sorted({str(b.get('blockType')) for b in article.get('blocks', []) if isinstance(b, dict) and b.get('blockType') not in supported})[:20]
        except (TypeError, ValueError, AttributeError):
            result['embedded_schema'] = 'unverified'
    return result


def main():
    payload = sys.stdin.buffer.read(64 * 1024 * 1024 + 1)
    if len(payload) > 64 * 1024 * 1024:
        raise ValueError('QA input bound')
    message = json.loads(payload)
    operation, value = message['operation'], message['value']
    if operation == 'inventory':
        result = inventory(value)
    elif operation == 'compare':
        result = compare(value)
    elif operation == 'decode':
        result = decode_image(base64.b64decode(value, validate=True))
    else:
        raise ValueError('unknown QA operation')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
