"""Offline all-record media gate. No networking, provider calls or file writes."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.media_qa import inventory


def check(docs):
    data = Path(docs) / 'data'
    articles = json.loads((data / 'articles.json').read_text(encoding='utf-8'))['articles']
    records = []
    for article in articles:
        sidecar = json.loads((data / 'content' / (article['id'] + '.json')).read_text(encoding='utf-8'))
        records.append(dict(article, content=sidecar['content']))
    return inventory(records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('docs', nargs='?', default='docs')
    args = parser.parse_args()
    print(json.dumps(check(args.docs), ensure_ascii=False))
