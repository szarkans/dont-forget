#!/usr/bin/env python3
"""Embed missing chunks and search vectors; invoked only in the plugin venv."""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

from common import connect_ro
from semantic import MODEL_ID, MODEL_REVISION, stub_enabled


def stub_vector(text: str) -> list[float]:
    """Deterministic tiny vectors for contract tests, with a known paraphrase."""
    lowered = text.casefold()
    return [float("bird" in lowered or "птиц" in lowered),
            float("crisp" in lowered or "хруст" in lowered),
            float("database" in lowered or "база" in lowered),
            float("other" in lowered)]


class StubEncoder:
    storage_bytes = 4

    def encode_documents(self, texts):
        return [stub_vector(text) for text in texts]

    def encode_query(self, text):
        return stub_vector(text)

    def pack(self, vector):
        return array.array("f", vector).tobytes()

    def unpack(self, blob, dim):
        vector = array.array("f")
        vector.frombytes(blob)
        return list(vector) if len(vector) == dim else None


class ModelEncoder:
    storage_bytes = 2

    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            import numpy as np
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(6)
            # Granite R2 uses native ModernBERT; remote model code is unnecessary.
            self.model = SentenceTransformer(MODEL_ID, device="cpu", revision=MODEL_REVISION,
                                             model_kwargs={"dtype": torch.float32})
            self.model.max_seq_length = 512
            self.np = np
        except Exception as error:
            raise SystemExit("model unavailable: run scripts/setup.py --install-semantic "
                             f"({type(error).__name__})") from None

    def encode_documents(self, texts):
        return self.model.encode_document(texts, batch_size=16, normalize_embeddings=True,
                                          convert_to_numpy=True, show_progress_bar=False)

    def encode_query(self, text):
        return self.model.encode_query(text, normalize_embeddings=True, convert_to_numpy=True)

    def pack(self, vector):
        return self.np.asarray(vector, dtype=self.np.float16).tobytes()

    def unpack(self, blob, dim):
        vector = self.np.frombuffer(blob, dtype=self.np.float16)
        return vector.astype(self.np.float32).tolist() if len(vector) == dim else None


def cosine(query, vector, query_norm):
    """The one scoring path for both the injected test encoder and Granite."""
    vector_norm = math.sqrt(sum(value * value for value in vector))
    return float(sum(float(a) * b for a, b in zip(query, vector)) / (query_norm * vector_norm)) if vector_norm else 0.0


def execute(request: dict, encoder=None) -> dict:
    started = time.perf_counter()
    try:
        index = connect_ro(Path(request["db"]))
    except sqlite3.Error as error:
        if not Path(request["db"]).is_file():
            return {"semantic": "off: no index"}
        return {"semantic": f"off: sqlite: {str(error).splitlines()[0][:100]}"}
    vectors = None
    try:
        vectors = sqlite3.connect(request["vectors_db"], timeout=30)
        vectors.execute("PRAGMA busy_timeout=30000")
        vectors.execute("CREATE TABLE IF NOT EXISTS chunk_vectors (hash TEXT PRIMARY KEY, vector BLOB NOT NULL, dim INTEGER NOT NULL)")
        chunks = [(row[0], row[1], hashlib.sha256(row[1].encode()).hexdigest())
                  for row in index.execute("SELECT id,body FROM chunks")]
        known = {row[0]: (row[1], row[2]) for row in vectors.execute("SELECT hash,vector,dim FROM chunk_vectors")}
        storage_bytes = encoder.storage_bytes if encoder is not None else (4 if stub_enabled() else 2)
        invalid = [digest for digest, (blob, dim) in known.items()
                   if dim <= 0 or len(blob) != dim * storage_bytes]
        with vectors:
            vectors.executemany("DELETE FROM chunk_vectors WHERE hash=?", ((digest,) for digest in invalid))
        for digest in invalid:
            del known[digest]
        missing = {digest: body for _, body, digest in chunks if digest not in known}
        if request["query"] is None and not missing:
            return {"semantic": "ready", "embedded_chunks": len(chunks),
                    "total_chunks": len(chunks), "new_vectors": 0,
                    "seconds": round(time.perf_counter() - started, 3)}

        if encoder is None:
            encoder = StubEncoder() if stub_enabled() else ModelEncoder()
        inserted = 0
        if request["full"] or len(missing) <= request["limit"]:
            pairs = list(missing.items())
            for start in range(0, len(pairs), 256):
                batch = pairs[start:start + 256]
                embeddings = encoder.encode_documents([body for _, body in batch])
                with vectors:
                    cursor = vectors.executemany(
                        "INSERT OR IGNORE INTO chunk_vectors(hash,vector,dim) VALUES(?,?,?)",
                        [(digest, encoder.pack(vector), len(vector))
                         for (digest, _), vector in zip(batch, embeddings)])
                    inserted += cursor.rowcount
            known = {row[0]: (row[1], row[2]) for row in vectors.execute("SELECT hash,vector,dim FROM chunk_vectors")}

        embedded = sum(digest in known for _, _, digest in chunks)
        total = len(chunks)
        status = "ready" if embedded == total else f"partial: {embedded} of {total} chunks"
        result = {"semantic": status, "embedded_chunks": embedded, "total_chunks": total,
                  "new_vectors": inserted, "seconds": round(time.perf_counter() - started, 3)}
        if request["query"] is not None and embedded:
            query = encoder.encode_query(request["query"])
            query_norm = math.sqrt(sum(float(value) ** 2 for value in query)) or 1.0
            scored = []
            for chunk_id, _, digest in chunks:
                if digest not in known:
                    continue
                vector = encoder.unpack(*known[digest])
                if vector is None:
                    continue
                score = cosine(query, vector, query_norm)
                scored.append([chunk_id, round(score, 6)])
            scored.sort(key=lambda row: (-row[1], row[0]))
            # Scope belongs to search.py; keep all ranks so each scope gets its pool.
            result["rankings"] = scored
        return result
    except sqlite3.Error as error:
        return {"semantic": f"off: sqlite: {str(error).splitlines()[0][:100]}"}
    finally:
        if vectors is not None:
            vectors.close()
        index.close()


def main() -> None:
    print(json.dumps(execute(json.load(sys.stdin))))


if __name__ == "__main__":
    main()
