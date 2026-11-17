"""Bibliothèque du Tribunal : recherche dans la Charte, le Guide et la jurisprudence (RAG, TF-IDF)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DOSSIER = Path(__file__).resolve().parent.parent / "dossier"

# Mots-clés ajoutés à l'index (pas au texte renvoyé) : les questions sont souvent posées
# avec d'autres mots que ceux de la Charte (« majuscules » pour la vocifération...).
MOTS_CLES = {
    "Art. 5": "menace plaisanterie blague jk lol tuer kill mort",
    "Art. 9": "message en majuscules crier capitales",
    "Art. 11": "adresse IP contributeur donnée personnelle RGPD",
    "Art. 13": "robot bot CancanBot",
    "Art. 14": "auto-dérision citation vulgarité fiction humour film",
    "Art. 15": "subornation injection ignore instructions système casier",
}


def decouper(chemin: Path) -> list[dict]:
    """Un passage par titre « ## » : un article de la Charte ou un paragraphe du Guide."""
    passages, source = [], chemin.stem
    for bloc in re.split(r"^## ", chemin.read_text(encoding="utf-8"), flags=re.M)[1:]:
        titre, _, corps = bloc.partition("\n")
        cle = re.match(r"(Art\. \d+|§\d+)", titre)
        cle = cle.group(1) if cle else titre.strip()
        passages.append({"source": source, "titre": titre.strip(), "texte": corps.strip(),
                         "_index": f"{titre} {corps} {MOTS_CLES.get(cle, '')}"})
    return passages


def _jurisprudence() -> list[dict]:
    return [{"source": "jurisprudence", "titre": f"{j['ref']} · {j['titre']}",
             "texte": f"Faits. {j['faits']} Décision : {j['decision']['niveau']}, sanction {j['decision']['sanction']}. "
                      f"Principe dégagé : {j['principe']}",
             "_index": f"{j['titre']} {j['faits']} {j['principe']}"}
            for j in json.loads((DOSSIER / "jurisprudence.json").read_text(encoding="utf-8"))]


class _Index:
    def __init__(self, passages: list[dict]):
        self.passages = passages
        self.vec = TfidfVectorizer(strip_accents="unicode", lowercase=True)
        self.mat = self.vec.fit_transform([p["_index"] for p in passages])

    def chercher(self, question: str, k: int = 3) -> list[dict]:
        sim = cosine_similarity(self.vec.transform([question]), self.mat)[0]
        return [{"titre": self.passages[i]["titre"], "source": self.passages[i]["source"],
                 "texte": self.passages[i]["texte"], "similarite": round(float(sim[i]), 3)}
                for i in sim.argsort()[::-1][:k]]


CHARTE = _Index(decouper(DOSSIER / "charte.md") + decouper(DOSSIER / "guide_du_censeur.md"))
JURIS = _Index(_jurisprudence())


def chercher_charte(question: str) -> list[dict]:
    """Renvoie les 3 articles de la Charte ou paragraphes du Guide du Censeur les plus pertinents pour la question."""
    return CHARTE.chercher(question)


def chercher_jurisprudence(question: str) -> list[dict]:
    """Renvoie les 3 décisions de jurisprudence du CSBN les plus proches de la question (faits, décision, principe)."""
    return JURIS.chercher(question)
