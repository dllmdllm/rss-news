import asyncio
import socket
from pathlib import Path

import aiohttp
import pytest

from src.public_http import (PublicResolver, UnsafeDestination, public_get,
                             public_url, read_public_body)


@pytest.mark.parametrize('url', [
    'http://127.0.0.1/a', 'http://127.1/', 'http://2130706433/', 'http://0x7f000001/',
    'http://0177.0.0.1/', 'http://127.0.0.1./', 'http://localhost/',
    'http://10.0.0.1/', 'http://169.254.169.254/', 'http://[::1]/',
    'http://[::ffff:127.0.0.1]/', 'http://[fe80::1%25en0]/',
    'http://secret.internal/', 'http://a.local/', 'http://u:p@example.com/',
    'https://example.com:8080/', 'file:///tmp/a', 'https://example.com\\@127.0.0.1/',
    'https://example.com/\npath', ' https://example.com/',
])
def test_unsafe_url_makes_no_request(url):
    class Session:
        def get(self, *args, **kwargs):
            raise AssertionError('unsafe URL must be rejected before HTTP')
    async def run():
        async with public_get(Session(), url):
            raise AssertionError('must reject unsafe URL')
    with pytest.raises(UnsafeDestination):
        asyncio.run(run())


@pytest.mark.parametrize('url', ['https://news.example.com/中文?q=香港',
                               'https://example.com:443/a?x=1&y=2',
                               'http://8.8.8.8/a', 'https://[2606:4700:4700::1111]/'])
def test_public_url_compatibility(url):
    assert public_url(url).scheme in {'http', 'https'}


class Response:
    def __init__(self, status=200, location=None, body=b'body'):
        self.status = status
        self.headers = {'Location': location} if location else {}
        self.body = body
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def read(self): return self.body


class Session:
    def __init__(self, responses): self.responses, self.calls = responses, []
    def get(self, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        self.calls.append(url)
        return self.responses.pop(0)


def test_public_relative_and_cross_host_redirects_remain_usable():
    session = Session([Response(302, '/article'), Response(301, '//cdn.example.org/body'), Response()])
    async def run():
        async with public_get(session, 'https://example.com/feed') as response:
            assert await read_public_body(response) == b'body'
    asyncio.run(run())
    assert session.calls == ['https://example.com/feed', 'https://example.com/article', 'https://cdn.example.org/body']


@pytest.mark.parametrize('target', ['http://127.0.0.1/', 'http://[::ffff:10.0.0.1]/',
                                    'https://example.com/\nprivate'])
def test_redirect_is_checked_before_second_request(target):
    session = Session([Response(302, target)])
    async def run():
        async with public_get(session, 'https://example.com/feed'): pass
    with pytest.raises(UnsafeDestination): asyncio.run(run())
    assert len(session.calls) == 1


def test_redirects_have_one_total_budget_and_hop_limit():
    session = Session([Response(302, '/loop') for _ in range(6)])
    async def run():
        async with public_get(session, 'https://example.com/feed'): pass
    with pytest.raises(UnsafeDestination, match='too many'): asyncio.run(run())
    assert len(session.calls) == 6
    class Slow(Response):
        async def __aenter__(self):
            await asyncio.sleep(.02)
            return self
    session = Session([Slow(302, '/one'), Slow(302, '/two')])
    async def limited():
        async with public_get(session, 'https://example.com/feed', timeout=aiohttp.ClientTimeout(total=.03)): pass
    with pytest.raises(TimeoutError): asyncio.run(limited())


def test_resolver_rejects_all_records_if_any_is_private_and_uses_same_records():
    class Resolver:
        def __init__(self, addresses): self.addresses, self.calls = addresses, 0
        async def resolve(self, host, port, family):
            self.calls += 1
            return [{'hostname': host, 'host': addr, 'port': port, 'family': socket.AF_INET,
                     'proto': 0, 'flags': 0} for addr in self.addresses]
        async def close(self): pass
    for addresses in [['10.0.0.1'], ['8.8.8.8', '127.0.0.1'], ['::ffff:10.0.0.1']]:
        delegate = Resolver(addresses)
        with pytest.raises(UnsafeDestination): asyncio.run(PublicResolver(delegate).resolve('example.com', 443))
        assert delegate.calls == 1
    delegate = Resolver(['8.8.8.8'])
    records = asyncio.run(PublicResolver(delegate).resolve('example.com', 443))
    assert delegate.calls == 1 and records[0]['host'] == '8.8.8.8'


def test_unprotected_real_session_is_rejected_without_dns_or_network():
    async def run():
        async with aiohttp.ClientSession() as session:
            async with public_get(session, 'https://example.com'): pass
    with pytest.raises(UnsafeDestination, match='guarded DNS'): asyncio.run(run())


def test_bounded_response_body():
    with pytest.raises(UnsafeDestination, match='size limit'):
        asyncio.run(read_public_body(Response(body=b'12345'), limit=4))
    class Content:
        async def iter_chunked(self, n):
            yield b'123'
            yield b'456'
    response = Response()
    response.content = Content()
    with pytest.raises(UnsafeDestination, match='size limit'):
        asyncio.run(read_public_body(response, limit=4))


def test_every_publisher_session_uses_guarded_connector():
    root = Path(__file__).resolve().parents[1]
    # Guard old diagnostic helper entry points too: no alternate client may
    # make a second unrestricted DNS lookup after URL validation.
    for path in ['src/fetch.py', 'src/scrape.py', 'src/fast_watch.py', 'tools/hosted_probe.py']:
        source = (root / path).read_text(encoding='utf-8')
        assert 'public_connector(' in source
        assert 'aiohttp.TCPConnector(' not in source
    source = (root / 'src/scrape.py').read_text(encoding='utf-8')
    assert 'urllib.request.urlopen' not in source and 'scraper.get(' not in source
