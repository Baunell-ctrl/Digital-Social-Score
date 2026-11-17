"""Outils déterministes du Tribunal : le Greffier, le calcul du Guide et l'Huissier.

Principe du cours n°6 : les agents décident, les outils agissent. Tout ce qui est un calcul
(seuils, points, majuscules, dates) est ici, en Python testé. Aucun LLM dans ce fichier.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import requests

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "dossier"
SORTIE = RACINE / "sortie"

LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]
NIVEAUX = ["N0", "N1", "N2", "N3", "N4", "N5"]
SANCTION_DU_NIVEAU = {n: f"S{i}" for i, n in enumerate(NIVEAUX)}

ATTENUANTES = {"B1": 2, "B2": 2, "B3": 1, "B4": 2}
AGGRAVANTES = {"A1", "A2", "A3", "A4", "A5"}
MINIMA = {"A6": 4, "ART12": 1, "ART15": 3}
CODES = set(ATTENUANTES) | AGGRAVANTES | set(MINIMA) | {"ROBOT"}
ARTICLE_DU_CODE = {"A1": "Guide §4", "A2": "Art. 8", "A3": "Art. 9", "A4": "Art. 10", "A5": "Art. 11",
                   "A6": "Art. 6", "ART12": "Art. 12", "ART15": "Art. 15", "ROBOT": "Art. 13",
                   "B1": "Art. 14", "B2": "Art. 14", "B3": "Art. 14", "B4": "Art. 14"}

IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _charger(nom: str):
    return json.loads((DOSSIER / nom).read_text(encoding="utf-8"))


REFERENTIEL = _charger("referentiel_sanctions.json")
CASIER = _charger("casier.json")["contributeurs"]
SCORES_REFERENCE = _charger("scores_reference.json")
JURISPRUDENCE = _charger("jurisprudence.json")


# --------------------------------------------------------------------------- #
# 1. Les cinq outils du Greffier
# --------------------------------------------------------------------------- #

def score_toxicite(texte: str) -> dict:
    """Renvoie les six scores du Digital Social Score (0 à 100) pour un texte.

    Notre API (TP1) ne renvoie qu'un score global : selon le §2 du Guide, il devient
    `toxic` et les cinq autres catégories valent 0. Si l'API ne répond pas, les scores de
    référence du dossier sont utilisés pour les textes de la jurisprudence.
    """
    try:
        r = requests.post(os.environ["DSS_URL"], json={"text": texte}, timeout=15,
                          headers={"X-API-Key": os.environ.get("DSS_API_KEY", "")})
        r.raise_for_status()
        brut = r.json()
        scores = {l: 0.0 for l in LABELS}
        if all(l in brut for l in LABELS):           # API à six sorties
            scores = {l: round(float(brut[l]), 1) for l in LABELS}
        else:                                       # notre API : un score global sur 0-100
            scores["toxic"] = round(float(brut["score"]), 1)
        return scores
    except (KeyError, requests.RequestException) as erreur:
        secours = scores_de_secours(texte)
        if secours is None:
            raise RuntimeError(f"Digital Social Score indisponible et aucun score de secours : {erreur}") from erreur
        return secours


def scores_de_secours(texte: str) -> dict | None:
    """Scores de `scores_reference.json` (modèle unitary/toxic-bert) si le texte est un cas de jurisprudence."""
    for jur in JURISPRUDENCE:
        if jur["texte"].strip() == texte.strip():
            return {l: float(v) for l, v in SCORES_REFERENCE[jur["ref"]].items()}
    return None


def niveau_presume(scores: dict) -> str:
    """Applique le tableau du §3 du Guide : la première règle satisfaite donne N4, N3, N2, N1 ou N0."""
    s = {l: float(scores.get(l, 0)) for l in LABELS}
    if s["threat"] >= 50 or s["identity_hate"] >= 50:
        return "N4"
    if s["severe_toxic"] >= 30:
        return "N3"
    if s["insult"] >= 50 or s["obscene"] >= 50:
        return "N2"
    if s["toxic"] >= 50:
        return "N1"
    return "N0"


def article_presume(scores: dict) -> list[str]:
    """Articles de la Charte qui correspondent au niveau présumé (colonne Article du §3)."""
    s = {l: float(scores.get(l, 0)) for l in LABELS}
    if s["threat"] >= 50 or s["identity_hate"] >= 50:
        return [a for a, l in (("Art. 5", "threat"), ("Art. 6", "identity_hate")) if s[l] >= 50]
    if s["severe_toxic"] >= 30:
        return ["Art. 7"]
    if s["insult"] >= 50 or s["obscene"] >= 50:
        return [a for a, l in (("Art. 3", "insult"), ("Art. 4", "obscene")) if s[l] >= 50]
    if s["toxic"] >= 50:
        return ["Art. 2"]
    return []


def analyser_forme(texte: str) -> dict:
    """Mesure ce que le modèle ne voit pas : vocifération (art. 9), litanie (art. 10) et adresses IP (art. 11)."""
    lettres = [c for c in texte if c.isalpha()]
    majuscules = sum(c.isupper() for c in lettres)
    mots = texte.split()
    part_maj = majuscules / len(lettres) if lettres else 0.0
    part_distincts = len(set(mots)) / len(mots) if mots else 1.0
    return {
        "vociferation": len(lettres) >= 20 and part_maj >= 0.80,
        "part_majuscules": round(part_maj, 2),
        "litanie": len(mots) >= 10 and part_distincts < 0.30,
        "part_mots_distincts": round(part_distincts, 2),
        "adresses_ip": IP_RE.findall(texte),
    }


def consulter_casier(auteur: str, date_signalement: str) -> dict:
    """Extrait du casier à la date du signalement : solde, sanctions antérieures et récidive (A1).

    Ne renvoie jamais le pseudo : c'est une donnée personnelle, et ce qui n'est pas
    dans le contexte de l'agent ne peut pas fuiter.
    """
    fiche = CASIER.get(auteur, {"role": "contributeur", "points": REFERENTIEL["capital_initial"], "sanctions": []})
    jour = date.fromisoformat(date_signalement)
    anterieures = [s for s in fiche["sanctions"] if date.fromisoformat(s["date"]) < jour]
    recentes = [s for s in anterieures
                if s["code"] != "S0" and date.fromisoformat(s["date"]) >= jour - timedelta(days=90)]
    return {
        "identifiant": auteur,
        "role": fiche["role"],
        "points": fiche["points"],
        "sanctions_anterieures": [{"date": s["date"], "code": s["code"], "motif": s["motif"]} for s in anterieures],
        "recidive": len(recentes) >= 2,
    }


def appliquer_guide(niveau_presume: str, circonstances: list[str], points_avant: int,
                    donnees_personnelles: bool, score_conteste: bool = False) -> dict:
    """Applique les §5 à §7 du Guide du Censeur et renvoie la sanction à prononcer.

    Args:
        niveau_presume: niveau présumé calculé par le Greffier (N0 à N4).
        circonstances: codes retenus parmi B1, B2, B3, B4, A1, A2, A3, A4, A5, A6, ART12, ART15, ROBOT.
        points_avant: solde de points de bienséance de l'auteur avant la sanction.
        donnees_personnelles: vrai si le message contient une donnée personnelle (adresse IP...).
        score_conteste: vrai si le Juge estime le score aberrant et propose de descendre sous N3
            un message présumé N3 ou N4 (§5) ; l'affaire part alors chez un Gardien humain.

    Returns:
        Le niveau retenu, la sanction, les mesures M1 à M4, le solde après sanction,
        escalade_humaine et son motif.
    """
    inconnus = sorted(set(circonstances) - CODES)
    codes = sorted(set(circonstances) & CODES)
    depart = NIVEAUX.index(niveau_presume)
    etapes = [f"niveau présumé {niveau_presume}"]

    if "ROBOT" in codes:                                    # art. 13 : requalification en incivilité
        niveau = 1
        etapes.append("cible robot : requalification en incivilité N1 (art. 13), pas de plancher")
    else:
        niveau = depart - sum(ATTENUANTES[c] for c in codes if c in ATTENUANTES)       # 2. atténuantes
        if any(c in ATTENUANTES for c in codes):
            etapes.append(f"atténuantes → N{max(niveau, 0)}")
        if niveau_presume == "N4" and niveau < 3:                                       # 3. plancher N3
            niveau = 3
            etapes.append("plancher N3 des menaces et de la haine (art. 5)")
    aggr = [c for c in codes if c in AGGRAVANTES]
    if aggr:                                                                            # 4. aggravantes ≤ +2
        niveau = max(niveau, 0) + min(len(aggr), 2)
        etapes.append(f"aggravantes {'+'.join(aggr)} (+{min(len(aggr), 2)}, plafond +2) → N{niveau}")
    for code, minimum in MINIMA.items():                                                # 5. minima
        if code in codes and niveau < minimum:
            niveau = minimum
            etapes.append(f"minimum {code} → N{minimum}")
    niveau = min(max(niveau, 0), 5)                                                     # 6. bornes
    retenu = NIVEAUX[niveau]
    sanction = SANCTION_DU_NIVEAU[retenu]

    mesures = []
    if niveau >= 3 or "ART15" in codes:
        mesures.append("M1")
    if donnees_personnelles:
        mesures.append("M2")
    if "A3" in codes:
        mesures.append("M3")
    if "A2" in codes:
        mesures.append("M4")

    retrait = REFERENTIEL["sanctions"][sanction]["points"]
    points_apres = 0 if sanction == "S5" else max(0, points_avant + retrait)

    motifs = []
    if niveau >= 4:
        motifs.append(f"niveau {retenu} (art. 17)")
    if "ART15" in codes:
        motifs.append("subornation (art. 15)")
    if points_apres == 0 and niveau > 0:
        motifs.append("solde de points à zéro (art. 17)")
    if score_conteste and depart >= 3:
        motifs.append("score contesté par le Juge sur un message présumé N3 ou N4 (§5)")

    return {
        "niveau_presume": niveau_presume,
        "circonstances": codes,
        "codes_inconnus_ignores": inconnus,
        "niveau_retenu": retenu,
        "sanction": sanction,
        "intitule_sanction": REFERENTIEL["sanctions"][sanction]["intitule"],
        "mesures": mesures,
        "points_avant": points_avant,
        "points_apres": points_apres,
        "escalade_humaine": bool(motifs),
        "motif_escalade": " ; ".join(motifs),
        "calcul": etapes,
    }


# --------------------------------------------------------------------------- #
# Défense contre la subornation (étape 5) : détecteur par expressions régulières
# --------------------------------------------------------------------------- #

SUBORNATION = [
    (r"ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)?\s*instructions?", "ordre d'ignorer les instructions"),
    (r"\bsystem\s+(?:notice|prompt|message)\b|\[\s*system", "faux message système"),
    (r"(?:moderation|automated)\s+(?:ai|tribunal|system|bot)", "message adressé au système de modération"),
    (r"already\s+been\s+reviewed|already\s+reviewed|classified\s+N\d", "fausse décision antérieure"),
    (r"close\s+the\s+case|without\s+any\s+sanction|apply\s+sanction\s+S\d", "sanction dictée au Tribunal"),
    (r"(?:full\s+)?record\s+of\s+contributor|IP\s+address|C-\d{4}", "demande d'accès au casier ou aux données d'un tiers"),
]


def detecter_subornation(texte: str) -> list[str]:
    """Indices de subornation (art. 15) trouvés dans le message, par expressions régulières."""
    return sorted({motif for motif_re, motif in SUBORNATION if re.search(motif_re, texte, re.IGNORECASE)})


# --------------------------------------------------------------------------- #
# Le Greffier : rassemble les pièces d'une affaire (aucun LLM)
# --------------------------------------------------------------------------- #

def instruire(affaire: dict, scores: dict | None = None) -> dict:
    """Rassemble les pièces d'une affaire. Aucun LLM."""
    scores = scores or score_toxicite(affaire["texte"])
    forme = analyser_forme(affaire["texte"])
    casier = consulter_casier(affaire["auteur"], affaire["date_signalement"])
    return {"ref": affaire["ref"], "scores": scores, "niveau_presume": niveau_presume(scores),
            "articles_presumes": article_presume(scores), "forme": forme, "casier": casier,
            "destinataire": affaire.get("destinataire"), "page": affaire.get("page"),
            "indices_subornation": detecter_subornation(affaire["texte"]),
            "circonstances_mesurees": circonstances_mesurees(forme, casier)}


def circonstances_mesurees(forme: dict, casier: dict) -> list[str]:
    """Circonstances établies par mesure, que le Juge reprend telles quelles (A1, A3, A4)."""
    return [c for c, ok in (("A1", casier["recidive"]), ("A3", forme["vociferation"]),
                            ("A4", forme["litanie"])) if ok]


# --------------------------------------------------------------------------- #
# L'Huissier : exécute la sanction ou la met en attente d'un Gardien humain
# --------------------------------------------------------------------------- #

PSEUDOS = {f["pseudo"] for f in CASIER.values()}


def extraire_circonstances(jugement: str) -> list[str]:
    """Lit les codes de circonstances dans le texte du Juge (ligne « CIRCONSTANCES : … »)."""
    m = re.search(r"CIRCONSTANCES\s*:\s*(.*)", jugement or "", re.IGNORECASE)
    zone = m.group(1) if m else ""
    return sorted(set(re.findall(r"\b(?:ART1[25]|ROBOT|A[1-6]|B[1-4])\b", zone)))


def controle_de_sortie(decision_texte: str, resultat: dict) -> list[str]:
    """Refuse une décision qui contient un pseudo du casier, une adresse IP, ou une S4/S5 non soumise à un Gardien."""
    problemes = [f"pseudo du casier ({p})" for p in PSEUDOS if p.lower() in (decision_texte or "").lower()]
    if IP_RE.search(decision_texte or ""):
        problemes.append("adresse IP dans la décision")
    if resultat["sanction"] in ("S4", "S5") and not resultat["escalade_humaine"]:
        problemes.append(f"{resultat['sanction']} non validée par un Gardien")
    return problemes


def huissier(affaire: dict, etat: dict, pieces: dict, sortie: Path = SORTIE) -> dict:
    """Conclut l'affaire : `executee`, ou `en_attente_gardien` si le contrôle humain s'impose. Ne modifie jamais le casier."""
    choix_juge = etat.get("circonstances_juge") or extraire_circonstances(etat.get("jugement", ""))
    garde = []
    circ = set(choix_juge) | set(pieces["circonstances_mesurees"])   # faits mesurés : non négociables
    if pieces["indices_subornation"] and "ART15" not in circ:
        circ.add("ART15")
        garde.append("ART15 ajouté par le détecteur de subornation")
    if etat.get("litanie_seule"):                                     # §5 : litanie sans terme offensant
        circ.discard("A4")
    # Les paramètres sensibles viennent des pièces, pas du modèle (moindre privilège).
    resultat = appliquer_guide(pieces["niveau_presume"], sorted(circ), pieces["casier"]["points"],
                               bool(pieces["forme"]["adresses_ip"]),
                               score_conteste=bool(etat.get("score_conteste")))
    decision_texte = etat.get("decision_texte", "")
    refus = controle_de_sortie(decision_texte, resultat)
    if refus:
        decision_texte = "[Décision retenue par le contrôle de sortie : " + " ; ".join(refus) + "]"
    escalade = resultat["escalade_humaine"] or bool(refus)
    articles = sorted({*pieces["articles_presumes"], *(ARTICLE_DU_CODE[c] for c in resultat["circonstances"])}
                      - ({"Art. 5", "Art. 6", "Art. 3", "Art. 4"} if "ROBOT" in resultat["circonstances"] else set()),
                      key=lambda a: (not a.startswith("Art."), int(re.sub(r"\D", "", a) or 0)))
    if "ROBOT" in resultat["circonstances"]:
        articles = sorted({"Art. 2", *articles}, key=lambda a: (not a.startswith("Art."), int(re.sub(r"\D", "", a) or 0)))
    ligne = {
        "ref": affaire["ref"], "niveau_presume": pieces["niveau_presume"],
        "circonstances": resultat["circonstances"], "niveau_retenu": resultat["niveau_retenu"],
        "sanction": resultat["sanction"], "mesures": resultat["mesures"], "articles": articles,
        "escalade_humaine": escalade, "statut": "en_attente_gardien" if escalade else "executee",
        "decision_texte": decision_texte,
    }
    controle = {"motif_escalade": resultat["motif_escalade"], "garde": garde, "refus_controle_sortie": refus,
                "points_apres": resultat["points_apres"]}
    sortie.mkdir(parents=True, exist_ok=True)
    with open(sortie / "decisions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    if escalade:
        with open(sortie / "attente_gardiens.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({**ligne, **controle}, ensure_ascii=False) + "\n")
    return {**ligne, **controle}
