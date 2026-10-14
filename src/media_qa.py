"""Bounded public media observations; no enrichment, paid provider or repair calls."""
import asyncio
import base64
import hashlib
import io
import json
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from bs4 import BeautifulSoup
from src.feeds import RSS_FEEDS, HTTP_HEADERS
from src.media_fields import image_url
from src.public_http import UnsafeDestination, public_connector, public_get, read_public_body
from src.scrape import SourceAccessDenied, SourceHTTPError, _fetch_html, _is_blocked

STATE_PATH = Path(__file__).resolve().parents[1] / 'docs/data/media_qa.json'
BUDGET_SECONDS = 60
MAX_SOURCES = 6
MAX_IMAGES = 3
STATE_VERSION = 1


def digest(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def atomic_save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    os.replace(tmp, path)


def load_state(path):
    try:
        if Path(path).stat().st_size > 2 * 1024 * 1024:
            raise ValueError('checkpoint too large')
        state = json.loads(Path(path).read_text(encoding='utf-8'))
        if state.get('version') == STATE_VERSION and isinstance(state.get('sources'), dict):
            return state
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {'version': STATE_VERSION, 'cursor': 0, 'sources': {}}


async def cpu_task(operation, value, deadline):
    """Kill the child on expiry; unlike threads, native parser work cannot linger."""
    raw = json.dumps({'operation': operation, 'value': value}, ensure_ascii=False).encode('utf-8')
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError('QA CPU input exceeds bound')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('QA budget expired')
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'src.media_qa_worker',
        cwd=str(Path(__file__).resolve().parents[1]), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(raw), max(.01, deadline - time.monotonic()))
        if process.returncode or len(output) > 8 * 1024 * 1024:
            raise ValueError('QA CPU worker failed or output exceeds bound')
        return json.loads(output)
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), 2)


def sequence(content):
    """Stable interleaved editorial block signature, retaining repeated images."""
    soup = BeautifulSoup(content or '', 'html.parser')
    return [(tag.name, digest(tag.get('src', '') if tag.name == 'img' else tag.get_text(' ', strip=True)))
            for tag in soup.find_all(['p', 'img', 'figcaption', 'blockquote', 'table'])]


def inventory(articles):
    ids = set()
    urls = set()
    findings = []
    for article in articles:
        aid = article.get('id')
        if not isinstance(aid, str) or not aid or aid in ids:
            raise ValueError('media QA: missing or duplicate article ID')
        ids.add(aid)
        url = article.get('url')
        if url and url in urls:
            raise ValueError('media QA: duplicate canonical article URL')
        if url:
            urls.add(url)
        if article.get('date'):
            stamp = datetime.fromisoformat(article['date'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('media QA: timezone-naive article date')
        if article.get('content_hash') and article.get('content') and article['content_hash'] != digest(article['content']):
            raise ValueError('media QA: current body hash mismatch for ' + aid)
        thumbnail = article.get('thumbnail')
        if thumbnail and image_url(thumbnail) != thumbnail:
            raise ValueError('media QA: invalid thumbnail for ' + aid)
        soup = BeautifulSoup(article.get('content') or '', 'html.parser')
        images = [tag.get('src', '') for tag in soup.find_all('img')]
        invalid = sum(not image_url(url) for url in images)
        if invalid:
            raise ValueError('media QA: invalid inline image for ' + aid)
        if len(images) != len(set(images)):
            findings.append({'id': aid, 'kind': 'repeated-image-occurrence', 'count': len(images) - len(set(images)),
                             'interpretation': 'source comparison required; not deleted'})
    return {'records': len(ids), 'findings': findings[:200], 'findings_total': len(findings)}


def fingerprint(row):
    # Same persistent problem does not alert again merely because newest ID changed.
    return digest(json.dumps({'status': row.get('status'), 'problem': row.get('problem'),
                             'unsupported_blocks': row.get('unsupported_editorial_blocks', []),
                             'image_failures': sorted((i.get('status'), i.get('url_hash', ''), i.get('reason', '')) for i in row.get('images', []) if i.get('status') == 'failed')}, sort_keys=True))


def fold(previous, observation, now):
    row = dict(observation)
    row['fingerprint'] = fingerprint(row)
    failed = row['status'] == 'failed'
    streak = int(previous.get('failures', 0)) + 1 if failed else 0
    row['failures'] = min(streak, 20)
    row['next_eligible'] = now + min(360, 20 * 2 ** min(max(streak - 1, 0), 5)) * 60 if failed else now
    row['acknowledged'] = previous.get('acknowledged')
    row['pending_event'] = previous.get('pending_event')
    row['last_good'] = previous.get('last_good')
    if row['status'] == 'verified':
        row['last_good'] = {'checked_at': row['checked_at'], 'article_id': row['article_id'], 'body_hash': row['body_hash']}
        row['pending_event'] = ({'status': 'verified', 'fingerprint': row['fingerprint']}
                                if row['acknowledged'] and row['acknowledged'] != row['fingerprint'] else None)
    elif failed and row['acknowledged'] != row['fingerprint']:
        row['pending_event'] = {'status': 'failed', 'fingerprint': row['fingerprint'], 'problem': row.get('problem')}
    if row['status'] == 'unverified':
        # Budget expiry / missing decoder / partial body is not recovery.
        row['acknowledged'] = previous.get('acknowledged')
        if (row.get('pending_event') or {}).get('status') == 'verified':
            row['pending_event'] = None
    return row


async def bounded_read(factory, deadline):
    """One retry for transient failures; never retry denial/TLS/unsafe URLs."""
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('QA budget expired')
        try:
            return await asyncio.wait_for(factory(), timeout=min(15, remaining))
        except (SourceAccessDenied, UnsafeDestination, aiohttp.ClientSSLError, ssl.SSLError):
            raise
        except SourceHTTPError as exc:
            if exc.status < 500 or attempt == 1:
                raise
        except (TimeoutError, aiohttp.ClientConnectionError):
            if attempt == 1:
                raise
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            raise TimeoutError('QA budget expired')
        await asyncio.sleep(min(1, remaining))


def decode_image(raw):
    # Reuse an installed decoder only. Never add/install dependencies on a runner.
    try:
        from PIL import Image
    except ImportError:
        return {'status': 'unverified', 'reason': 'image decoder unavailable'}
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if width <= 0 or height <= 0:
                return {'status': 'failed', 'reason': 'invalid image dimensions'}
            if width * height > 20_000_000:
                return {'status': 'unverified', 'reason': 'image dimensions exceed QA decode bound'}
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()  # Decode the selected image's first frame, not only MIME.
        return {'status': 'verified', 'width': width, 'height': height, 'decoded_frames': 1}
    except Exception:
        return {'status': 'failed', 'reason': 'image decode failed'}


async def check_image(session, url, deadline):
    async def read():
        async with public_get(session, url, timeout=aiohttp.ClientTimeout(total=8)) as response:
            if response.status in {401, 403, 406, 429, 451}:
                raise SourceAccessDenied('HTTP ' + str(response.status))
            if response.status >= 400:
                raise SourceHTTPError(response.status)
            mime = response.headers.get('Content-Type', '').split(';')[0].lower()
            raw = await read_public_body(response)
            return mime, raw
    try:
        mime, raw = await bounded_read(read, deadline)
        if not mime.startswith('image/'):
            return {'url_hash': digest(url), 'status': 'failed', 'reason': 'non-image response'}
        result = await cpu_task('decode', base64.b64encode(raw).decode('ascii'), deadline)
        return dict(result, url_hash=digest(url), response_hash=digest(raw), mime=mime)
    except (SourceAccessDenied, UnsafeDestination, aiohttp.ClientSSLError, ssl.SSLError) as exc:
        return {'url_hash': digest(url), 'status': 'failed', 'reason': type(exc).__name__}
    except Exception as exc:
        return {'url_hash': digest(url), 'status': 'unverified' if time.monotonic() >= deadline else 'failed', 'reason': type(exc).__name__}


async def observe(session, article, previous, deadline):
    body = article.get('content') or ''
    row = {'article_id': article['id'], 'body_hash': digest(body), 'checked_at': datetime.now(timezone.utc).isoformat(),
           'status': 'unverified', 'completeness': (article.get('content_quality') or {}).get('completeness', 'unverified')}
    try:
        feed = next((f for f in RSS_FEEDS if f['name'] == article.get('source')), {})
        origin = 'article-html'
        original_url = article['url']
        if feed.get('fetcher') == 'wordpress_public':
            # Pick the already configured reader before any request, never as
            # an alternate access route after a denied article-page request.
            origin = 'wordpress-public-posts'
            original_url = feed['api_url'] + '?' + urlencode({'page': '1', 'status': 'publish', 'context': 'view',
                'orderby': 'date', 'order': 'desc', 'per_page': min(int(feed.get('max_items', 30)), 100),
                '_embed': 'wp:featuredmedia', '_fields': 'id,link,date_gmt,status,title,content,excerpt,_links,_embedded'})
        async def read_original():
            if origin == 'article-html':
                return await _fetch_html(session, original_url)
            async with public_get(session, original_url, headers={**feed.get('headers', {}), 'Accept': 'application/json'}, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status in {401, 403, 406, 429, 451}:
                    raise SourceAccessDenied('public index denied')
                if response.status >= 400:
                    raise SourceHTTPError(response.status)
                return (await read_public_body(response)).decode(response.charset or 'utf-8', errors='replace')
        html = await bounded_read(read_original, deadline)
        if _is_blocked(html):
            raise SourceAccessDenied('publisher challenge')
        original_hash = digest(html)
        compared = await cpu_task('compare', {'html': html, 'url': article['url'], 'body': body,
                                             'thumbnail': article.get('thumbnail'), 'origin': origin}, deadline)
        row.update(compared, original_hash=original_hash, original_observed_at=row['checked_at'], original_reader=origin)
        if previous.get('article_id') == article['id'] and previous.get('original_hash') and previous['original_hash'] != original_hash:
            row['revision_comparison'] = 'original-changed; historical parser order not inferred'
        else:
            row['revision_comparison'] = 'first-observation' if previous.get('article_id') != article['id'] else 'same-original'
        if row['order_signature'] != row['stored_order_signature']:
            row['order_comparison'] = 'difference; translation, hero extraction or source revision needs comparison'
        else:
            row['order_comparison'] = 'matches-current-extractor; not independent completeness proof'
        urls = row.pop('image_urls')
        row['images'] = [await check_image(session, url, deadline) for url in urls]
        row['image_sample_count'] = len(urls)
        if any(image['status'] == 'failed' for image in row['images']):
            row.update(status='failed', problem='image-unavailable')
        elif any(image['status'] == 'unverified' for image in row['images']):
            row['problem'] = 'image-check-unverified'
        elif row.get('unsupported_editorial_blocks'):
            row.update(status='failed', problem='unsupported-editorial-blocks')
        elif row.get('embedded_schema') == 'unverified':
            row['problem'] = 'editorial-block-schema-unverified'
        elif row['order_signature'] != row['stored_order_signature']:
            row['problem'] = 'order-comparison-unverified; no historical reorder inferred'
        elif (article.get('content_quality') or {}).get('fallback') == 'none' and not article.get('content_retention'):
            row['status'] = 'verified'
        else:
            row['problem'] = 'partial-or-retained-body; completeness-unverified'
    except (SourceAccessDenied, UnsafeDestination, aiohttp.ClientSSLError, ssl.SSLError) as exc:
        row.update(status='failed', problem=type(exc).__name__)
    except Exception as exc:
        row.update(status='unverified' if time.monotonic() >= deadline else 'failed', problem=type(exc).__name__)
    return row


async def run_media_qa(articles, *, path=STATE_PATH, feeds=None, budget=BUDGET_SECONDS):
    started = time.monotonic()
    # Reserve two seconds to reap a killed parser/decoder before the hard cap.
    deadline = started + max(0, min(max(budget, 0), BUDGET_SECONDS) - 2)
    feeds = RSS_FEEDS if feeds is None else feeds
    if budget <= 2:
        report = {'records': len(articles), 'status': 'unverified-budget', 'findings': []}
    else:
        try:
            report = await cpu_task('inventory', [{key: a.get(key) for key in ('id', 'url', 'date', 'content', 'content_hash', 'thumbnail')} for a in articles], deadline)
        except TimeoutError:
            report = {'records': len(articles), 'status': 'unverified-budget', 'findings': []}
    names = [feed['name'] for feed in feeds]
    state = load_state(path)
    state['sources'] = {name: value for name, value in state['sources'].items() if name in names and isinstance(value, dict)}
    state['config_hash'] = digest(json.dumps(feeds, sort_keys=True))
    state['inventory'] = report
    state['revision'] = os.getenv('GITHUB_SHA') or 'local-unpublished'
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    now = time.time()
    cursor = int(state.get('cursor', 0)) % max(1, len(names))
    selected = []
    for offset in range(len(names)):
        index = (cursor + offset) % len(names)
        name = names[index]
        previous = state['sources'].get(name, {})
        rows = [a for a in articles if a.get('source') == name]
        if rows and now >= float(previous.get('next_eligible', 0)):
            selected.append((name, max(rows, key=lambda a: a.get('date', '')), previous))
        state['cursor'] = (index + 1) % len(names)
        if len(selected) == MAX_SOURCES:
            break
    state['coverage'] = {'selected_sources': len(selected), 'configured_sources': len(names), 'max_images_per_article': MAX_IMAGES,
                         'scope': 'rotating samples; remaining originals/images unverified'}
    atomic_save(path, state)
    async with aiohttp.ClientSession(headers=HTTP_HEADERS, connector=public_connector(limit=6)) as session:
        for name, article, previous in selected:
            if time.monotonic() >= deadline:
                observation = {'article_id': article['id'], 'body_hash': digest(article.get('content') or ''),
                               'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'unverified', 'problem': 'QA budget expired'}
            else:
                try:
                    observation = await asyncio.wait_for(observe(session, article, previous, deadline), max(.01, deadline - time.monotonic()))
                except TimeoutError:
                    observation = {'article_id': article['id'], 'body_hash': digest(article.get('content') or ''),
                                   'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'unverified', 'problem': 'QA budget expired'}
            state['sources'][name] = fold(previous, observation, time.time())
            atomic_save(path, state)
    return state


async def notify_media_qa(*, path=STATE_PATH, sender=None):
    """Existing Telegram audience; failed sends leave transitions unacknowledged."""
    from src.breaking_alert import TELEGRAM_BOT_TOKEN, _send_telegram
    if sender is None and not TELEGRAM_BOT_TOKEN:
        return []
    state = load_state(path)
    sent = []
    async with aiohttp.ClientSession() as session:
        for name, row in state['sources'].items():
            event = row.get('pending_event')
            if not isinstance(event, dict) or event.get('status') not in {'failed', 'verified'}:
                continue
            fingerprint_value = event.get('fingerprint')
            acknowledged = row.get('acknowledged')
            if fingerprint_value == acknowledged or (event['status'] == 'verified' and not acknowledged):
                continue
            message = ('RSS 圖文 QA：問題 ' if event['status'] == 'failed' else 'RSS 圖文 QA：抽樣恢復 ') + name + '\n' + str(event.get('problem') or '公開原文／抽樣圖片已驗證')
            success = await sender(message) if sender else await _send_telegram(session, message)
            if success:
                row['acknowledged'] = fingerprint_value
                row['pending_event'] = None
                atomic_save(path, state)
                sent.append(name)
    return sent
