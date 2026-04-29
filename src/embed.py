"""Article embedding and similar-article computation.

Produces two artefacts in docs/data/:
  embeddings.bin      — raw Float32 array, shape (n, 384), row-major
  embeddings_meta.json — {ids: [...], dim: 384, count: n, updated: "..."}
  similar.json        — {article_id: [top_k_similar_ids], ...}

Uses paraphrase-multilingual-MiniLM-L12-v2 (384-dim, ~120 MB on first run,
then cached by sentence-transformers in ~/.cache/huggingface).  The same
model is available as Xenova/paraphrase-multilingual-MiniLM-L12-v2 for
transformers.js, so build-time and browser-time embeddings are compatible.
"""
import hashlib
import json
import os
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

DATA_DIR  = Path(__file__).parent.parent / "docs" / "data"
EMB_PATH  = DATA_DIR / "embeddings.bin"
META_PATH = DATA_DIR / "embeddings_meta.json"
SIM_PATH  = DATA_DIR / "similar.json"

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBED_DIM  = 384
TOP_K      = 5
SIM_MIN    = 0.45   # min cosine similarity to count as "similar"
BATCH_SIZE = 64


def _article_text(a: dict) -> str:
    title   = (a.get("title")   or "").strip()
    summary = (a.get("summary") or "").replace("\n", " ").strip()
    return f"{title} {summary}".strip()


def _article_hash(a: dict) -> str:
    return hashlib.md5(_article_text(a).encode("utf-8")).hexdigest()[:12]


def _load_meta() -> dict:
    if META_PATH.exists():
        try:
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ids": [], "hashes": {}, "dim": EMBED_DIM, "count": 0}


def _save_meta(meta: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = META_PATH.with_suffix(META_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, META_PATH)


def _load_embeddings(count: int) -> np.ndarray:
    if EMB_PATH.exists():
        try:
            arr = np.frombuffer(EMB_PATH.read_bytes(), dtype=np.float32)
            if arr.size == count * EMBED_DIM:
                return arr.reshape(count, EMBED_DIM)
        except Exception:
            pass
    return np.empty((0, EMBED_DIM), dtype=np.float32)


def _save_embeddings(mat: np.ndarray):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EMB_PATH.with_suffix(EMB_PATH.suffix + ".tmp")
    tmp.write_bytes(mat.astype(np.float32).tobytes())
    os.replace(tmp, EMB_PATH)


def _save_similar(similar: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SIM_PATH.with_suffix(SIM_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(similar, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, SIM_PATH)


def compute_embeddings(articles: list) -> None:
    """Embed all articles, cache incrementally, write similar.json."""
    if not articles:
        return

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("[embed] sentence-transformers not installed — skipping embeddings")
        return

    meta      = _load_meta()
    old_ids   = meta.get("ids") or []
    old_count = len(old_ids)
    old_mat   = _load_embeddings(old_count)
    old_hash  = meta.get("hashes") or {}

    # Keep existing embeddings for articles whose text hasn't changed.
    keep_ids: list[str] = []
    keep_mat: list[np.ndarray] = []
    for i, aid in enumerate(old_ids):
        if i < old_mat.shape[0]:
            keep_ids.append(aid)
            keep_mat.append(old_mat[i])

    # Build new article set, skipping rows we can reuse.
    current_ids = [a["id"] for a in articles]
    keep_set    = {aid: vec for aid, vec in zip(keep_ids, keep_mat)}

    to_embed: list[dict] = []
    reused_ids:   list[str]        = []
    reused_vecs:  list[np.ndarray] = []
    new_ids:      list[str]        = []

    for a in articles:
        aid  = a["id"]
        h    = _article_hash(a)
        if aid in keep_set and old_hash.get(aid) == h:
            reused_ids.append(aid)
            reused_vecs.append(keep_set[aid])
        else:
            to_embed.append(a)
            new_ids.append(aid)

    print(f"[embed] {len(reused_ids)} reused, {len(to_embed)} to embed")

    new_vecs: list[np.ndarray] = []
    if to_embed:
        model = SentenceTransformer(MODEL_NAME)
        texts = [_article_text(a) for a in to_embed]
        raw   = model.encode(
            texts,
            batch_size=BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        new_vecs = [raw[i] for i in range(len(to_embed))]

    # Rebuild in article order (matches current_ids).
    id_to_vec: dict[str, np.ndarray] = {}
    for aid, v in zip(reused_ids, reused_vecs):
        id_to_vec[aid] = v
    for aid, v in zip(new_ids, new_vecs):
        id_to_vec[aid] = v

    ordered_ids: list[str]        = []
    ordered_vecs: list[np.ndarray] = []
    for a in articles:
        aid = a["id"]
        if aid in id_to_vec:
            ordered_ids.append(aid)
            ordered_vecs.append(id_to_vec[aid])

    if not ordered_vecs:
        return

    mat = np.stack(ordered_vecs, axis=0)   # (n, 384), already L2-normalised
    _save_embeddings(mat)

    new_hash = {a["id"]: _article_hash(a) for a in articles}
    updated  = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M HKT")
    _save_meta({"ids": ordered_ids, "hashes": new_hash,
                "dim": EMBED_DIM, "count": len(ordered_ids), "updated": updated})

    # Compute top-K similar for each article (cosine sim = dot product, normalised).
    sim_matrix = mat @ mat.T          # (n, n)
    similar: dict[str, list[str]] = {}
    n = len(ordered_ids)
    for i in range(n):
        row = sim_matrix[i].copy()
        row[i] = -1.0                  # exclude self
        top_idx = np.argsort(row)[::-1][:TOP_K]
        similar[ordered_ids[i]] = [
            ordered_ids[j] for j in top_idx if row[j] >= SIM_MIN
        ]
    _save_similar(similar)

    kb_emb  = EMB_PATH.stat().st_size  // 1024
    kb_sim  = SIM_PATH.stat().st_size  // 1024
    kb_meta = META_PATH.stat().st_size // 1024
    print(f"[embed] embeddings.bin {kb_emb} KB, similar.json {kb_sim} KB, "
          f"meta {kb_meta} KB ({n} articles)")
