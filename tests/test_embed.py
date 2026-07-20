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
    # 冒煙測試：compute_embeddings 全部 article 都 reuse（冇任何要重新
    # embed 嘅），唔應該叫 sentence-transformers model；save 完之後
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
    # compute_embeddings today — this smoke test only exercises the
    # meta/bin plumbing (_load_meta/_load_embeddings), not the full
    # compute_embeddings reuse path, to avoid requiring a real model load.
    meta = E._load_meta(meta_path)
    old_mat = E._load_embeddings(len(meta["ids"]), emb_path, meta.get("bin_hash"))
    assert old_mat.shape == (2, E.EMBED_DIM)
    assert np.allclose(old_mat, mat)
