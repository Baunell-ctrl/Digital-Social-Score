"""Évalue le LSTM DÉPLOYÉ (artifacts/) sur le test propre et écrit le résultat dans reports/results_lstm.json.

Usage : python -m src.models.evaluate_lstm

Test propre : les commentaires de val_dataset.csv absents du train (ni même identifiant, ni même texte),
soit 1 308 commentaires, le même découpage que BERT et LAYA. Le pipeline est celui de l'API
(ToxicityPredictor : anonymisation, nettoyage, LSTM), donc le chiffre publié est celui du modèle en service.
"""
import json

import numpy as np
import pandas as pd

from src.api.predictor import ToxicityPredictor
from src.models.lstm_binary import ROOT, compute_metrics, load_splits

RESULTS = ROOT / "reports" / "results_lstm.json"


def main() -> None:
    _, _, test_full = load_splits("cleaned")
    seen = pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")
    test = test_full[~test_full.id.isin(seen.id) & ~test_full.comment_text.isin(set(seen.comment_text))]
    predictor = ToxicityPredictor(use_ner=True)
    proba = np.array([predictor.predict(t) for t in test.comment_text])

    results = json.loads(RESULTS.read_text())
    if "test" in results:  # ancien nom : le val complet, qui recoupe le train (5 182 commentaires communs)
        results["test_full"] = results.pop("test")
    results["test_clean"] = {**compute_metrics(test["label"].values, proba), "n": len(test),
                             "modele": "artifacts/lstm.pt (modèle déployé), pipeline de l'API"}
    RESULTS.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(results["test_clean"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
