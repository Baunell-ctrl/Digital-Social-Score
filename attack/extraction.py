"""Attaque par extraction (vol) du modèle, et mesure des défenses (cours 5, § 17).

    python attack/extraction.py            # écrit reports/attack/extraction.json

L'attaquant ne voit pas le modèle : il envoie des textes à l'API, récupère les scores, et entraîne son
propre modèle « clone » (TF-IDF + régression) sur ces réponses. On mesure la fidélité du clone sur le test
propre (1 308 commentaires) : part des décisions toxique / non toxique identiques à celles du vrai modèle.

Deux défenses sont comparées :
- score exact (1 décimale) contre score arrondi par paliers de 5 (SCORE_STEP de l'API) ;
- nombre de requêtes autorisé (le quota quotidien par client borne ce nombre).

Les réponses de l'API sont simulées en local avec le même prédicteur que l'API (sans NER, pour la vitesse),
et la même fonction d'arrondi que l'API (src.api.main.round_score).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("API_KEY", "simulation")
from src.api.main import round_score  # noqa: E402
from src.api.predictor import ToxicityPredictor  # noqa: E402
from src.models.lstm_binary import load_splits  # noqa: E402

SEED = 42
BUDGETS = (500, 2000, 10000, 25000)


def main() -> None:
    train, _, test_full = load_splits("cleaned")
    seen = pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")
    test = test_full[~test_full.id.isin(seen.id) & ~test_full.comment_text.isin(set(seen.comment_text))]
    cible = ToxicityPredictor(use_ner=False)

    # Textes de l'attaquant : des commentaires publics (ici ceux du train, mélangés), sans leurs étiquettes.
    requetes = train.comment_text.sample(frac=1, random_state=SEED).tolist()[: max(BUDGETS)]
    proba = np.array([cible.predict(t) for t in requetes])
    vrai_test = np.array([cible.predict(t) for t in test.comment_text]) >= 0.5

    resultats = {"jeu_evaluation": f"test propre, {len(test)} commentaires", "fidelite_du_clone": {}}
    for nom, sortie in (("score_exact", lambda p: round_score(p, step=0)),
                        ("score_arrondi_5", lambda p: round_score(p, step=5))):
        y = np.array([sortie(p) for p in proba])
        ligne = {}
        for n in BUDGETS:
            vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
            clone = Ridge(alpha=1.0).fit(vec.fit_transform(requetes[:n]), y[:n])
            pred = clone.predict(vec.transform(test.comment_text)) >= 50
            ligne[str(n)] = round(float((pred == vrai_test).mean()), 3)
        resultats["fidelite_du_clone"][nom] = ligne
    out = ROOT / "reports" / "attack" / "extraction.json"
    out.write_text(json.dumps(resultats, indent=2, ensure_ascii=False))
    print(json.dumps(resultats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
