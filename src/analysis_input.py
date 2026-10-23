"""Pure definition of the text and provenance actually supplied for analysis."""
import hashlib
import json

from src.article_evidence import body_text, normalise_text

MAX_PROMPT_CHARS = 2000


def source_text(article):
    for key in ('content', 'rss_content'):
        if article.get(key):
            return normalise_text(body_text(article[key]))[:MAX_PROMPT_CHARS]
    return article.get('title', '')


def input_hash(article, version):
    payload = {'title': article.get('title', ''), 'text': source_text(article),
               'published': article.get('date', ''), 'source': article.get('source', ''),
               'url': article.get('url', ''), 'version': version}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
