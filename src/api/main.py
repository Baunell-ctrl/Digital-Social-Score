"""API Digital Social Score : texte -> score de toxicité de 0 à 100.

Lancement local :  API_KEY=test uvicorn src.api.main:app --reload
Documentation   :  http://localhost:8000/docs

Sécurité / RGPD (étape 4 du TP, détaillée dans le README) :
- une clé API par client (``X-API-Key``), comparée en temps constant, révocable individuellement ;
- limitation du nombre de requêtes par client (429) contre l'abus et l'extraction du modèle ;
- validation des entrées (texte de 1 à 5 000 caractères, corps de requête limité à 32 Ko) ;
- le texte n'est jamais stocké ni journalisé : seul un identifiant HMAC (pseudonyme) est écrit ;
- échecs d'authentification journalisés (journal d'audit), sans la clé ni l'adresse IP en clair ;
- en-têtes de sécurité HTTP ; HTTPS fourni par la plateforme cloud (Cloud Run).
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
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from .predictor import LayaPredictor, ToxicityPredictor

THRESHOLD = 0.5  # même seuil qu'à l'entraînement (artifacts/config.json)
API_KEY = os.environ.get("API_KEY", "")
# Plusieurs clients : API_KEYS="moderation:cle1,mobile:cle2". Le nom du client apparaît dans les logs
# (jamais la clé), et chaque clé peut être révoquée sans toucher aux autres.
API_KEYS = os.environ.get("API_KEYS", "")
# Requêtes autorisées par client et par minute, sur chaque instance (0 = pas de limite).
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600"))
MAX_BODY_BYTES = 32_768
# Nombre d'entrées ajoutées à la fin de X-Forwarded-For par les proxys de confiance, IP du client comprise.
# GKE Ingress : 2 (le load balancer Google ajoute "<IP du client>, <IP du load balancer>").
# En local, sans proxy : 0 (on prend l'IP de la connexion).
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
# Clé secrète du HMAC des logs. Sans LOG_SALT, une clé aléatoire est tirée au démarrage :
# les identifiants ne sont alors comparables qu'au sein d'une même instance.
LOG_SALT = os.environ.get("LOG_SALT") or secrets.token_hex(32)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dss")

SCORE_HIST = Histogram("dss_toxicity_score", "Scores de toxicité renvoyés", buckets=(10, 25, 50, 75, 90, 100))

state: dict = {}


def parse_keys(single: str, multiple: str) -> dict[str, str]:
    """Retourne {clé: nom du client}. ``multiple`` = "nom:clé,nom:clé" (le nom est facultatif)."""
    keys: dict[str, str] = {}
    if single:
        keys[single] = "default"
    for i, entry in enumerate(e.strip() for e in multiple.split(",") if e.strip()):
        name, sep, key = entry.partition(":")
        keys[key if sep else name] = name if sep else f"client-{i + 1}"
    return keys


def pseudonym(value: str) -> str:
    """Identifiant stable et non réversible (HMAC-SHA256 avec la clé secrète LOG_SALT)."""
    return hmac.new(LOG_SALT.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["keys"] = parse_keys(API_KEY, API_KEYS)
    if not state["keys"]:
        raise RuntimeError("Définir API_KEY ou API_KEYS : l'API refuse de démarrer sans clé")
    if any(len(k) < 16 for k in state["keys"]):
        log.warning(json.dumps({"event": "weak_api_key", "detail": "clé API de moins de 16 caractères"}))
    state["predictor"] = ToxicityPredictor(use_ner=os.environ.get("USE_NER", "1") == "1")
    yield


app = FastAPI(title="Digital Social Score", version="1.1.0", lifespan=lifespan)
Instrumentator().instrument(app)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",  # les scores ne doivent pas rester dans un cache intermédiaire
    "Strict-Transport-Security": "max-age=31536000",  # ignoré en HTTP, actif derrière HTTPS
}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Ajoute les en-têtes de sécurité à toutes les réponses."""
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


class BodySizeLimitMiddleware:
    """Limite la taille du corps à MAX_BODY_BYTES (413).

    L'en-tête Content-Length ne suffit pas : un client peut l'omettre (envoi « chunked ») ou mentir.
    Le corps est donc lu ici, morceau par morceau, en comptant les octets réellement reçus : au-delà
    de la limite, on répond 413 sans lire la suite. Sinon, le corps (32 Ko au plus) est transmis à l'API.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        too_large = JSONResponse({"detail": "Requête trop volumineuse"}, status_code=413,
                                 headers=SECURITY_HEADERS)
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            return await too_large(scope, receive, send)

        body, more = b"", True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_BODY_BYTES:
                return await too_large(scope, receive, send)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


app.add_middleware(BodySizeLimitMiddleware)


def client_ip(request: Request) -> str:
    """IP du client, sans faire confiance à ce que le client écrit lui-même dans X-Forwarded-For.

    Les proxys de confiance AJOUTENT leurs entrées à la fin de l'en-tête. La vraie IP du client est
    donc la N-ième en partant de la fin (N = TRUSTED_PROXY_HOPS, 2 derrière l'Ingress GKE).
    Les entrées avant elle peuvent être inventées par le client et sont ignorées.
    """
    peer = request.client.host if request.client else "inconnue"
    if TRUSTED_PROXY_HOPS <= 0:
        return peer
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return parts[-TRUSTED_PROXY_HOPS] if len(parts) >= TRUSTED_PROXY_HOPS else peer


def authenticate(key: str | None, request: Request) -> str:
    """Retourne le nom du client, ou lève 401. Chaque échec est écrit dans le journal d'audit."""
    for known, name in state["keys"].items():
        if key and secrets.compare_digest(key.encode(), known.encode()):
            return name
    log.warning(json.dumps({"event": "auth_failed", "path": request.url.path,
                            "reason": "absente" if not key else "invalide",
                            "ip_id": pseudonym(client_ip(request))}))
    raise HTTPException(status_code=401, detail="Clé API invalide ou absente")


_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limit(client: str) -> None:
    """Fenêtre glissante d'une minute par client (compteur propre à chaque instance)."""
    if RATE_LIMIT_PER_MINUTE <= 0:
        return
    now = time.monotonic()
    with _hits_lock:
        hits = _hits[client]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_PER_MINUTE:
            retry = int(60 - (now - hits[0])) + 1
            log.warning(json.dumps({"event": "rate_limited", "client": client}))
            raise HTTPException(status_code=429, detail="Trop de requêtes, réessayer plus tard",
                                headers={"Retry-After": str(retry)})
        hits.append(now)


def check_api_key(request: Request, key: str | None = Security(api_key_header)) -> str:
    client = authenticate(key, request)
    rate_limit(client)
    return client


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request, x_api_key: str | None = Header(None),
            authorization: str | None = Header(None)) -> Response:
    """Métriques Prometheus, protégées par la clé API (``X-API-Key`` ou ``Authorization: Bearer``)."""
    bearer = (authorization or "").removeprefix("Bearer ").strip()
    authenticate(x_api_key or bearer or None, request)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
def score(req: ScoreRequest, client: str = Depends(check_api_key)) -> ScoreResponse:
    predictor = get_laya() if req.model == "laya" else state["predictor"]
    t0 = time.perf_counter()
    proba = predictor.predict(req.text)
    latency = (time.perf_counter() - t0) * 1000
    value = round(proba * 100, 1)
    SCORE_HIST.observe(value)

    # Log pseudonyme : HMAC-SHA256 du texte avec une clé secrète, jamais le texte ni sa longueur.
    log.info(json.dumps({"event": "score", "client": client, "model": req.model,
                         "text_id": pseudonym(req.text), "score": value, "latency_ms": round(latency, 2)}))

    return ScoreResponse(
        score=value,
        toxic=proba >= THRESHOLD,
        model=req.model,
        latency_ms=round(latency, 2),
    )
