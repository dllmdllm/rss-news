"""Publisher GETs restricted to public destinations, including DNS and redirects.

The resolver is part of the connection path: approving a separate DNS lookup
and then resolving again for the request would leave a rebinding gap.
"""
import asyncio
import ipaddress
import re
import socket
from contextlib import asynccontextmanager

import aiohttp
from yarl import URL

MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_CLIENT_SESSION_TYPE = aiohttp.ClientSession


class UnsafeDestination(ValueError):
    """Publisher input attempted to leave the public HTTP destination boundary."""


def _public_address(value):
    if '%' in str(value):
        raise UnsafeDestination('scoped address is not public')
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise UnsafeDestination('invalid resolved address') from exc
    if not address.is_global or (getattr(address, 'ipv4_mapped', None)
                                 and not address.ipv4_mapped.is_global):
        raise UnsafeDestination('non-public destination address')
    return address


def public_url(value):
    raw = str(value or '')
    if not raw or raw != raw.strip() or '\\' in raw or any(ord(c) < 33 or ord(c) == 127 for c in raw):
        raise UnsafeDestination('invalid HTTP URL')
    try:
        url = URL(raw)
        host = url.host
        if url.scheme not in {'http', 'https'} or not host or url.user is not None or url.password is not None:
            raise UnsafeDestination('HTTP(S) URL without credentials required')
        if url.port not in {80, 443}:
            raise UnsafeDestination('nonstandard destination port')
        if '%' in host:
            raise UnsafeDestination('scoped address is not public')
        try:
            address = ipaddress.ip_address(host.rstrip('.'))
        except ValueError:
            # Use aiohttp/yarl's IDNA representation for public hostnames.
            normalized = url.raw_host.rstrip('.').lower()
            if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}', normalized):
                raise UnsafeDestination('invalid public hostname')
            if normalized.endswith(('.local', '.localhost', '.internal', '.test', '.invalid')):
                raise UnsafeDestination('non-public hostname')
        else:
            _public_address(str(address))
        return url.with_fragment(None)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, UnsafeDestination):
            raise
        raise UnsafeDestination('invalid HTTP URL') from exc


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, resolver=None):
        self._resolver = resolver or aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self._resolver.resolve(host, port, family)
        if not records:
            raise UnsafeDestination('destination DNS has no addresses')
        for record in records:
            _public_address(record['host'])
        return records

    async def close(self):
        await self._resolver.close()


def public_connector(**kwargs):
    # aiohttp skips DNS resolvers for IP literals. public_get validates those
    # before issuing a request; named hosts use these exact approved records.
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), **kwargs)
    # TCPConnector normally leaves an explicitly supplied resolver open. This
    # resolver belongs to this connector and must close with its session.
    connector._resolver_owner = True
    return connector


@asynccontextmanager
async def public_get(session, url, **kwargs):
    current = public_url(url)
    if isinstance(session, _CLIENT_SESSION_TYPE) and not isinstance(
            getattr(session.connector, '_resolver', None), PublicResolver):
        raise UnsafeDestination('publisher session requires guarded DNS resolver')
    timeout = kwargs.get('timeout', getattr(session, 'timeout', None))
    total = getattr(timeout, 'total', None)
    async with asyncio.timeout(total):
        for hop in range(MAX_REDIRECTS + 1):
            async with session.get(str(current), allow_redirects=False, **kwargs) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    if hop == MAX_REDIRECTS:
                        raise UnsafeDestination('too many publisher redirects')
                    location = response.headers.get('Location')
                    if not location:
                        raise UnsafeDestination('publisher redirect missing Location')
                    # Validate raw Location before URL joining can strip controls.
                    if '\\' in location or any(ord(c) < 33 or ord(c) == 127 for c in location):
                        raise UnsafeDestination('invalid publisher redirect')
                    current = public_url(current.join(URL(location)))
                    continue
                yield response
                return


async def read_public_body(response, limit=MAX_RESPONSE_BYTES):
    """Bound memory consumed by publisher responses before HTML/XML parsing."""
    length = getattr(response, 'content_length', None)
    if isinstance(length, int) and length > limit:
        raise UnsafeDestination('publisher response exceeds size limit')
    content = getattr(response, 'content', None)
    if content is None:
        # Lightweight fake responses in parser tests have only read().
        raw = await response.read()
        if len(raw) > limit:
            raise UnsafeDestination('publisher response exceeds size limit')
        return raw
    chunks, size = [], 0
    async for chunk in content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > limit:
            raise UnsafeDestination('publisher response exceeds size limit')
        chunks.append(chunk)
    return b''.join(chunks)
