"""Source-bound title reuse from published metadata; no IO writes or inference."""
import copy
import hashlib
import json
import re
from pathlib import Path

from src.publisher_identity import identity_key


def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def valid_title(value):
    return (isinstance(value, str) and 0 < len(value) <= 1000 and value == value.strip()
            and not any(ord(c) < 32 for c in value) and not re.search(r'[<>]', value)
            and not value.startswith(('{', '[', '```')))


def bind_translation(article, source_title, translated, version):
    """Only a changed, usable translation receives a successful binding.

    The provider helper returns the original on errors. That fallback must not
    become a successful translation cache entry. Existing article data stays.
    """
    article.pop('title_translation', None)
    article['title_translation_status'] = 'original'
    if (not valid_title(source_title) or not valid_title(translated) or translated == source_title
            or not re.search(r'[\u3400-\u9fff\uf900-\ufaff]', translated)):
        return False
    article['title'] = translated
    article['title_translation_status'] = 'translated'
    article['title_translation'] = {
        'version': version, 'identity': identity_key(article), 'source': article.get('source'),
        'source_title': source_title, 'source_hash': text_hash(source_title),
        'translated_hash': text_hash(translated),
    }
    return True


def reuse_translation(article, previous, version):
    """Unknown/changed provenance is a miss, never a hash migration."""
    prior = previous.get((identity_key(article), article.get('source'))) if previous else None
    if not isinstance(prior, dict):
        return False
    binding = prior.get('title_translation')
    raw, translated = article.get('title'), prior.get('title')
    if (not isinstance(binding, dict) or binding.get('version') != version
            or binding.get('identity') != identity_key(article)
            or binding.get('source') != article.get('source')
            or not valid_title(raw) or not valid_title(translated)
            or binding.get('source_title') != raw or binding.get('source_hash') != text_hash(raw)
            or binding.get('translated_hash') != text_hash(translated)
            or not re.search(r'[\u3400-\u9fff\uf900-\ufaff]', translated)):
        return False
    article['title'] = translated
    article['title_translation'] = copy.deepcopy(binding)
    article['title_translation_status'] = 'cached'
    return True


def published_titles(path):
    """Read the existing owned aggregate once; legacy records stay untrusted."""
    path = Path(path)
    try:
        if path.stat().st_size > 32*1024*1024:
            return {}
        rows = json.loads(path.read_text(encoding='utf-8'))['articles']
        result = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get('title_translation'), dict):
                result[(identity_key(row), row.get('source'))] = {
                    'title': row.get('title'), 'title_translation': row['title_translation']}
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}
