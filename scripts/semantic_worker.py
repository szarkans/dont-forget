#!/usr/bin/env python3
"""Embed missing chunks and search vectors; invoked only in the plugin venv."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import sys
import time

MODEL = "ibm-granite/granite-embedding-311m-multilingual-r2"
STUB = os.environ.get("DONT_FORGET_EMBED_STUB") == "1"


def stub_vector(text: str) -> list[float]:
    """Deterministic tiny vectors for contract tests, with a known paraphrase."""
    lowered = text.casefold()
    return [float("bird" in lowered or "птиц" in lowered),
            float("crisp" in lowered or "хруст" in lowered),
            float("database" in lowered or "база" in lowered),
            float("other" in lowered)]


def main() -> None:
    request = json.load(sys.stdin)
    started = time.perf_counter()
    con = sqlite3.connect(request["db"], timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("CREATE TABLE IF NOT EXISTS chunk_vectors (hash TEXT PRIMARY KEY, vector BLOB NOT NULL, dim INTEGER NOT NULL)")
    chunks = [(row[0], row[1], row[2], hashlib.sha256(row[2].encode()).hexdigest()) for row in
              con.execute("SELECT c.id,n.type,c.body FROM chunks c JOIN notes n ON n.id=c.note_id")]
    known = {row[0]: (row[1], row[2]) for row in con.execute("SELECT hash,vector,dim FROM chunk_vectors")}
    missing = {}
    for _, _, body, digest in chunks:
        if digest not in known:
            missing[digest] = body
    # Health on an unchanged index is a true no-op: avoid even loading the model.
    if request["query"] is None and not missing:
        print(json.dumps({"semantic": "ready", "embedded_chunks": len(chunks),
                          "total_chunks": len(chunks), "new_vectors": 0,
                          "seconds": round(time.perf_counter() - started, 3)}))
        con.close()
        return
    if STUB:
        import array

        def encode_documents(texts):
            return [stub_vector(text) for text in texts]

        def encode_query(text):
            return stub_vector(text)

        def pack(vector):
            return array.array("f", vector).tobytes()

        def unpack(blob, dim):
            vec = array.array("f")
            vec.frombytes(blob)
            return list(vec) if len(vec) == dim else None
    else:
        try:
            import numpy as np
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(6)
            model = SentenceTransformer(MODEL, device="cpu", trust_remote_code=True,
                                        model_kwargs={"dtype": torch.float32})
            model.max_seq_length = 512
        except Exception as error:
            raise SystemExit(f"model unavailable: {type(error).__name__}: {error}") from None

        def encode_documents(texts):
            return model.encode_document(texts, batch_size=16, normalize_embeddings=True,
                                         convert_to_numpy=True, show_progress_bar=False)

        def encode_query(text):
            return model.encode_query(text, normalize_embeddings=True, convert_to_numpy=True)

        def pack(vector):
            return np.asarray(vector, dtype=np.float16).tobytes()

        def unpack(blob, dim):
            vec = np.frombuffer(blob, dtype=np.float16)
            return vec.astype(np.float32) if len(vec) == dim else None

    if request["full"] or len(missing) <= request["limit"]:
        pairs = list(missing.items())
        for start in range(0, len(pairs), 256):
            batch = pairs[start:start + 256]
            vectors = encode_documents([body for _, body in batch])
            with con:
                con.executemany("INSERT OR IGNORE INTO chunk_vectors(hash,vector,dim) VALUES(?,?,?)",
                                [(digest, pack(vec), len(vec)) for (digest, _), vec in zip(batch, vectors)])
        known = {row[0]: (row[1], row[2]) for row in con.execute("SELECT hash,vector,dim FROM chunk_vectors")}
    embedded = sum(digest in known for _, _, _, digest in chunks)
    total = len(chunks)
    status = "ready" if embedded == total else f"partial: {embedded} of {total} chunks"
    result = {"semantic": status, "embedded_chunks": embedded, "total_chunks": total,
              "new_vectors": len(missing) if status == "ready" or request["full"] else 0,
              "seconds": round(time.perf_counter() - started, 3)}
    if request["query"] is not None and embedded:
        query = encode_query(request["query"])
        if STUB:
            norm = math.sqrt(sum(x * x for x in query)) or 1
            scored = []
            for chunk_id, note_type, _, digest in chunks:
                if digest not in known:
                    continue
                vector = unpack(*known[digest])
                if vector is None:
                    continue
                vnorm = math.sqrt(sum(x * x for x in vector)) or 1
                score = sum(a * b for a, b in zip(query, vector)) / norm / vnorm
                scored.append((chunk_id, note_type, round(score, 6)))
        else:
            valid = [(chunk_id, note_type, unpack(*known[digest])) for chunk_id, note_type, _, digest in chunks
                     if digest in known]
            valid = [(chunk_id, note_type, vec) for chunk_id, note_type, vec in valid if vec is not None]
            if valid:
                matrix = np.stack([vec for _, _, vec in valid])
                q = np.asarray(query, dtype=np.float32)
                scores = (matrix @ q) / (np.linalg.norm(matrix, axis=1) * (np.linalg.norm(q) or 1) + 1e-12)
                scored = [(chunk_id, note_type, round(float(score), 6))
                          for (chunk_id, note_type, _), score in zip(valid, scores)]
            else:
                scored = []
        scored.sort(key=lambda row: (-row[2], row[0]))
        result["rankings"] = {
            "all": [[chunk_id, score] for chunk_id, _, score in scored[:500]],
            "own": [[chunk_id, score] for chunk_id, kind, score in scored
                    if (kind or "").strip().casefold() != "lead"][:500],
            "leads": [[chunk_id, score] for chunk_id, kind, score in scored
                      if (kind or "").strip().casefold() == "lead"][:500],
        }
        result["best_cosine"] = {scope: rows[0][1] if rows else None
                                 for scope, rows in result["rankings"].items()}
    print(json.dumps(result))
    con.close()


if __name__ == "__main__":
    main()
