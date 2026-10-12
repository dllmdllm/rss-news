import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path

import pytest

from tools import source_response_probe as P


def test_response_classification_never_retains_publisher_text():
    raw = b'<html><script>challenge-platform</script>PRIVATE_BODY_SENTINEL</html>'
    report = P.classify(raw)
    assert report['html_markup'] and not report['valid_json']
    assert report['markers'] == ['cloudflare-challenge']
    assert 'PRIVATE_BODY_SENTINEL' not in json.dumps(report)
    assert P.classify(b'[]')['json_items'] == 0
    assert not P.classify(b'\x00\xff')['valid_json']


def test_url_metadata_omits_queries_and_rejects_private_destinations():
    assert P.safe_url('https://hkcourtnews.com/wp-json/wp/v2/posts?secret=PRIVATE') == 'https://hkcourtnews.com/wp-json/wp/v2/posts'
    with pytest.raises(ValueError):
        P.safe_url('http://127.0.0.1/private')


@pytest.mark.parametrize('status', [200, 403])
def test_probe_has_one_guarded_request_and_denial_body_is_not_read(monkeypatch, status):
    calls = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    @asynccontextmanager
    async def get(session, url, **kwargs):
        calls.append((url, kwargs))
        response = type('Response', (), {'status': status, 'headers': {'Content-Type': 'text/html; charset=utf-8'}, 'url': url})()
        yield response

    async def read(response):
        assert status == 200, 'denial body must remain unread'
        return b'<html>PRIVATE_BODY_SENTINEL</html>'

    settings = []
    monkeypatch.setattr(P.aiohttp, 'ClientSession', lambda **kwargs: settings.append(kwargs) or Session())
    monkeypatch.setattr(P, 'public_connector', lambda **kwargs: None)
    monkeypatch.setattr(P, 'public_get', get)
    monkeypatch.setattr(P, 'read_public_body', read)
    feed = next(row for row in P.RSS_FEEDS if row['name'] == '法庭線')
    result = asyncio.run(P.probe(feed))
    assert len(calls) == 1 and calls[0][1]['timeout'].total == 15
    assert calls[0][1]['headers']['User-Agent'] == feed['headers']['User-Agent']
    assert calls[0][1]['headers']['Accept'] == 'application/json'
    assert 'context=view' in calls[0][0] and 'status=publish' in calls[0][0]
    assert isinstance(settings[0]['cookie_jar'], P.aiohttp.DummyCookieJar)
    assert result['status'] == status and 'PRIVATE_BODY_SENTINEL' not in json.dumps(result)
    assert ('bytes' in result) == (status == 200)


def test_diagnostic_workflow_cannot_run_paid_build_or_publish():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/source-response-probe.yml').read_text(encoding='utf-8')
    assert 'workflow_dispatch:' in workflow and 'schedule:' not in workflow
    assert 'contents: read' in workflow and 'persist-credentials: false' in workflow
    assert 'runs-on: [self-hosted, Windows, X64]' in workflow
    assert 'timeout-minutes: 3' in workflow
    assert 'secrets.' not in workflow and 'build.py' not in workflow and 'git push' not in workflow
    assert "MINIMAX_API_KEY: ''" in workflow and 'retention-days: 3' in workflow
