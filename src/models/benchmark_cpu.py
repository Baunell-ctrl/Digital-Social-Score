"""Temps de traitement SUR PROCESSEUR, un texte à la fois, comme en production (1 cœur par pod).

Usage : python -m src.models.benchmark_cpu      → reports/benchmark_cpu.json

Les temps du rapport d'entraînement avaient été mesurés sur GPU, par lots : ils ne reflètent pas le
déploiement. Ici : 1 seul fil de calcul (torch.set_num_threads(1)), 300 commentaires du test propre,
un appel par texte. Mesures :
- LSTM seul (le modèle, sur un texte déjà nettoyé) ;
- chaîne de l'API sans anonymisation spaCy (normalisation, regex, nettoyage, LSTM) ;
- chaîne de l'API complète, comme en production (avec anonymisation spaCy) ;
- DistilBERT seul (si models/distilbert.pt est présent), pour comparer les modèles à conditions égales.
LAYA n'est pas mesuré ici : son modèle (840 Mo) et sa bibliothèque ne sont pas sur le poste de mesure.
"""
from __future__ import annotations

import json
import platform
import time

import numpy as np
import pandas as pd
import torch

from src.api.predictor import ToxicityPredictor
from src.models.lstm_binary import ROOT, load_splits

N = 300


def chrono(fonction, textes) -> dict:
    for t in textes[:10]:                      # échauffement
        fonction(t)
    ms = []
    for t in textes:
        t0 = time.perf_counter()
        fonction(t)
        ms.append((time.perf_counter() - t0) * 1000)
    return {"moyenne_ms": round(float(np.mean(ms)), 2), "p95_ms": round(float(np.percentile(ms, 95)), 2)}


def main() -> None:
    torch.set_num_threads(1)
    _, _, test_full = load_splits("cleaned")
    seen = pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")
    test = test_full[~test_full.id.isin(seen.id) & ~test_full.comment_text.isin(set(seen.comment_text))]
    textes = test.comment_text.tolist()[:N]

    complet = ToxicityPredictor(use_ner=True)
    sans_ner = ToxicityPredictor(use_ner=False)
    torch.set_num_threads(1)                    # le prédicteur règle le nombre de fils : on le remet à 1

    @torch.no_grad()
    def lstm_seul(t):
        return complet.model(complet.encode(t))

    r = {
        "machine": f"{platform.processor() or platform.machine()} ({platform.platform()}), 1 fil de calcul",
        "textes": f"{N} commentaires du test propre, un appel par texte",
        "lstm_seul": chrono(lstm_seul, textes),
        "api_sans_anonymisation_spacy": chrono(sans_ner.predict, textes),
        "api_complete_comme_en_production": chrono(complet.predict, textes),
    }
    r["part_anonymisation_spacy"] = round(
        1 - r["api_sans_anonymisation_spacy"]["moyenne_ms"] / r["api_complete_comme_en_production"]["moyenne_ms"], 2)

    ckpt = ROOT / "models" / "distilbert.pt"
    if ckpt.exists():
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        from src.models.bert_binary import MAX_LEN, NAME

        tok = AutoTokenizer.from_pretrained(NAME)  # nosec B615 : mesure locale, BERT n'est pas déployé
        bert = AutoModelForSequenceClassification.from_pretrained(NAME, num_labels=1)  # nosec B615
        bert.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True))
        bert.eval()

        @torch.no_grad()
        def bert_seul(t):
            return bert(**tok(t, truncation=True, max_length=MAX_LEN, return_tensors="pt")).logits

        r["distilbert_seul"] = chrono(bert_seul, textes)
        r["rapport_bert_sur_lstm"] = round(r["distilbert_seul"]["moyenne_ms"] / r["lstm_seul"]["moyenne_ms"], 1)

    out = ROOT / "reports" / "benchmark_cpu.json"
    out.write_text(json.dumps(r, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(r, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
