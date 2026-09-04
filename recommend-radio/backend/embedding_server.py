"""Local OpenAI-compatible BGE-M3 dense embedding server for AMEM."""

from __future__ import annotations

import os
import time
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
    vectors = _model_instance().encode(values, normalize_embeddings=True, show_progress_bar=False)
    return {
        "object": "list",
        "model": MODEL_NAME,
        "data": [
            {"object": "embedding", "index": index, "embedding": vector.tolist()}
            for index, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
        "created": int(time.time()),
    }
