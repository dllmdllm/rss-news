import pytest
from bs4 import BeautifulSoup

from src.rss_html import first_rss_image, safe_http_url, sanitize_rss_html


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:image/svg+xml,<svg/>', 'file:///etc/passwd', 'blob:https://example.com/a', 'ftp://example.com/a', 'https://user:pass@example.com/a', 'https://127.0.0.1/a', 'http://127.1/a', 'http://0x7f000001/a', 'http://[::1]/a', 'http://localhost/a', 'http://foo.local/a', 'https://example.com:8080/a', 'https:\\\\example.com/a', 'java\nscript:alert(1)'])
def test_unsafe_urls_are_rejected(url):
    assert safe_http_url(url, 'https://example.com/news/story') is None


def test_relative_unicode_image_url_is_encoded_and_resolved():
    assert safe_http_url('../圖片/photo one.jpg#fragment', 'https://example.com/news/story') == 'https://example.com/%E5%9C%96%E7%89%87/photo%20one.jpg'
    assert safe_http_url('//cdn.example.com/p.jpg', 'https://example.com/article') == 'https://cdn.example.com/p.jpg'


def test_rss_active_html_is_removed_but_text_images_and_captions_survive():
    raw = '''<script>window.stolen=1</script><style>@import "x"</style><svg><a href="x">bad svg</a></svg><math>bad math</math><iframe src="x"></iframe><form>bad form</form>
    <p onclick="bad()">News <strong style="background:url(x)">bold</strong><a href="javascript:bad()">link</a></p>
    <figure><img src="data:image/png;base64,xxx" data-src="/img/photo.jpg" onerror="bad()" srcset="javascript:bad() 1x"><figcaption>Photo caption</figcaption></figure>
    <a href="/original" ping="https://tracker.example.com" style="x">Read</a>'''
    soup = BeautifulSoup(sanitize_rss_html(raw, 'https://example.com/news/story'), 'html.parser')
    assert not soup.find(['script', 'style', 'svg', 'math', 'iframe', 'form'])
    assert soup.img['src'] == 'https://example.com/img/photo.jpg'
    assert soup.figcaption.get_text() == 'Photo caption'
    assert soup.strong.get_text() == 'bold'
    assert not soup.find('a', string='link').has_attr('href')
    assert soup.find('a', string='Read')['href'] == 'https://example.com/original'
    assert all(not any(k.startswith('on') or k in {'style', 'srcset', 'ping'} for k in tag.attrs) for tag in soup.find_all(True))


def test_lazy_srcset_duplicates_emoji_and_tracking_pixels():
    raw = '''<img data-srcset="/a.jpg 480w, /b.jpg 960w"><img src="/a.jpg"><img src="/pixel.png" width="1"><img class="wp-smiley" src="https://s.w.org/emoji.png" alt="🔸">'''
    soup = BeautifulSoup(sanitize_rss_html(raw, 'https://example.com/article'), 'html.parser')
    assert [img['src'] for img in soup.find_all('img')] == ['https://example.com/a.jpg']
    assert '🔸' in soup.get_text()
    assert first_rss_image(raw, 'https://example.com/article') == 'https://example.com/a.jpg'


def test_unsafe_lazy_source_falls_back_to_safe_src():
    assert first_rss_image('<img data-src="javascript:bad()" src="/photo.jpg">', 'https://example.com/a') == 'https://example.com/photo.jpg'
