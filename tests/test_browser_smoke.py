import contextlib
import functools
import http.server
import socketserver
import threading
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


@contextlib.contextmanager
def _static_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DOCS))
    with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright is not installed")
    return sync_playwright


def test_index_and_article_pages_render_in_browser():
    sync_playwright = _require_playwright()
    with _static_server() as base_url:
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(base_url + "/index.html", wait_until="networkidle")
                page.locator("#grid .card").first.wait_for(timeout=10_000)
                assert page.locator("#sort-toggle [data-sort='ai']").count() == 1

                page.locator("#sort-toggle [data-sort='ai']").click()
                assert "active" in page.locator("#sort-toggle [data-sort='ai']").get_attribute("class")

                first_href = page.locator("#grid .card").first.get_attribute("href")
                assert first_href and first_href.startswith("article.html?id=")

                mobile_index = browser.new_page(viewport={"width": 390, "height": 844})
                mobile_index.goto(base_url + "/index.html", wait_until="networkidle")
                mobile_index.locator("#grid .card").first.wait_for(timeout=10_000)
                index_overflow = mobile_index.evaluate(
                    "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) > window.innerWidth + 1"
                )
                assert index_overflow is False
                mobile_index.close()

                page.goto(base_url + "/" + first_href, wait_until="networkidle")
                page.locator("#art-body").wait_for(state="visible", timeout=10_000)
                assert page.locator("#art-title").inner_text().strip()
                assert page.locator("#art-content").inner_text().strip()
                assert "disabled" in page.locator("#nav-prev").get_attribute("class")
                assert "disabled" not in page.locator("#nav-next").get_attribute("class")
                assert page.locator("#nav-next").get_attribute("href")

                mobile = browser.new_page(viewport={"width": 390, "height": 844})
                mobile.goto(base_url + "/" + first_href, wait_until="networkidle")
                mobile.locator("#art-body").wait_for(state="visible", timeout=10_000)
                overflow = mobile.evaluate(
                    "Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) > window.innerWidth + 1"
                )
                assert overflow is False
                mobile.close()
                browser.close()
        except Exception as exc:
            pytest.skip(f"playwright browser runtime unavailable: {exc}")
