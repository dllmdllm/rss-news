"""Owned aggregate verification: bounded streams, no live HTTP or providers."""
import asyncio
import gzip
import hashlib
import json
import ssl
import subprocess
import zlib

import pytest

from src import public_http
from tools import verify_publication as V


class Content:
    def __init__(self, raw):
        self.raw, self.position, self.requests = raw, 0, []

    async def read(self, size):
        assert 0 < size <= V.CHUNK_BYTES
        self.requests.append(size)
        chunk = self.raw[self.position:self.position + size]
        self.position += len(chunk)
        return chunk

    async def iter_chunked(self, size):
        while chunk := await self.read(size):
            yield chunk


class Response:
    def __init__(self, raw, *, encoding=None, status=200, length=None):
        self.content = Content(raw)
        self.headers = {'Content-Encoding': encoding} if encoding else {}
        self.status, self.content_length, self.closed = status, length, False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def stream(raw, *, expected_size=None, path=V.INDEX, encoding=None, length=None):
    response = Response(raw, encoding=encoding, length=length)
    session = Session(response)
    digest = asyncio.run(V.public_digest(session, path, 'a' * 40,
        len(raw) if expected_size is None else expected_size))
    assert response.closed
    return digest, session


def test_owned_regression_file_above8mib_is_streamed_with_exact_bytes(monkeypatch):
    raw = b'x' * 8452306

    async def forbidden(*args):
        pytest.fail('streaming verifier must not call the buffered publisher reader')

    monkeypatch.setattr(V, 'read_public_body', forbidden)
    actual, session = stream(raw)
    assert actual == hashlib.sha256(raw).hexdigest()
    assert max(session.response.content.requests) <= 64 * 1024
    assert session.calls[0][1]['auto_decompress'] is False
    assert session.calls[0][1]['headers']['Accept-Encoding'] == 'identity'
    assert session.calls[0][1]['timeout'].total == 8
    assert session.calls[0][1]['allow_redirects'] is False
    assert public_http.MAX_RESPONSE_BYTES == 8 * 1024 * 1024


@pytest.mark.parametrize('encoding', ['gzip', 'deflate'])
def test_compressed_transfer_hashes_decoded_canonical_bytes(encoding):
    raw = ('廣東話原文\n' * 30000).encode()
    compressed = gzip.compress(raw) if encoding == 'gzip' else zlib.compress(raw)
    actual, session = stream(compressed, expected_size=len(raw), encoding=encoding,
                             length=len(compressed))
    assert actual == V.digest(raw)
    assert session.response.content.position == len(compressed)


@pytest.mark.parametrize('header', [None, 1, 999999999])
def test_missing_or_wrong_content_length_never_replaces_actual_count(header):
    actual, _ = stream(b'canonical', length=header)
    assert actual == V.digest(b'canonical')


@pytest.mark.parametrize('raw,expected_size', [(b'short', 10), (b'longer', 3)])
def test_truncated_or_extra_decoded_bytes_are_rejected_and_closed(raw, expected_size):
    response = Response(raw)
    session = Session(response)
    with pytest.raises(ValueError, match='length'):
        asyncio.run(V.public_digest(session, V.INDEX, 'a' * 40, expected_size))
    assert response.closed and len(session.calls) == 1
    assert response.content.position <= expected_size + 1


def test_same_length_wrong_hash_remains_unverified(monkeypatch):
    monkeypatch.setattr(V, 'MAX_ATTEMPTS', 1)
    expected = {V.INDEX: V.digest(b'correct')}
    session = Session(Response(b'changed'))

    async def fetched(path):
        return await V.public_digest(session, path, 'a' * 40, 7)

    result = asyncio.run(V.verify_digests(expected, fetched))
    assert result['status'] == 'unverified' and result['checked_files'] == []
    assert result['attempts'] == 1 and 'bytes differ' in result['reason']


@pytest.mark.parametrize('path,size', [
    ('data/articles.json', 32 * 1024 * 1024 + 1),
    (V.INDEX, 32 * 1024 * 1024 + 1),
    ('article.html', 8 * 1024 * 1024 + 1),
    ('data/content/story.json', 8 * 1024 * 1024 + 1),
    ('data/articles.json.bak', 9 * 1024 * 1024),
    ('../../.env', 1),
])
def test_only_two_owned_paths_get_the32mib_budget_before_http(path, size):
    session = Session(Response(b''))
    with pytest.raises(V.SnapshotError):
        asyncio.run(V.public_digest(session, path, 'a' * 40, size))
    assert not session.calls
    assert V.AGGREGATE_PATHS == {'data/articles.json', 'data/articles_index.json'}
    assert V.SNAPSHOT_MAX_BYTES == 64 * 1024 * 1024


def test_generic_publisher_reader_still_rejects_over8mib():
    response = Response(b'x' * (8 * 1024 * 1024 + 1), length=8 * 1024 * 1024 + 1)
    with pytest.raises(public_http.UnsafeDestination, match='size limit'):
        asyncio.run(public_http.read_public_body(response))
    assert response.content.position == 0


def test_encoded_body_has_an_independent_limit(monkeypatch):
    monkeypatch.setattr(V, 'MAX_RESPONSE_BYTES', 16)
    session = Session(Response(b'x' * 17))
    with pytest.raises(V.PermanentFailure, match='encoded body exceeds 16'):
        asyncio.run(V.public_digest(session, 'article.html', 'a' * 40, 16))
    assert session.response.closed


def test_gzip_expansion_stops_at_expected_length_plus_one(monkeypatch):
    compressed = gzip.compress(b'x' * (10 * 1024 * 1024))
    factory = zlib.decompressobj
    expansions = []

    class Decoder:
        def __init__(self, *args):
            self.real = factory(*args)

        def __getattr__(self, name):
            return getattr(self.real, name)

        def decompress(self, data, maximum):
            assert 0 < maximum <= V.CHUNK_BYTES
            raw = self.real.decompress(data, maximum)
            expansions.append((maximum, len(raw)))
            return raw

    monkeypatch.setattr(V.zlib, 'decompressobj', Decoder)
    session = Session(Response(compressed, encoding='gzip'))
    with pytest.raises(ValueError, match='length exceeds expected 10'):
        asyncio.run(V.public_digest(session, V.INDEX, 'a' * 40, 10))
    assert expansions == [(11, 11)] and session.response.closed


@pytest.mark.parametrize('kind', ['truncated', 'extra', 'corrupt'])
def test_bad_gzip_streams_fail_closed(kind):
    body = gzip.compress(b'content')
    body = body[:-3] if kind == 'truncated' else body + b'junk' if kind == 'extra' else b'bad gzip'
    session = Session(Response(body, encoding='gzip'))
    with pytest.raises(ValueError, match='compressed'):
        asyncio.run(V.public_digest(session, V.INDEX, 'a' * 40, 7))
    assert session.response.closed


def test_external_cancellation_closes_the_active_stream():
    async def scenario():
        started = asyncio.Event()

        class Blocked(Content):
            async def read(self, size):
                started.set()
                await asyncio.Event().wait()

        response = Response(b'')
        response.content = Blocked(b'')
        session = Session(response)
        task = asyncio.create_task(V.public_digest(session, V.INDEX, 'a' * 40, 10))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert response.closed and len(session.calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('status', [401, 403, 406, 429, 451])
def test_denial_stops_stream_verification_without_reading_body(status):
    response = Response(b'private challenge', status=status)
    session = Session(response)
    with pytest.raises(V.PermanentFailure, match=str(status)):
        asyncio.run(V.public_digest(session, V.INDEX, 'a' * 40, 10))
    assert response.closed and response.content.position == 0


def test_stream_tls_failure_is_permanent_without_relaxing_checks():
    class BrokenTLS(Session):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise ssl.SSLCertVerificationError('invalid certificate')

    session = BrokenTLS(Response(b''))
    with pytest.raises(V.PermanentFailure, match='SSLCertVerificationError'):
        asyncio.run(V.public_digest(session, V.INDEX, 'a' * 40, 0))
    assert len(session.calls) == 1


def test_stream_private_redirect_is_stopped_before_contacting_target():
    response = Response(b'', status=302)
    response.headers['Location'] = 'http://127.0.0.1/private'
    session = Session(response)
    with pytest.raises(V.PermanentFailure, match='UnsafeDestination'):
        asyncio.run(V.public_digest(session, V.INDEX, 'a' * 40, 0))
    assert len(session.calls) == 1 and response.closed


def test_request_deadline_closes_a_stalled_stream(monkeypatch):
    monkeypatch.setattr(V, 'REQUEST_SECONDS', .01)

    class Stalled(Content):
        async def read(self, size):
            await asyncio.Event().wait()

    response = Response(b'')
    response.content = Stalled(b'')
    # The shared reader preserves its existing retryable timeout diagnostic.
    with pytest.raises(ValueError, match='TimeoutError'):
        asyncio.run(V.public_digest(Session(response), V.INDEX, 'a' * 40, 10))
    assert response.closed


def fixture_repo(tmp_path, *, padding=0):
    payload = {'schema_version': 2, 'articles': [{'id': 'news'}], 'padding': 'x' * padding}
    for path in V.CORE_PATHS + ('data/content/news.json',):
        f = tmp_path / 'docs' / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(json.dumps(payload).encode() if path == 'data/articles.json' else b'current\n')
    git = V._git(tmp_path)
    subprocess.run(git + ['init', '-q'], check=True, capture_output=True)
    subprocess.run(git + ['add', 'docs'], check=True, capture_output=True)
    subprocess.run(git + ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                         'commit', '-qm', 'fixture'], check=True, capture_output=True)
    return subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()


def test_real_git_aggregate_above8mib_has_streamed_expected_hash(tmp_path):
    revision = fixture_repo(tmp_path, padding=8452306)
    hashes, sizes, health = V._expected_files(tmp_path, revision)
    raw = (tmp_path / 'docs/data/articles.json').read_bytes()
    assert 8 * 1024 * 1024 < sizes['data/articles.json'] < 32 * 1024 * 1024
    assert hashes['data/articles.json'] == V.digest(raw) and not health


def test_selected_snapshot_budget_is_checked_before_archive(tmp_path, monkeypatch):
    revision = fixture_repo(tmp_path)
    original = V.subprocess.run
    calls = []

    def run(command, *args, **kwargs):
        calls.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(V.subprocess, 'run', run)
    monkeypatch.setattr(V, 'SNAPSHOT_MAX_BYTES', 128)
    with pytest.raises(V.SnapshotError, match='selected snapshot exceeds 128'):
        V._expected_files(tmp_path, revision)
    assert not any('archive' in command for command in calls)


def test_oversized_git_blob_is_rejected_before_its_body_is_read(tmp_path, monkeypatch):
    calls = []

    def checked(command, **kwargs):
        calls.append(command)
        assert 'cat-file' in command
        return ('a' * 40 + ' blob ' + str(V.AGGREGATE_MAX_BYTES + 1) + '\n').encode()

    monkeypatch.setattr(V.subprocess, 'check_output', checked)
    with pytest.raises(V.SnapshotError, match='data/articles.json: expected .* exceeds'):
        V._expected_files(tmp_path, 'a' * 40)
    assert len(calls) == 1
