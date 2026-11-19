"""API Digital Social Score : texte -> score de toxicité de 0 à 100.

Lancement local :  API_KEY=test uvicorn src.api.main:app --reload
Documentation   :  http://localhost:8000/docs

Sécurité / RGPD (étape 4 du TP, détaillée dans docs/JOURNAL.md) :
- une clé API par client (``X-API-Key``), comparée en temps constant, révocable individuellement,
  avec une date d'expiration facultative (``API_KEYS="nom:clé:2027-01-31"``) ;
- limitation du nombre de requêtes par client (429) contre l'abus et l'extraction du modèle ;
- validation des entrées (texte de 1 à 5 000 caractères, corps de requête limité à 32 Ko) ;
- le texte n'est jamais stocké ni journalisé : seul un identifiant HMAC (pseudonyme) est écrit ;
- échecs d'authentification journalisés (journal d'audit), sans la clé ni l'adresse IP en clair ;
- en-têtes de sécurité HTTP (dont une Content-Security-Policy stricte) ;
- page web (``/``) : l'utilisateur y saisit sa clé API, qui n'est jamais dans la page ni dans le code.
- limite du nombre de requêtes ``/score`` traitées EN MÊME TEMPS par ce pod (503 immédiat au-delà), contre
  les attaques par coût asymétrique qui restent sous le seuil de ``RATE_LIMIT_PER_MINUTE`` (TP2 étape 3).

Défenses contre les autres attaques du cours 5 (TP2, README_TP2 section 3) :
- extraction / inversion du modèle : score arrondi par paliers (``SCORE_STEP``), jamais la probabilité
  exacte, et quota quotidien de requêtes par client (``DAILY_QUOTA_PER_CLIENT``) ;
- exemples adversariaux (mots déguisés) : normalisation des entrées (src/preprocessing/robustesse.py) ;
- fuzzing / injection JSON : schéma strict (champ inconnu refusé), le texte n'est jamais évalué ni exécuté ;
- empoisonnement / modèle remplacé : empreintes SHA-256 des fichiers du modèle vérifiées au démarrage
  (src/security/integrite.py, artifacts/manifest.json).

Supervision (étape 6) : ``/metrics`` expose les métriques Prometheus, collectées sur GKE par
Managed Service for Prometheus (voir k8s/monitoring/) :
- HTTP (Instrumentator) : requêtes, codes de réponse, latence par route ;
- IA (instrumentation manuelle) : latence d'inférence, distribution des scores ;
- sécurité : échecs d'authentification et requêtes refusées par la limite (429).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
from pathlib import Path
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, Security
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator, metrics as instr_metrics
from pydantic import BaseModel, ConfigDict, Field

from src.security.integrite import verifier_artefacts

from .predictor import LayaPredictor, ToxicityPredictor

THRESHOLD = 0.5  # même seuil qu'à l'entraînement (artifacts/config.json)
API_KEY = os.environ.get("API_KEY", "")
# Plusieurs clients : API_KEYS="moderation:cle1,mobile:cle2". Le nom du client apparaît dans les logs
# (jamais la clé), et chaque clé peut être révoquée sans toucher aux autres.
API_KEYS = os.environ.get("API_KEYS", "")
# Requêtes autorisées par client et par minute, sur chaque instance (0 = pas de limite).
RATE_LIMIT_PER_MINUTE = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "600"))
# Requêtes /score traitées EN MÊME TEMPS, sur chaque instance (0 = pas de limite). Contrairement à
# RATE_LIMIT_PER_MINUTE (qui compte des requêtes), ceci borne le coût de calcul en cours : une attaque
# par coût asymétrique peut rester sous la limite de débit tout en saturant le pipeline (TP2 étape 3).
MAX_CONCURRENT_SCORING = int(os.environ.get("MAX_CONCURRENT_SCORING", "0"))
# Partage équitable des places : traitements /score simultanés autorisés POUR UNE MÊME CLÉ (0 = pas de limite).
# Mesuré en local (TP2) : avec le seul limiteur global, l'attaquant prend toutes les places et 99 % des
# messages des autres clients sont refusés ; une limite par clé lui interdit de les monopoliser.
MAX_CONCURRENT_PER_CLIENT = int(os.environ.get("MAX_CONCURRENT_PER_CLIENT", "0"))
MAX_BODY_BYTES = 32_768
# Contre l'extraction du modèle (cours 5, § 17 : « ne jamais envoyer les scores exacts ») : le score est
# arrondi au palier le plus proche (5 → 0, 5, 10… 100). Un attaquant qui veut cloner le modèle n'obtient
# qu'une sortie grossière ; la décision toxique / non toxique, elle, reste calculée sur la probabilité exacte.
SCORE_STEP = float(os.environ.get("SCORE_STEP", "5"))
# Quota quotidien de requêtes /score par client, sur chaque instance (0 = pas de quota). Cloner un modèle
# demande des dizaines de milliers de requêtes : au-delà du quota, 429 jusqu'au lendemain, et un événement
# « quota_exceeded » dans les logs signale le client.
DAILY_QUOTA_PER_CLIENT = int(os.environ.get("DAILY_QUOTA_PER_CLIENT", "0"))
# Refuse de démarrer si les fichiers du modèle ne correspondent pas aux empreintes de artifacts/manifest.json.
VERIFY_ARTIFACTS = os.environ.get("VERIFY_ARTIFACTS", "1") == "1"
# Page web de test (``/``) : l'utilisateur y saisit sa clé API. Désactivable avec DEMO_ENABLED=0.
DEMO_ENABLED = os.environ.get("DEMO_ENABLED", "1") == "1"
STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("font/woff2", ".woff2")  # absent de la base de types de l'image Python slim
DEMO_PAGE = STATIC_DIR / "index.html"
# La page contient son CSS et son JS ; les polices sont servies par l'API elle-même (/fonts/) :
# aucune ressource externe, donc aucune adresse IP de visiteur envoyée à un tiers (RGPD).
DEMO_CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
# Nombre d'entrées ajoutées à la fin de X-Forwarded-For par les proxys de confiance, IP du client comprise.
# GKE Ingress : 2 (le load balancer Google ajoute "<IP du client>, <IP du load balancer>").
# En local, sans proxy : 0 (on prend l'IP de la connexion).
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "0"))
# Clé secrète du HMAC des logs. Sans LOG_SALT, une clé aléatoire est tirée au démarrage :
# les identifiants ne sont alors comparables qu'au sein d'une même instance.
LOG_SALT = os.environ.get("LOG_SALT") or secrets.token_hex(32)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dss")

# --- Métriques Prometheus propres à l'API (méthode n°2 du cours : instrumentation manuelle) ---
# Seuils de latence (secondes) : 0,3 s pour le SLO du cours, 0,5 s pour l'alerte de latence. Ils vont jusqu'à
# 30 s, le délai maximal d'une requête : avec un dernier seuil à 10 s, Grafana affichait un plat à 10 s pendant
# l'attaque du TP2 alors que les clients attendaient 30 s.
LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5, 10, 15, 20, 30)
SCORE_HIST = Histogram("dss_toxicity_score", "Scores de toxicité renvoyés", buckets=(10, 25, 50, 75, 90, 100))
INFERENCE_LATENCY = Histogram("dss_inference_latency_seconds",
                              "Durée de l'inférence seule (anonymisation + nettoyage + modèle)",
                              ["model"], buckets=LATENCY_BUCKETS)
AUTH_FAILURES = Counter("dss_auth_failures_total", "Requêtes refusées pour clé API absente ou invalide",
                        ["reason"])
RATE_LIMITED = Counter("dss_rate_limited_total", "Requêtes refusées par la limite de requêtes (429)",
                       ["client"])
OVERLOAD_REJECTED = Counter("dss_overload_rejected_total",
                            "Requêtes refusées (503) : trop de traitements /score simultanés")
# Suivi de dérive (cours 1) : si les textes reçus ou les décisions du modèle s'éloignent de ce qui a été
# observé à l'entraînement, ces deux séries le montrent dans Grafana.
UNKNOWN_WORDS = Histogram("dss_unknown_word_ratio", "Part des mots du texte inconnus du vocabulaire du modèle",
                          buckets=(0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1))
PREDICTIONS = Counter("dss_predictions_total", "Décisions du modèle", ["toxic"])
QUOTA_EXCEEDED = Counter("dss_quota_exceeded_total", "Requêtes refusées par le quota quotidien (429)",
                         ["client"])

state: dict = {}


def parse_keys(single: str, multiple: str) -> dict[str, str]:
    """Retourne {clé: nom du client}. ``multiple`` = "nom:clé,nom:clé:2027-01-31" (nom et date facultatifs)."""
    keys: dict[str, str] = {}
    if single:
        keys[single] = "default"
    for i, entry in enumerate(e.strip() for e in multiple.split(",") if e.strip()):
        parts = entry.split(":")
        if len(parts) == 1:
            keys[parts[0]] = f"client-{i + 1}"
        else:
            keys[parts[1]] = parts[0]
    return keys


def parse_expiry(multiple: str) -> dict[str, str]:
    """{nom du client: date d'expiration AAAA-MM-JJ} pour les entrées "nom:clé:date" de API_KEYS."""
    return {p[0]: p[2] for e in multiple.split(",") if len(p := e.strip().split(":")) == 3}


def pseudonym(value: str) -> str:
    """Identifiant stable et non réversible (HMAC-SHA256 avec la clé secrète LOG_SALT)."""
    return hmac.new(LOG_SALT.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["keys"] = parse_keys(API_KEY, API_KEYS)
    state["expiry"] = parse_expiry(API_KEYS)
    if not state["keys"]:
        raise RuntimeError("Définir API_KEY ou API_KEYS : l'API refuse de démarrer sans clé")
    if any(len(k) < 16 for k in state["keys"]):
        log.warning(json.dumps({"event": "weak_api_key", "detail": "clé API de moins de 16 caractères"}))
    if VERIFY_ARTIFACTS:
        verifier_artefacts()  # lève une erreur (l'API ne démarre pas) si un fichier du modèle a été modifié
    state["predictor"] = ToxicityPredictor(use_ner=os.environ.get("USE_NER", "1") == "1",
                                           normalise=os.environ.get("NORMALISATION", "1") == "1")
    # Séries créées à 0 dès le démarrage : sans elles, une métrique n'existe qu'après le premier
    # événement (premier 401, premier 429…) et une règle d'alerte qui la cite est refusée.
    for reason in ("absente", "invalide", "expiree"):
        AUTH_FAILURES.labels(reason=reason)
    for name in set(state["keys"].values()):
        RATE_LIMITED.labels(client=name)
        QUOTA_EXCEEDED.labels(client=name)
    INFERENCE_LATENCY.labels(model="lstm")
    for decision in ("true", "false"):
        PREDICTIONS.labels(toxic=decision)
    yield


app = FastAPI(title="Digital Social Score", version="1.1.0", lifespan=lifespan)
# Méthode n°1 du cours : middleware prêt à l'emploi (requêtes, codes 2xx/4xx/5xx, latence par route).
# /health, /metrics et les polices sont exclus : les sondes Kubernetes et la collecte fausseraient les taux.
Instrumentator(excluded_handlers=["/health", "/metrics", "/fonts/.*"]).add(
    instr_metrics.default(latency_lowr_buckets=LATENCY_BUCKETS)).instrument(app)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

DOCS_PATHS = {"/docs", "/redoc"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",  # les scores ne doivent pas rester dans un cache intermédiaire
    # Appliquée à TOUTES les réponses (pas seulement DEMO_CSP sur "/") : une API JSON n'a besoin
    # d'exécuter ni charger aucune ressource, donc tout est refusé par défaut.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Ajoute les en-têtes de sécurité à toutes les réponses.

    setdefault (pas update) : la page de démo pose sa propre Content-Security-Policy, plus permissive
    (elle charge ses scripts/styles/polices), qui ne doit pas être écrasée par la CSP générique ici.
    """
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        # /docs et /redoc (pages Swagger/ReDoc de FastAPI) chargent leurs scripts depuis un CDN : la règle
        # « default-src 'none' » les rendrait vides. Les autres en-têtes s'y appliquent quand même.
        if key == "Content-Security-Policy" and request.url.path in DOCS_PATHS:
            continue
        response.headers.setdefault(key, value)
    if request.url.path.startswith("/fonts/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=86400"  # polices : aucune donnée sensible
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
    reason = "absente" if not key else "invalide"
    for known, name in state["keys"].items():
        if key and secrets.compare_digest(key.encode(), known.encode()):
            expire = state.get("expiry", {}).get(name)
            if not expire or time.strftime("%Y-%m-%d", time.gmtime()) <= expire:
                return name
            reason = "expiree"   # clé connue mais périmée : refusée comme une clé invalide, motif distinct
            break
    AUTH_FAILURES.labels(reason=reason).inc()
    log.warning(json.dumps({"event": "auth_failed", "path": request.url.path,
                            "reason": reason,
                            "ip_id": pseudonym(client_ip(request))}))
    raise HTTPException(status_code=401, detail="Clé API invalide ou absente")


_hits: dict[str, deque] = defaultdict(deque)
_hits_lock = threading.Lock()

# Un jeton par requête /score en cours de traitement ; None si MAX_CONCURRENT_SCORING == 0 (désactivé).
_scoring_slots = threading.Semaphore(MAX_CONCURRENT_SCORING) if MAX_CONCURRENT_SCORING > 0 else None
_en_cours: dict[str, int] = defaultdict(int)   # traitements /score en cours, par client, sur cette instance


def prendre_place(client: str) -> bool:
    """Réserve une place de traitement : d'abord la part du client, puis une place du pod. False si refusé."""
    with _hits_lock:
        if MAX_CONCURRENT_PER_CLIENT > 0 and _en_cours[client] >= MAX_CONCURRENT_PER_CLIENT:
            return False
        _en_cours[client] += 1
    if _scoring_slots is not None and not _scoring_slots.acquire(blocking=False):
        rendre_place(client, slot=False)
        return False
    return True


def rendre_place(client: str, slot: bool = True) -> None:
    if slot and _scoring_slots is not None:
        _scoring_slots.release()
    with _hits_lock:
        _en_cours[client] -= 1
        if _en_cours[client] <= 0:
            del _en_cours[client]


def rate_limit(client: str, limit: int | None = None) -> None:
    """Fenêtre glissante d'une minute par client (compteur propre à chaque instance)."""
    limit = RATE_LIMIT_PER_MINUTE if limit is None else limit
    if limit <= 0:
        return
    now = time.monotonic()
    with _hits_lock:
        if len(_hits) > 10_000:  # évite que les compteurs d'IP anciennes grossissent sans fin
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60]:
                del _hits[k]
        hits = _hits[client]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= limit:
            retry = int(60 - (now - hits[0])) + 1
            RATE_LIMITED.labels(client=client).inc()
            log.warning(json.dumps({"event": "rate_limited", "client": client}))
            raise HTTPException(status_code=429, detail="Trop de requêtes, réessayer plus tard",
                                headers={"Retry-After": str(retry)})
        hits.append(now)


_quota: dict[str, tuple[str, int]] = {}   # client -> (jour, requêtes /score ce jour-là), sur cette instance


def daily_quota(client: str) -> None:
    """Quota quotidien par client (défense contre l'extraction du modèle). Remis à zéro chaque jour (UTC)."""
    if DAILY_QUOTA_PER_CLIENT <= 0:
        return
    jour = time.strftime("%Y-%m-%d", time.gmtime())
    with _hits_lock:
        jour_vu, n = _quota.get(client, (jour, 0))
        n = n if jour_vu == jour else 0
        if n >= DAILY_QUOTA_PER_CLIENT:
            QUOTA_EXCEEDED.labels(client=client).inc()
            log.warning(json.dumps({"event": "quota_exceeded", "client": client}))
            raise HTTPException(status_code=429, detail="Quota quotidien atteint, réessayer demain",
                                headers={"Retry-After": "3600"})
        _quota[client] = (jour, n + 1)


def round_score(proba: float, step: float | None = None) -> float:
    """Score sur 100 arrondi au palier ``step`` (contre l'extraction du modèle) ; step <= 0 : 1 décimale."""
    step = SCORE_STEP if step is None else step
    if step <= 0:
        return round(proba * 100, 1)
    return float(min(100, max(0, round(proba * 100 / step) * step)))


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
    # Schéma strict : un champ inattendu est refusé (422) au lieu d'être ignoré (fuzzing, injection JSON).
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5000, examples=["Thank you for your help, have a nice day"])
    model: Literal["lstm", "laya"] = Field(
        default="lstm", description="lstm (déployé, rapide) ou laya (local uniquement, plus lent)")


class ScoreResponse(BaseModel):
    score: float = Field(description="Toxicité de 0 (respectueux) à 100 (très toxique), arrondie par paliers")
    toxic: bool = Field(description="true si le score dépasse le seuil (50)")
    model: str
    latency_ms: float


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if "predictor" in state else "loading"}


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest, client: str = Depends(check_api_key)) -> ScoreResponse:
    daily_quota(client)
    predictor = get_laya() if req.model == "laya" else state["predictor"]

    if not prendre_place(client):
        OVERLOAD_REJECTED.inc()
        log.warning(json.dumps({"event": "overload_rejected", "client": client}))
        raise HTTPException(status_code=503, detail="Service temporairement surchargé, réessayer plus tard",
                            headers={"Retry-After": "2"})
    try:
        t0 = time.perf_counter()
        if hasattr(predictor, "predict_detail"):
            proba, inconnus = predictor.predict_detail(req.text)
            UNKNOWN_WORDS.observe(inconnus)
        else:
            proba = predictor.predict(req.text)
        latency = (time.perf_counter() - t0) * 1000
    finally:
        rendre_place(client)
    INFERENCE_LATENCY.labels(model=req.model).observe(latency / 1000)
    value = round_score(proba)
    SCORE_HIST.observe(value)
    PREDICTIONS.labels(toxic=str(proba >= THRESHOLD).lower()).inc()

    # Log pseudonyme : HMAC-SHA256 du texte avec une clé secrète, jamais le texte ni sa longueur.
    log.info(json.dumps({"event": "score", "client": client, "model": req.model,
                         "text_id": pseudonym(req.text), "score": value, "latency_ms": round(latency, 2)}))

    return ScoreResponse(
        score=value,
        toxic=proba >= THRESHOLD,
        model=req.model,
        latency_ms=round(latency, 2),
    )


# --------------------------------------------------------------------------- #
# Page web de test : la clé API est saisie par l'utilisateur, jamais écrite dans la page
# --------------------------------------------------------------------------- #


@app.get("/", include_in_schema=False)
def web_page() -> Response:
    if not DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(DEMO_PAGE, media_type="text/html; charset=utf-8",
                        headers={"Content-Security-Policy": DEMO_CSP})


# Polices de la page (licence OFL), mises en cache par le navigateur : seules ressources statiques publiques.
app.mount("/fonts", StaticFiles(directory=STATIC_DIR / "fonts", check_dir=False), name="fonts")
