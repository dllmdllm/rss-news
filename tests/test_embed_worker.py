import json
import sys

from src import embed_worker


def test_main_reads_input_and_calls_compute_embeddings_sync(monkeypatch, tmp_path):
    input_path = tmp_path / "input.json"
    articles = [{"id": "a1", "title": "t", "summary": "s"}]
    input_path.write_text(json.dumps(articles), encoding="utf-8")

    calls = {}

    def fake_sync(articles_arg, data_dir=None):
        calls["articles"] = articles_arg
        calls["data_dir"] = data_dir

    monkeypatch.setattr(embed_worker, "_compute_embeddings_sync", fake_sync)
    monkeypatch.setattr(sys, "argv", [
        "embed_worker.py", "--input", str(input_path), "--data-dir", str(tmp_path),
    ])

    rc = embed_worker.main()

    assert rc == 0
    assert calls["articles"] == articles
    assert calls["data_dir"] == str(tmp_path)
