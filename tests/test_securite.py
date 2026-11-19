"""Défenses du TP2 contre les attaques du cours 5 : chaque test prouve qu'une défense agit.

- exemples adversariaux : normalisation des mots déguisés ;
- extraction / inversion du modèle : score arrondi par paliers, quota quotidien ;
- fuzzing / injection JSON : aucune entrée ne provoque d'erreur 500, champ inconnu refusé ;
- empoisonnement / modèle remplacé : empreintes SHA-256 et audit du jeu d'entraînement.
"""
import json
import os
import random
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VOCAB = json.loads((ROOT / "artifacts" / "vocab.json").read_text())

from src.preprocessing.robustesse import normaliser  # noqa: E402
from src.security.integrite import auditer_jeu, empreinte, verifier_artefacts  # noqa: E402


# --- Exemples adversariaux ----------------------------------------------------

@pytest.mark.parametrize("deguise", ["id1ot", "𝐢𝐝𝐢𝐨𝐭", "іdiot", "iiidiooot", "i.d.i.o.t", "id\u200biot"])
def test_normalisation_decode_les_mots_deguises(deguise):
    assert normaliser(f"you are an {deguise}", VOCAB) == "you are an idiot"


def test_normalisation_mot_masque():
    assert normaliser("f*ck you", VOCAB) == "fuck you"


def test_normalisation_ne_touche_pas_un_texte_normal():
    texte = "Thank you for your help, I'll fix the article tomorrow (see page 3)."
    assert normaliser(texte, VOCAB) == texte


# --- Empoisonnement / intégrité du modèle et des données ------------------------

def test_modele_conforme_au_manifeste():
    verifier_artefacts()


def test_modele_modifie_refuse(tmp_path):
    for nom in ("lstm.pt", "vocab.json", "config.json", "manifest.json"):
        shutil.copy(ROOT / "artifacts" / nom, tmp_path / nom)
    (tmp_path / "vocab.json").write_text((tmp_path / "vocab.json").read_text().replace('"idiot"', '"idi0t"'))
    with pytest.raises(RuntimeError, match="vocab.json"):
        verifier_artefacts(tmp_path)


def test_donnees_conformes_au_manifeste():
    attendu = json.loads((ROOT / "artifacts" / "manifest.json").read_text())["donnees"]
    for jeu, h in attendu.items():
        assert empreinte(ROOT / jeu) == h, jeu


def test_audit_du_jeu_d_entrainement():
    audit = auditer_jeu(ROOT / "data" / "cleaned" / "train_dataset.csv")
    assert 0.45 <= audit["part_toxique"] <= 0.55          # distribution attendue (règle du OU)
    assert audit["textes_vides"] == 0
    assert audit["doublons_contradictoires"] / audit["lignes"] < 0.001   # pas d'étiquetage massivement inversé


# --- API : extraction, fuzzing, injection JSON ---------------------------------

pytest.importorskip("fastapi")
os.environ.setdefault("API_KEY", "test-key")
os.environ["USE_NER"] = "0"
from fastapi.testclient import TestClient  # noqa: E402

from src.api import main  # noqa: E402

main.API_KEY = main.API_KEY or "test-key"
H = {"X-API-Key": main.API_KEY}


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:
        yield c


def test_score_arrondi_par_paliers(client):
    for texte in ("you are a stupid idiot", "have a nice day", "this article is rubbish"):
        assert client.post("/score", json={"text": texte}, headers=H).json()["score"] % main.SCORE_STEP == 0
    assert main.round_score(0.987) == 100 and main.round_score(0.512) == 50 and main.round_score(0.024) == 0
    assert main.round_score(0.987, step=0) == 98.7


def test_quota_quotidien(client, monkeypatch):
    monkeypatch.setattr(main, "DAILY_QUOTA_PER_CLIENT", 3)
    main._quota.clear()
    codes = [client.post("/score", json={"text": "hello"}, headers=H).status_code for _ in range(4)]
    main._quota.clear()
    assert codes == [200, 200, 200, 429]


def test_champ_inconnu_refuse(client):
    r = client.post("/score", json={"text": "hello", "admin": True}, headers=H)
    assert r.status_code == 422


@pytest.mark.parametrize("corps", [
    {"text": "__import__('os').system('rm -rf /')"},     # injection de code : le texte n'est jamais évalué
    {"text": "<script>alert(1)</script>"},
    {"text": "'; DROP TABLE users; --"},
    {"text": {"$ne": None}},                             # injection d'objet à la place du texte
    {"text": ["a", "b"]},
    {"text": 12345},
    [],
    "texte nu",
])
def test_injection_json(client, corps):
    assert client.post("/score", json=corps, headers=H).status_code in (200, 422)


def test_fuzzing_aucune_erreur_500(client):
    rng = random.Random(42)
    alphabets = [range(0x20, 0x7F), range(0x00, 0x20), range(0x0400, 0x0500), range(0x1F300, 0x1F650),
                 range(0x0590, 0x0700), range(0x2000, 0x2070), range(0x4E00, 0x4F00)]
    for _ in range(200):
        n = rng.choice([1, 5, 50, 500, 5000])
        texte = "".join(chr(rng.choice(rng.choice(alphabets))) for _ in range(n))
        r = client.post("/score", json={"text": texte}, headers=H)
        assert r.status_code in (200, 422), (r.status_code, texte[:50])


def test_limiteur_equitable(monkeypatch):
    """Un client ne peut pas prendre toutes les places du pod : les autres gardent les leurs."""
    import threading

    monkeypatch.setattr(main, "MAX_CONCURRENT_PER_CLIENT", 2)
    monkeypatch.setattr(main, "_scoring_slots", threading.Semaphore(4))
    assert [main.prendre_place("attaquant") for _ in range(3)] == [True, True, False]
    assert [main.prendre_place("temoin") for _ in range(2)] == [True, True]
    assert main.prendre_place("troisieme") is False          # les 4 places du pod sont prises
    for c in ("attaquant", "attaquant", "temoin", "temoin"):
        main.rendre_place(c)
    assert main._en_cours == {}


def test_cle_expiree():
    assert main.parse_keys("", "a:cle-a,b:cle-b:2020-01-01") == {"cle-a": "a", "cle-b": "b"}
    assert main.parse_expiry("a:cle-a,b:cle-b:2020-01-01") == {"b": "2020-01-01"}


def test_cle_expiree_refusee(client, monkeypatch):
    monkeypatch.setitem(main.state, "keys", {**main.state["keys"], "cle-perimee-0000000": "ancien"})
    monkeypatch.setitem(main.state, "expiry", {"ancien": "2020-01-01"})
    r = client.post("/score", json={"text": "hello"}, headers={"X-API-Key": "cle-perimee-0000000"})
    assert r.status_code == 401
    assert client.post("/score", json={"text": "hello"}, headers=H).status_code == 200
