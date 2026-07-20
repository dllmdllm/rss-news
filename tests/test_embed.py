from pathlib import Path

import numpy as np

from src import embed as E


def test_save_and_load_embeddings_roundtrip(tmp_path):
    mat = np.random.rand(3, E.EMBED_DIM).astype(np.float32)
    emb_path = tmp_path / "embeddings.bin"
    bin_hash = E._save_embeddings(mat, tmp_path, emb_path)

    loaded = E._load_embeddings(3, emb_path, bin_hash)
    assert loaded.shape == (3, E.EMBED_DIM)
    assert np.allclose(loaded, mat)


def test_load_embeddings_rejects_stale_bin_when_hash_mismatches(tmp_path):
    # 2026-07-21 audit finding：embeddings.bin 同 embeddings_meta.json 各自
    # atomic write，但兩個一齊唔係 atomic pair——如果 count 啱啱好一樣，
    # 純粹 byte-size check 會將錯配嘅 bin/meta 當有效，靜靜哋派錯
    # vector 俾錯嘅 article id。有 bin_hash 就一定要對得上先信呢個 cache。
    mat = np.random.rand(3, E.EMBED_DIM).astype(np.float32)
    emb_path = tmp_path / "embeddings.bin"
    E._save_embeddings(mat, tmp_path, emb_path)

    # Meta 話個 hash 係第二嚿嘢（模擬 meta 冧咗但 bin 已經更新過嘅情況）。
    loaded = E._load_embeddings(3, emb_path, "0000000000000000")
    assert loaded.shape == (0, E.EMBED_DIM)


def test_load_embeddings_without_expected_hash_falls_back_to_size_check(tmp_path):
    # 冇 bin_hash（例如舊 meta.json，遷移前嘅格式）就照用返 byte-size
    # check，唔應該因為新加咗嘅參數令舊 state 完全用唔到。
    mat = np.random.rand(2, E.EMBED_DIM).astype(np.float32)
    emb_path = tmp_path / "embeddings.bin"
    E._save_embeddings(mat, tmp_path, emb_path)

    loaded = E._load_embeddings(2, emb_path, None)
    assert loaded.shape == (2, E.EMBED_DIM)


def test_load_embeddings_missing_file_returns_empty(tmp_path):
    loaded = E._load_embeddings(5, tmp_path / "missing.bin", "anyhash")
    assert loaded.shape == (0, E.EMBED_DIM)


def test_save_meta_includes_bin_hash_for_reuse_on_next_load(tmp_path, monkeypatch):
    # 冒煙測試：_compute_embeddings_sync 全部 article 都 reuse（冇任何要
    # 重新 embed 嘅），唔應該叫 sentence-transformers model；save 完之後
    # bin_hash 應該入咗 meta。
    articles = [
        {"id": "a1", "title": "標題一", "summary": "摘要一"},
        {"id": "a2", "title": "標題二", "summary": "摘要二"},
    ]
    data_dir = tmp_path
    emb_path = data_dir / "embeddings.bin"
    meta_path = data_dir / "embeddings_meta.json"

    mat = np.random.rand(2, E.EMBED_DIM).astype(np.float32)
    bin_hash = E._save_embeddings(mat, data_dir, emb_path)
    hashes = {a["id"]: E._article_hash(a) for a in articles}
    E._save_meta(
        {"ids": ["a1", "a2"], "hashes": hashes, "dim": E.EMBED_DIM, "count": 2,
         "updated": "x", "bin_hash": bin_hash},
        data_dir, meta_path,
    )

    # sentence_transformers import happens unconditionally near the top of
    # _compute_embeddings_sync today — this smoke test only exercises the
    # meta/bin plumbing (_load_meta/_load_embeddings), not the full reuse
    # path, to avoid requiring a real model load.
    meta = E._load_meta(meta_path)
    old_mat = E._load_embeddings(len(meta["ids"]), emb_path, meta.get("bin_hash"))
    assert old_mat.shape == (2, E.EMBED_DIM)
    assert np.allclose(old_mat, mat)


# ── compute_embeddings() subprocess orchestration ──────────────────────
# 2026-07-21 audit finding: 之前用 loop.run_in_executor 跑呢個計算，
# asyncio.wait_for 逾時淨係停止等待，唔會真正停止個 thread（Python 冇
# API 可以殺 thread）。而家改用真正嘅 subprocess，逾時可以真.kill 咗佢。
# 呢啲 test 用 fake asyncio.create_subprocess_exec，唔需要真係起
# sentence-transformers/subprocess。

class _FakeProc:
    def __init__(self, communicate_result=(b"", b""), returncode=0, hang=False):
        self._result = communicate_result
        self.returncode = returncode
        self._hang = hang
        self.killed = False
        self.waited = False

    async def communicate(self):
        if self._hang:
            import asyncio
            await asyncio.sleep(10)
        return self._result

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return self.returncode


def test_compute_embeddings_noop_when_articles_empty(monkeypatch, tmp_path):
    import asyncio

    async def fail_exec(*a, **kw):
        raise AssertionError("should not spawn a subprocess for empty articles")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_exec)

    asyncio.run(E.compute_embeddings([], data_dir=tmp_path))


def test_compute_embeddings_spawns_worker_with_correct_args(monkeypatch, tmp_path):
    import asyncio

    calls = {}
    fake_proc = _FakeProc(communicate_result=(b"[embed] ok\n", b""))

    async def fake_exec(*args, **kwargs):
        calls["args"] = args
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    articles = [{"id": "a1", "title": "t", "summary": "s"}]
    asyncio.run(E.compute_embeddings(articles, data_dir=tmp_path))

    assert not fake_proc.killed
    args = calls["args"]
    assert "-m" in args and "src.embed_worker" in args
    assert "--data-dir" in args
    assert str(tmp_path) in args
    assert "--input" in args
    # temp input file must be cleaned up after the call
    input_idx = args.index("--input") + 1
    assert not Path(args[input_idx]).exists()


def test_compute_embeddings_kills_subprocess_on_timeout(monkeypatch, tmp_path):
    import asyncio

    fake_proc = _FakeProc(hang=True)

    async def fake_exec(*args, **kwargs):
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    articles = [{"id": "a1", "title": "t", "summary": "s"}]
    asyncio.run(E.compute_embeddings(articles, data_dir=tmp_path, timeout=0.05))

    assert fake_proc.killed
    assert fake_proc.waited


def test_compute_embeddings_writes_minimal_input_fields(monkeypatch, tmp_path):
    import asyncio
    import json

    written = {}
    fake_proc = _FakeProc()

    async def fake_exec(*args, **kwargs):
        input_idx = args.index("--input") + 1
        written["payload"] = json.loads(Path(args[input_idx]).read_text(encoding="utf-8"))
        return fake_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    articles = [
        {"id": "a1", "title": "標題", "summary": "摘要", "content": "呢個唔應該傳過去worker"},
        {"id": None, "title": "冇id應該skip"},
    ]
    asyncio.run(E.compute_embeddings(articles, data_dir=tmp_path))

    assert written["payload"] == [{"id": "a1", "title": "標題", "summary": "摘要"}]
