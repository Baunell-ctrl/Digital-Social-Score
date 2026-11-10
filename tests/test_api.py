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
