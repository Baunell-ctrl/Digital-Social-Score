import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
if not (Path(__file__).resolve().parents[1] / "artifacts" / "lstm.pt").exists():
    pytest.skip("poids du LSTM absents", allow_module_level=True)

os.environ["API_KEY"] = "test-key"
os.environ["USE_NER"] = "0"  # plus rapide pour les tests

from fastapi.testclient import TestClient  # noqa: E402

from src.api import main  # noqa: E402

main.API_KEY = "test-key"


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_auth_required(client):
    assert client.post("/score", json={"text": "hello"}).status_code == 401
    assert client.post("/score", json={"text": "hello"}, headers={"X-API-Key": "bad"}).status_code == 401


def test_metrics_protected(client):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"X-API-Key": "test-key"}).status_code == 200
    assert client.get("/metrics", headers={"Authorization": "Bearer test-key"}).status_code == 200


def test_input_validation(client):
    h = {"X-API-Key": "test-key"}
    assert client.post("/score", json={"text": ""}, headers=h).status_code == 422
    assert client.post("/score", json={"text": "a" * 5001}, headers=h).status_code == 422
    assert client.post("/score", json={"text": "hello", "model": "gpt"}, headers=h).status_code == 422


def test_scores(client):
    h = {"X-API-Key": "test-key"}
    nice = client.post("/score", json={"text": "Thank you for your help, have a nice day"}, headers=h).json()
    bad = client.post("/score", json={"text": "you are a stupid fucking idiot"}, headers=h).json()
    assert 0 <= nice["score"] <= 100 and 0 <= bad["score"] <= 100
    assert bad["score"] > nice["score"]
    assert bad["toxic"] and not nice["toxic"]


def test_response_format(client):
    r = client.post("/score", json={"text": "you are an idiot"}, headers={"X-API-Key": "test-key"}).json()
    assert set(r) == {"score", "toxic", "model", "latency_ms"}
    assert r["model"] == "lstm"


# --------------------------------------------------------------------------- #
# Étape 4 : sécurité
# --------------------------------------------------------------------------- #


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Cache-Control"] == "no-store"


def test_body_too_large_rejected(client):
    r = client.post("/score", content=b"x" * 40_000,
                    headers={"X-API-Key": "test-key", "Content-Type": "application/json"})
    assert r.status_code == 413


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MINUTE", 3)
    main._hits.clear()
    h = {"X-API-Key": "test-key"}
    codes = [client.post("/score", json={"text": "hello"}, headers=h).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    main._hits.clear()


def test_parse_keys_multiple_clients():
    keys = main.parse_keys("", "moderation:aaaa,mobile:bbbb,cccc")
    assert keys == {"aaaa": "moderation", "bbbb": "mobile", "cccc": "client-3"}


def test_audit_log_has_no_secret(client, caplog):
    with caplog.at_level("INFO", logger="dss"):
        client.post("/score", json={"text": "hello"}, headers={"X-API-Key": "wrong-secret-key"})
        client.post("/score", json={"text": "my secret sentence"}, headers={"X-API-Key": "test-key"})
    logs = caplog.text
    assert '"event": "auth_failed"' in logs
    assert "wrong-secret-key" not in logs           # la clé testée n'est jamais écrite
    assert "my secret sentence" not in logs         # le texte n'est jamais écrit
    assert '"client": "default"' in logs


def test_body_too_large_without_content_length(client):
    """Envoi « chunked » sans Content-Length : la limite porte sur les octets réellement reçus."""
    def chunks():
        for _ in range(10):
            yield b"x" * 8_192
    r = client.post("/score", content=chunks(),
                    headers={"X-API-Key": "test-key", "Content-Type": "application/json"})
    assert "content-length" not in {k.lower() for k in r.request.headers}
    assert r.status_code == 413


def test_client_ip_ignores_spoofed_forwarded_for(monkeypatch):
    from starlette.requests import Request

    def req(xff):
        return Request({"type": "http", "client": ("10.0.0.9", 1234),
                        "headers": [(b"x-forwarded-for", xff.encode())]})

    # derrière l'Ingress GKE : "<ce que le client a écrit>, <IP réelle>, <IP du load balancer>"
    monkeypatch.setattr(main, "TRUSTED_PROXY_HOPS", 2)
    assert main.client_ip(req("6.6.6.6, 203.0.113.7, 34.1.2.3")) == "203.0.113.7"
    assert main.client_ip(req("203.0.113.7, 34.1.2.3")) == "203.0.113.7"
    # sans proxy de confiance, l'en-tête est ignoré
    monkeypatch.setattr(main, "TRUSTED_PROXY_HOPS", 0)
    assert main.client_ip(req("6.6.6.6")) == "10.0.0.9"


# --------------------------------------------------------------------------- #
# Étape 6 : supervision (métriques Prometheus)
# --------------------------------------------------------------------------- #


def _metric(client, line_start: str) -> float:
    """Valeur d'une série dans /metrics (0 si absente)."""
    text = client.get("/metrics", headers={"X-API-Key": "test-key"}).text
    for line in text.splitlines():
        if line.startswith(line_start + " "):
            return float(line.split()[-1])
    return 0.0


def test_metrics_count_auth_failures(client):
    before = _metric(client, 'dss_auth_failures_total{reason="invalide"}')
    client.post("/score", json={"text": "hello"}, headers={"X-API-Key": "bad"})
    assert _metric(client, 'dss_auth_failures_total{reason="invalide"}') == before + 1


def test_metrics_count_rate_limited(client, monkeypatch):
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MINUTE", 1)
    main._hits.clear()
    before = _metric(client, 'dss_rate_limited_total{client="default"}')
    h = {"X-API-Key": "test-key"}
    client.post("/score", json={"text": "hello"}, headers=h)
    assert client.post("/score", json={"text": "hello"}, headers=h).status_code == 429
    monkeypatch.setattr(main, "RATE_LIMIT_PER_MINUTE", 0)
    assert _metric(client, 'dss_rate_limited_total{client="default"}') == before + 1
    main._hits.clear()


def test_metrics_inference_and_http_latency(client):
    before = _metric(client, 'dss_inference_latency_seconds_count{model="lstm"}')
    client.post("/score", json={"text": "hello"}, headers={"X-API-Key": "test-key"})
    assert _metric(client, 'dss_inference_latency_seconds_count{model="lstm"}') == before + 1
    text = client.get("/metrics", headers={"X-API-Key": "test-key"}).text
    # seuil de 0,3 s du SLO présent dans l'histogramme HTTP ; /health exclu des métriques
    assert 'http_request_duration_seconds_bucket{handler="/score",le="0.3",method="POST"}' in text
    assert 'handler="/health"' not in text


# --------------------------------------------------------------------------- #
# Page web de test : la clé API est saisie par l'utilisateur
# --------------------------------------------------------------------------- #


def test_web_page_served_with_csp(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "font-src 'self'" in csp
    assert "http" not in csp.replace("'self'", "")          # aucune ressource externe autorisée


def test_web_page_has_no_key_and_no_external_resource(client):
    html = client.get("/").text
    assert "test-key" not in html and "X-API-Key" in html   # l'en-tête est utilisé, aucune clé n'est écrite
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert 'href="/grafana/"' in html                        # bouton de supervision


def test_fonts_served_by_api_and_cacheable(client):
    r = client.get("/fonts/literata-latin-wght-normal.woff2")
    assert r.status_code == 200 and len(r.content) > 10_000
    assert r.headers["Cache-Control"] == "public, max-age=86400"
    assert client.get("/fonts/absent.woff2").status_code == 404


def test_keyless_demo_route_removed(client):
    assert client.post("/demo/score", json={"text": "hello"}).status_code in (404, 405)
    assert client.post("/score", json={"text": "hello"}).status_code == 401   # /score exige toujours la clé


def test_web_page_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(main, "DEMO_ENABLED", False)
    assert client.get("/").status_code == 404
