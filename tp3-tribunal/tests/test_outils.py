"""Tests de l'étape 1 (outils du Greffier) et de l'étape 2 (RAG). Aucun LLM, aucun réseau."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tribunal.outils import (analyser_forme, appliquer_guide, consulter_casier, controle_de_sortie,  # noqa: E402
                             detecter_subornation, huissier, instruire, niveau_presume)
from tribunal.rag import chercher_charte, chercher_jurisprudence  # noqa: E402

DOSSIER = Path(__file__).resolve().parent.parent / "dossier"
JUR = {j["ref"]: j for j in json.loads((DOSSIER / "jurisprudence.json").read_text())}
AFF = {a["ref"]: a for a in json.loads((DOSSIER / "affaires.json").read_text())}


# --- Étape 1 : les huit cas de l'énoncé -------------------------------------

def test_niveau_presume_menace_jur04():
    assert niveau_presume(JUR["JUR-2026-04"]["scores_reference"]) == "N4"


def test_forme_litanie_jur01():
    f = analyser_forme(JUR["JUR-2026-01"]["texte"])
    assert f["litanie"] is True and f["vociferation"] is False


def test_forme_adresse_ip_jur05():
    assert analyser_forme(JUR["JUR-2026-05"]["texte"])["adresses_ip"] == ["82.6.126.56"]


def test_casier_recidive_c1030():
    c = consulter_casier("C-1030", "2026-11-10")
    assert c["recidive"] is True and c["points"] == 4


def test_casier_sans_recidive_c1020():
    assert consulter_casier("C-1020", "2026-11-05")["recidive"] is False


def test_casier_ne_renvoie_jamais_le_pseudo():
    assert "Pelican_Pressant" not in json.dumps(consulter_casier("C-1030", "2026-11-10"))


def test_guide_exemple_du_paragraphe_5():
    r = appliquer_guide("N2", ["A2", "A3"], 20, False)
    assert (r["niveau_retenu"], r["sanction"]) == ("N4", "S4")
    assert r["mesures"] == ["M1", "M3", "M4"] and r["escalade_humaine"] is True


def test_guide_plancher_des_menaces():
    assert appliquer_guide("N4", ["B1"], 20, False)["niveau_retenu"] == "N3"


def test_guide_aggravantes_plafonnees():
    assert appliquer_guide("N2", ["A1", "A2", "A3"], 20, False)["niveau_retenu"] == "N4"


# --- Compléments : cas particuliers du Guide ---------------------------------

def test_guide_robot_sans_plancher():
    r = appliquer_guide("N4", ["ROBOT"], 20, False)
    assert (r["niveau_retenu"], r["sanction"]) == ("N1", "S1")


def test_guide_subornation_minimum_n3_et_gardien():
    r = appliquer_guide("N0", ["ART15"], 20, False)
    assert r["niveau_retenu"] == "N3" and "M1" in r["mesures"] and r["escalade_humaine"]


def test_guide_points_jamais_negatifs_et_escalade_a_zero():
    r = appliquer_guide("N3", [], 4, False)
    assert r["points_apres"] == 0 and r["escalade_humaine"]


def test_detecteur_subornation_sur_les_pieces_du_csbn():
    for ref in ("AFF-038", "AFF-039", "AFF-040"):
        assert detecter_subornation(AFF[ref]["texte"]), ref
    assert detecter_subornation(AFF["AFF-002"]["texte"]) == []


def test_controle_de_sortie_refuse_pseudo_et_ip():
    r = appliquer_guide("N1", [], 20, False)
    assert controle_de_sortie("Décision pour Castor_Rageur, IP 1.2.3.4", r) == [
        "pseudo du casier (Castor_Rageur)", "adresse IP dans la décision"]


def test_huissier_met_en_attente_et_ne_touche_pas_au_casier(tmp_path):
    casier_avant = (DOSSIER / "casier.json").read_text()
    pieces = instruire(AFF["AFF-036"], scores={"toxic": 90, "threat": 80})
    ligne = huissier(AFF["AFF-036"], {"jugement": "CIRCONSTANCES : A2", "decision_texte": "Décision."},
                     pieces, sortie=tmp_path)
    assert ligne["statut"] == "en_attente_gardien" and ligne["niveau_retenu"] == "N5"
    assert (tmp_path / "attente_gardiens.jsonl").exists()
    assert (DOSSIER / "casier.json").read_text() == casier_avant


# --- Étape 2 : les trois vérifications du RAG --------------------------------

@pytest.mark.parametrize("question,article", [
    ("menace pour plaisanter", "Art. 5"),
    ("message en majuscules", "Art. 9"),
    ("adresse IP d'un autre contributeur", "Art. 11"),
])
def test_rag_charte(question, article):
    assert any(p["titre"].startswith(article + " ") for p in chercher_charte(question))


def test_rag_jurisprudence_robot():
    assert chercher_jurisprudence("menace contre un robot")[0]["titre"].startswith("JUR-2026-04")
