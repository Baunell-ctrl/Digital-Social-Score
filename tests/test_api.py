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
