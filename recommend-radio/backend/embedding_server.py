"""Local OpenAI-compatible BGE-M3 dense embedding server for AMEM."""

from __future__ import annotations

import os
import hashlib
import sqlite3
import time
from array import array
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer


MODEL_PATH = os.getenv(
    "BGE_M3_MODEL_PATH",
    r"C:\Users\Administrator\.cache\huggingface\hub\models--BAAI--bge-m3\snapshots\5617a9f61b028005a4858fdac845db406aefb181",
)
MODEL_NAME = os.getenv("BGE_M3_MODEL_NAME", "bge-m3")
DEVICE = os.getenv("BGE_M3_DEVICE", "cpu")

app = FastAPI(title="Local BGE-M3 Embeddings")
_model: SentenceTransformer | None = None
_cache: OrderedDict[str, list[float]] = OrderedDict()
_cache_lock = RLock()
_cache_limit = max(int(os.getenv("BGE_M3_CACHE_SIZE", "4096")), 128)
CACHE_PATH = Path(
    os.getenv(
        "BGE_M3_CACHE_PATH",
        str(Path(__file__).resolve().parents[1] / "server-data" / "embedding-cache" / "bge-m3.sqlite3"),
    )
)


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]


def _model_instance() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_PATH, device=DEVICE, local_files_only=True)
    return _model


@app.on_event("startup")
def load_model() -> None:
    _model_instance()
    _initialize_disk_cache()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ready" if _model is not None else "cold", "model": MODEL_NAME, "device": DEVICE}


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "local"}]}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest) -> dict[str, Any]:
    if request.model not in {MODEL_NAME, "BAAI/bge-m3"}:
        raise HTTPException(status_code=400, detail=f"unsupported embedding model: {request.model}")
    values = [request.input] if isinstance(request.input, str) else request.input
    if not values or any(not isinstance(value, str) for value in values):
        raise HTTPException(status_code=400, detail="input must contain one or more strings")
    with _cache_lock:
        missing = list(dict.fromkeys(value for value in values if value not in _cache))
        for text, vector in _load_disk_vectors(missing).items():
            _cache[text] = vector
            _cache.move_to_end(text)
        missing = [value for value in missing if value not in _cache]
        if missing:
            encoded = _model_instance().encode(missing, normalize_embeddings=True, show_progress_bar=False)
            persisted: dict[str, list[float]] = {}
            for text, vector in zip(missing, encoded):
                values_list = vector.tolist()
                _cache[text] = values_list
                _cache.move_to_end(text)
                persisted[text] = values_list
            _store_disk_vectors(persisted)
            while len(_cache) > _cache_limit:
                _cache.popitem(last=False)
        vectors = []
        for value in values:
            vector = _cache[value]
            _cache.move_to_end(value)
            vectors.append(vector)
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
        "created": int(time.time()),
    }


def _initialize_disk_cache() -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CACHE_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embedding_cache (
                cache_key TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )


def _cache_key(text: str) -> str:
    return hashlib.sha256(f"{MODEL_PATH}\0{text}".encode("utf-8")).hexdigest()


def _load_disk_vectors(texts: list[str]) -> dict[str, list[float]]:
    if not texts or not CACHE_PATH.exists():
        return {}
    by_key = {_cache_key(text): text for text in texts}
    placeholders = ",".join("?" for _ in by_key)
    with sqlite3.connect(CACHE_PATH) as conn:
        rows = conn.execute(
            f"SELECT cache_key, vector FROM embedding_cache WHERE cache_key IN ({placeholders})",
            tuple(by_key),
        ).fetchall()
    result: dict[str, list[float]] = {}
    for key, payload in rows:
        values = array("f")
        values.frombytes(payload)
        if values:
            result[by_key[key]] = list(values)
    return result


def _store_disk_vectors(values: dict[str, list[float]]) -> None:
    if not values:
        return
    now = time.time()
    with sqlite3.connect(CACHE_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO embedding_cache (cache_key, vector, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET vector=excluded.vector, updated_at=excluded.updated_at
            """,
            [(_cache_key(text), array("f", vector).tobytes(), now) for text, vector in values.items()],
        )
        conn.execute(
            """
            DELETE FROM embedding_cache
            WHERE cache_key IN (
                SELECT cache_key FROM embedding_cache
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (_cache_limit,),
        )
