"""Conformité IA Act (TP2, étapes 4 et 5) : la model card existe, est complète et dit la vérité.

Lancé par pytest, donc par le pipeline CI/CD à chaque push : un modèle modifié sans mise à jour
de model_card.json fait échouer le build.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "model_card.json"

# Champs demandés par l'énoncé du TP2 (étape 4).
REQUIRED = ["model_name", "description", "risk_level", "data_source", "privacy_measures",
            "bias_detected", "version", "last_update"]
# Catégories de risque de l'IA Act (règlement (UE) 2024/1689).
RISK_LEVELS = {"inacceptable", "haut", "limité", "minimal"}


@pytest.fixture(scope="module")
def card():
    return json.loads(CARD.read_text(encoding="utf-8"))


def test_champs_obligatoires(card):
    missing = [k for k in REQUIRED if card.get(k) in (None, "", [], "à définir")]
    assert not missing, f"champs manquants ou non remplis dans model_card.json : {missing}"


def test_niveau_de_risque(card):
    assert card["risk_level"] in RISK_LEVELS
    assert card.get("risk_justification"), "le niveau de risque doit être justifié"


def test_biais_coherent_avec_le_modele(card, tmp_path):
    """Relance le test de biais sur le modèle actuel : bias_detected doit correspondre."""
    if not (ROOT / "artifacts" / "lstm.pt").exists():
        pytest.skip("poids du LSTM absents")
    pytest.importorskip("torch")
    from src.models.bias_check import run

    report = run(out=tmp_path / "bias_check.json")
    assert card["bias_detected"] == report["biais_detecte"], (
        f"model_card.json dit bias_detected={card['bias_detected']}, le test de biais trouve "
        f"{report['biais_detecte']} (groupes : {report['groupes_biaises']}) : mettre la fiche à jour")
