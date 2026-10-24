"""Conservative source-text states; never infer missing article facts."""
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from src.article_evidence import normalise_text, body_text

RTHK_DESCRIPTION = '詳盡報道本地時事、大中華消息、國際局勢等新聞，緊貼最新熱話。'


def text_status(article):
    content = article.get('content') or article.get('rss_content') or ''
    soup = BeautifulSoup(content, 'html.parser')
    text = normalise_text(body_text(content))
    host = (urlsplit(article.get('url') or '').hostname or '').removeprefix('www.')
    if host == 'am730.com.hk' and text.casefold() == 'this press release has been killed':
        return 'withdrawn'
    if host == 'news.rthk.hk' and text == RTHK_DESCRIPTION:
        return 'boilerplate'
    if (article.get('content_quality') or {}).get('fallback') == 'minimal':
        return 'title-only'
    if not text:
        return 'image-only' if soup.find('img') else 'unavailable'
    if text == normalise_text(article.get('title')):
        return 'title-only'
    return 'available'


def classify_content(article):
    status = text_status(article)
    quality = dict(article.get('content_quality') or {})
    if status != 'available':
        quality['text_status'] = status
        quality['completeness'] = 'insufficient'
    elif 'text_status' in quality:
        quality['text_status'] = status
    article['content_quality'] = quality
    return status
