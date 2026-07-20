"""Subprocess entry point for src.embed.compute_embeddings.

Runs the actual sentence-transformers model load + encode in a genuinely
killable OS process (invoked as `python -m src.embed_worker`), so a hang
can be terminated with proc.kill() from the parent instead of leaking a
stuck background thread into build.py's process (see src/embed.py's module
docstring for the full reasoning). Thin by design — all real logic stays
in src.embed._compute_embeddings_sync so it's testable without spawning a
subprocess.
"""
import argparse
import json
import sys
from pathlib import Path

from src.embed import _compute_embeddings_sync


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to a JSON array of {id, title, summary}")
    parser.add_argument("--data-dir", required=True, help="Output directory for embeddings.bin/embeddings_meta.json/similar.json")
    args = parser.parse_args()

    articles = json.loads(Path(args.input).read_text(encoding="utf-8"))
    _compute_embeddings_sync(articles, data_dir=args.data_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
