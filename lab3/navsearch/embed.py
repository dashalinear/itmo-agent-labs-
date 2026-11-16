"""Embeddings for L1 and L2.

Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (384 dims, Russian + English) through
fastembed (ONNX Runtime, CPU, no torch). The model is downloaded once into lab3/.cache/fastembed
(EMBED_CACHE_DIR) and loaded once per process. Vectors are L2-normalized; Elasticsearch uses cosine.

HashEmbedder is a deterministic stand-in for offline unit tests only (no semantics).
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
from pathlib import Path

from . import LAB3_ROOT

MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
DIMS = 384
_lock = threading.Lock()
_model = None


class Embedder:
    name = MODEL
    dims = DIMS

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = Path(os.getenv("EMBED_CACHE_DIR", str(cache_dir or LAB3_ROOT / ".cache" / "fastembed")))

    def _load(self):
        global _model
        with _lock:
            if _model is None:
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                from fastembed import TextEmbedding
                _model = TextEmbedding(MODEL, cache_dir=str(self.cache_dir))
            return _model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out = []
        for v in self._load().embed([t[:4000] for t in texts], batch_size=16):
            n = math.sqrt(float((v * v).sum())) or 1.0
            out.append([float(x) / n for x in v])
        return out


class HashEmbedder:
    """Offline tests only: deterministic pseudo-vectors from word hashes (bag of words)."""
    name = "hash-test-embedder"
    dims = DIMS

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for w in t.lower().split():
                h = int(hashlib.md5(w[:5].encode("utf-8")).hexdigest(), 16)
                v[h % self.dims] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out
