#!/usr/bin/env python3
"""Embed missing chunks and search vectors; invoked only in the plugin venv."""

from __future__ import annotations

import argparse
import array
import fcntl
import hashlib
import json
import math
import os
import signal
import socket
import sqlite3
import sys
import time
from pathlib import Path

from common import connect_ro
from semantic import MODEL_ID, MODEL_REVISION, code_identity, stub_enabled


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


def _answer(connection: socket.socket, encoder, code: str) -> dict | None:
    """One request in, one reply out; a bad request must not kill the loop."""
    try:
        connection.settimeout(5)
        data = b""
        while not data.endswith(b"\n"):
            chunk = connection.recv(1 << 16)
            if not chunk:
                break
            data += chunk
        if not data.strip():
            return None
        request = json.loads(data)
        if request.get("code") != code:
            return {"semantic": "off: stale server"}
        return execute(request, encoder)
    except Exception as error:  # noqa: BLE001 - the loop outlives any one request
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return {"semantic": f"off: {type(error).__name__}"}


def serve(socket_path: Path, idle_hours: float) -> None:
    """Keep the encoder loaded and answer one JSON request per connection."""
    lock_path = Path(f"{socket_path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another server already owns this socket
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    encoder = StubEncoder() if stub_enabled() else ModelEncoder()
    # What this process is; a client built from newer code retires it.
    code = code_identity()
    socket_path.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o077)
    try:
        listener.bind(str(socket_path))
    finally:
        os.umask(old_umask)
    os.chmod(socket_path, 0o600)
    listener.listen(8)
    idle_seconds = max(1.0, idle_hours * 3600)
    deadline = time.monotonic() + idle_seconds
    # Wake periodically so a home directory that has been removed ends the server.
    listener.settimeout(min(60.0, idle_seconds))
    try:
        while True:
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                if time.monotonic() >= deadline or not socket_path.exists():
                    break
                continue
            deadline = time.monotonic() + idle_seconds
            with connection:
                reply = _answer(connection, encoder, code)
                if reply is not None:
                    try:
                        connection.sendall((json.dumps(reply) + "\n").encode())
                    except OSError:
                        pass
                if reply is not None and reply["semantic"] == "off: stale server":
                    break
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    if "--serve" in sys.argv[1:]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--serve", type=Path, required=True)
        parser.add_argument("--idle-hours", type=float, default=12)
        args = parser.parse_args()
        serve(args.serve, args.idle_hours)
        return
    print(json.dumps(execute(json.load(sys.stdin))))


if __name__ == "__main__":
    main()
