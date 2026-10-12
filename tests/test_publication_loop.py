"""Deterministic deployment-loop regressions: no live HTTP or paid calls."""
import asyncio
import json
import ssl
import subprocess
from pathlib import Path

import pytest

from tools import verify_publication as V


class Clock:
    def __init__(self):
        self.value = 0
        self.delays = []

    def now(self):
        return self.value

    async def sleep(self, delay):
        self.delays.append(delay)
        self.value += delay


def expected(paths=(V.INDEX, 'article.html', 'data/content/news.json')):
    return {path: V.digest(b'current') for path in paths}


def test_delayed_index_polls_with_backoff_then_checks_remaining_files():
    clock, calls = Clock(), []

    async def fetch(path):
        calls.append(path)
        return b'old' if path == V.INDEX and calls.count(path) < 3 else b'current'

    result = asyncio.run(V.verify(expected(), fetch, clock=clock.now, sleep=clock.sleep))
    assert result['status'] == 'verified' and result['attempts'] == 3
    assert calls[:3] == [V.INDEX] * 3
    assert set(calls[3:]) == {'article.html', 'data/content/news.json'}
    assert clock.delays == [5, 10]


def test_stale_sidecar_cannot_report_verified_and_retries_are_finite():
    clock, calls = Clock(), []

    async def fetch(path):
        calls.append(path)
        return b'old body' if path.endswith('/news.json') else b'current'

    result = asyncio.run(V.verify(expected(), fetch, clock=clock.now, sleep=clock.sleep))
    assert result['status'] == 'unverified' and result['checked_files'] == []
    assert result['attempts'] == V.MAX_ATTEMPTS
    assert 'news.json' in result['reason']
    assert clock.delays == [5, 10, 15, 15, 15]


def test_denial_wins_over_simultaneous_stale_asset_and_stops_polling():
    clock = Clock()

    async def fetch(path):
        if path == 'article.html':
            return b'old'
        if path.endswith('/news.json'):
            raise V.PermanentFailure('sidecar: HTTP 403')
        return b'current'

    result = asyncio.run(V.verify(expected(), fetch, clock=clock.now, sleep=clock.sleep))
    assert result['status'] == 'unverified' and result['attempts'] == 1
    assert result['reason'] == 'sidecar: HTTP 403' and clock.delays == []


def test_overall_deadline_cancels_and_drains_pending_http_tasks():
    cancelled = []

    async def fetch(path):
        if path == V.INDEX:
            return b'current'
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(path)

    result = asyncio.run(V.verify(expected(), fetch, budget=0.02))
    assert result['status'] == 'unverified' and result['attempts'] == 1
    assert sorted(cancelled) == ['article.html', 'data/content/news.json']


def test_parallel_http_count_is_bounded():
    active, peak = 0, 0

    async def fetch(path):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            return b'current'
        finally:
            active -= 1

    paths = (V.INDEX,) + tuple(f'js/{number}.js' for number in range(20))
    result = asyncio.run(V.verify(expected(paths), fetch))
    assert result['status'] == 'verified'
    assert peak == V.CONCURRENCY and active == 0


def test_external_cancellation_propagates_and_drains_without_another_attempt():
    async def scenario():
        started = asyncio.Event()
        active, calls = 0, []

        async def fetch(path):
            nonlocal active
            calls.append(path)
            if path == V.INDEX:
                return b'current'
            active += 1
            if active == 2:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        task = asyncio.create_task(V.verify(expected(), fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0 and calls.count(V.INDEX) == 1 and len(calls) == 3
    asyncio.run(scenario())


@pytest.mark.parametrize('budget', [0, -1, V.BUDGET_SECONDS + 1])
def test_invalid_or_unbounded_poll_budget_is_rejected(budget):
    async def forbidden(path):
        raise AssertionError('request was issued')
    with pytest.raises(ValueError, match='budget'):
        asyncio.run(V.verify(expected(), forbidden, budget=budget))


class Response:
    def __init__(self, status, body):
        self.status, self.body, self.headers = status, body, {}

    async def read(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, status=200, body=b'current'):
        self.status, self.body, self.calls = status, body, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.status, self.body)


def test_live_reader_is_fixed_public_get_with_bounded_timeout_and_cache_key():
    session = Session()
    assert asyncio.run(V.public_bytes(session, V.INDEX, 'a' * 40)) == b'current'
    url, kwargs = session.calls[0]
    assert url == V.SITE + V.INDEX + '?publication_sha=' + 'a' * 40
    assert kwargs['timeout'].total == V.REQUEST_SECONDS and not kwargs['allow_redirects']
    assert kwargs['headers']['User-Agent'].startswith('rss-news/publication-check')


@pytest.mark.parametrize('status', [401, 403, 406, 429, 451])
def test_public_denial_is_permanent_without_response_body_logging(status):
    session = Session(status, b'private challenge body')
    with pytest.raises(V.PermanentFailure) as caught:
        asyncio.run(V.public_bytes(session, V.INDEX, 'a' * 40))
    assert str(status) in str(caught.value) and 'private challenge' not in str(caught.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize('status', [404, 503])
def test_deployment_delay_or_transient_status_can_be_polled(status):
    with pytest.raises(ValueError, match=str(status)) as caught:
        asyncio.run(V.public_bytes(Session(status), V.INDEX, 'a' * 40))
    assert not isinstance(caught.value, V.PermanentFailure)


def test_tls_failure_is_permanent_without_relaxing_verification():
    class TLSFailure(Session):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise ssl.SSLCertVerificationError('invalid certificate')
    session = TLSFailure()
    with pytest.raises(V.PermanentFailure, match='SSLCertVerificationError'):
        asyncio.run(V.public_bytes(session, V.INDEX, 'a' * 40))
    assert len(session.calls) == 1


def test_private_redirect_is_rejected_without_contacting_target():
    class Redirect(Session):
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            response = Response(302, b'')
            response.headers = {'Location': 'http://127.0.0.1/private'}
            return response
    session = Redirect()
    with pytest.raises(V.PermanentFailure, match='UnsafeDestination'):
        asyncio.run(V.public_bytes(session, V.INDEX, 'a' * 40))
    assert len(session.calls) == 1


def test_response_size_guard_is_in_the_live_reader(monkeypatch):
    async def oversized(*args):
        raise V.UnsafeDestination('publisher response exceeds size limit')
    monkeypatch.setattr(V, 'read_public_body', oversized)
    with pytest.raises(V.PermanentFailure, match='UnsafeDestination'):
        asyncio.run(V.public_bytes(Session(), V.INDEX, 'a' * 40))


@pytest.mark.parametrize('autocrlf', ['false', 'true'])
def test_expected_hashes_and_samples_come_from_commit_not_dirty_worktree(tmp_path, autocrlf):
    articles = [{'id': 'news1', 'summary_evidence': [{'quote': '公開'}], 'content_quality': {'fallback': 'none'}},
                {'id': 'news2', 'content_quality': {'fallback': 'none'}},
                {'id': 'news3', 'content_quality': {'fallback': 'none'}},
                {'id': 'news4', 'evidence_status': 'partial', 'content_quality': {'fallback': 'rss-blocked'}}] + [
                {'id': f'news{i}'} for i in range(5, 12)]
    payload = {'schema_version': 2, 'articles': articles,
               'sources': {'法庭線': {'fetch_status': 'success', 'fresh_count': 5}}}
    files = {path: b'committed\nsecond line\n' for path in V.CORE_PATHS}
    files['data/articles.json'] = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    for article in articles:
        files['data/content/' + article['id'] + '.json'] = b'public body'
    for path, raw in files.items():
        target = tmp_path / 'docs' / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    git = V._git(tmp_path)
    subprocess.run(git + ['init', '-q'], check=True, capture_output=True)
    subprocess.run(git + ['add', 'docs'], check=True, capture_output=True)
    subprocess.run(git + ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                         'commit', '-qm', 'fixture'], check=True, capture_output=True)
    revision = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True, encoding='utf-8').strip()
    subprocess.run(git + ['config', 'core.autocrlf', autocrlf], check=True, capture_output=True)
    subprocess.run(git + ['config', 'core.eol', 'crlf'], check=True, capture_output=True)
    (tmp_path / 'docs/article.html').write_bytes(b'uncommitted')
    hashes, health = V.expected_snapshot(tmp_path, revision)
    assert hashes['article.html'] == V.digest(b'committed\nsecond line\n')
    assert subprocess.check_output(git + ['config', 'core.autocrlf'], text=True).strip() == autocrlf
    assert subprocess.check_output(git + ['config', 'core.eol'], text=True).strip() == 'crlf'
    assert {path for path in hashes if path.startswith('data/content/')} == {
        'data/content/news1.json', 'data/content/news2.json', 'data/content/news3.json', 'data/content/news4.json'}
    assert health['法庭線']['fresh_count'] == 5


@pytest.mark.parametrize('revision', ['HEAD', '--all', 'a' * 39, 'a' * 41, 'a' * 40 + '^'])
def test_revision_options_or_symbolic_refs_are_rejected_before_git(tmp_path, revision):
    with pytest.raises(ValueError, match='full lowercase commit SHA'):
        V.expected_snapshot(tmp_path, revision)


def test_unsafe_article_id_never_becomes_a_git_or_remote_path(tmp_path, monkeypatch):
    calls = []

    def git(*args, **kwargs):
        calls.append(args[0])
        return b'{"schema_version":2,"articles":[{"id":"../../.env"}]}'
    monkeypatch.setattr(V.subprocess, 'check_output', git)
    with pytest.raises(ValueError, match='safe article identities'):
        V.expected_snapshot(tmp_path, 'a' * 40)
    assert len(calls) == 1 and calls[0][-1].endswith(':docs/data/articles.json')


def test_workflow_verification_precedes_heartbeat_and_preserves_existing_schedule():
    text = (Path(__file__).resolve().parents[1] / '.github/workflows/update.yml').read_text(encoding='utf-8')
    assert text.index('- name: Commit & push if changed') < text.index('- name: Verify public Pages publication') < text.index('- name: Daily heartbeat')
    verify = text.split('- name: Verify public Pages publication', 1)[1].split('- name:', 1)[0]
    assert 'steps.publication.outputs.revision' in verify and 'python tools/verify_publication.py' in verify
    assert "MINIMAX_API_KEY: ''" in verify and "TELEGRAM_BOT_TOKEN: ''" in verify
    assert 'build.py' not in verify and 'workflow run' not in verify
    assert 'cron: "*/20 * * * *"' in text and 'cancel-in-progress: false' in text
    assert 'timeout-minutes: 25' in text


def test_cli_writes_utf8_report_and_summary_without_publishing(tmp_path, monkeypatch):
    out, summary = tmp_path / 'result.json', tmp_path / 'summary.md'

    async def checked(repo, revision):
        return {'status': 'verified', 'revision': revision, 'attempts': 1,
                'checked_files': [V.INDEX], 'source_health': {'法庭線': {'fresh_count': 5}}}
    monkeypatch.setattr(V, 'verify_publication', checked)
    monkeypatch.setattr(V.sys, 'argv', ['verify_publication', '--repo', str(tmp_path), '--revision', 'a' * 40, '--result', str(out)])
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    assert V.main() == 0
    assert '法庭線' in out.read_text(encoding='utf-8')
    assert '**verified**' in summary.read_text(encoding='utf-8')


def test_cli_failure_remains_unverified_and_does_not_dump_exception_body(tmp_path, monkeypatch):
    out = tmp_path / 'result.json'

    async def failed(*args):
        raise ValueError('private body must not be logged')
    monkeypatch.setattr(V, 'verify_publication', failed)
    monkeypatch.setattr(V.sys, 'argv', ['verify_publication', '--repo', str(tmp_path), '--revision', 'a' * 40, '--result', str(out)])
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    assert V.main() == 1
    raw = out.read_text(encoding='utf-8')
    assert '"status": "unverified"' in raw and 'private body' not in raw
