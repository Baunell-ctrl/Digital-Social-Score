"""API Digital Social Score : texte -> score de toxicité de 0 à 100.

Lancement local :  API_KEY=test uvicorn src.api.main:app --reload
Documentation   :  http://localhost:8000/docs

Sécurité / RGPD :
- clé API obligatoire (en-tête ``X-API-Key``), comparée en temps constant ;
- validation d'entrée (texte de 1 à 5 000 caractères) ;
- le texte n'est jamais stocké ni journalisé : seul un hash (pseudonyme) est écrit dans les logs ;
- HTTPS fourni par la plateforme Cloud (Cloud Run / Scaleway).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response, Security
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from .predictor import LayaPredictor, ToxicityPredictor

THRESHOLD = 0.5  # même seuil qu'à l'entraînement (artifacts/config.json)
API_KEY = os.environ.get("API_KEY", "")
# Clé secrète du HMAC des logs. Sans LOG_SALT, une clé aléatoire est tirée au démarrage :
# les identifiants ne sont alors comparables qu'au sein d'une même instance.
LOG_SALT = os.environ.get("LOG_SALT") or secrets.token_hex(32)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dss")

SCORE_HIST = Histogram("dss_toxicity_score", "Scores de toxicité renvoyés", buckets=(10, 25, 50, 75, 90, 100))

state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not API_KEY:
        raise RuntimeError("La variable d'environnement API_KEY est obligatoire")
    state["predictor"] = ToxicityPredictor(use_ner=os.environ.get("USE_NER", "1") == "1")
    yield


app = FastAPI(title="Digital Social Score", version="1.0.0", lifespan=lifespan)
Instrumentator().instrument(app)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.get("/metrics", include_in_schema=False)
def metrics(x_api_key: str | None = Header(None), authorization: str | None = Header(None)) -> Response:
    """Métriques Prometheus, protégées par la clé API (``X-API-Key`` ou ``Authorization: Bearer``)."""
    key = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    if not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(status_code=401, detail="Clé API invalide ou absente")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def check_api_key(key: str | None = Security(api_key_header)) -> str:
    if not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(status_code=401, detail="Clé API invalide ou absente")
    return key


_laya_lock = threading.Lock()


def get_laya() -> LayaPredictor:
    """Charge LAYA au premier appel (environ 840 Mo), puis le garde en mémoire."""
    with _laya_lock:
        if "laya" not in state:
            try:
                state["laya"] = LayaPredictor(state["predictor"].preprocess)
            except (ImportError, FileNotFoundError) as e:
                raise HTTPException(status_code=503, detail=f"LAYA indisponible : {e}") from e
    return state["laya"]


class ScoreRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000, examples=["Thank you for your help, have a nice day"])
    model: Literal["lstm", "laya"] = Field(
        default="lstm", description="lstm (déployé, rapide) ou laya (local uniquement, plus lent)")


class ScoreResponse(BaseModel):
    score: float = Field(description="Toxicité de 0 (respectueux) à 100 (très toxique)")
    toxic: bool = Field(description="true si le score dépasse le seuil (50)")
    model: str
    latency_ms: float


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if "predictor" in state else "loading"}


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest, _: str = Depends(check_api_key)) -> ScoreResponse:
    predictor = get_laya() if req.model == "laya" else state["predictor"]
    t0 = time.perf_counter()
    proba = predictor.predict(req.text)
    latency = (time.perf_counter() - t0) * 1000
    value = round(proba * 100, 1)
    SCORE_HIST.observe(value)

    # Log pseudonyme : HMAC-SHA256 du texte avec une clé secrète, jamais le texte ni sa longueur.
    text_id = hmac.new(LOG_SALT.encode(), req.text.encode(), hashlib.sha256).hexdigest()[:16]
    log.info(json.dumps({"event": "score", "model": req.model, "text_id": text_id,
                         "score": value, "latency_ms": round(latency, 2)}))

    return ScoreResponse(
        score=value,
        toxic=proba >= THRESHOLD,
        model=req.model,
        latency_ms=round(latency, 2),
    )
