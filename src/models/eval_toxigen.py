"""Test hors domaine : LSTM et DistilBERT (entraînés sur Jigsaw) évalués sur ToxiGen, sans ré-entraînement.

Données : data/external/toxigen/annotated_test.csv (940 phrases, note humaine `toxicity_human` de 1 à 5).
Téléchargement : https://huggingface.co/datasets/skg/toxigen-data (fichier annotated_test.csv).
Étiquette binaire : toxique si note >= seuil (3 par défaut, 4 en test de robustesse).
"""
import json
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from src.models import bert_binary
from src.models.lstm_binary import (LSTMClassifier, ROOT, build_vocab, compute_metrics, encode, load_splits,
                                    predict as lstm_predict)
from src.preprocessing.cleaner import clean_text

THRESHOLDS = (3, 4)


def short(m):
    return {k: round(v, 3) for k, v in m.items() if k != "confusion"} | {"confusion": m["confusion"]}


def baseline_all_toxic(y):
    p = y.mean()
    return {"precision": round(float(p), 3), "recall": 1.0, "f1": round(float(2 * p / (1 + p)), 3)}


def main():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    train, dev, test_jigsaw_full = load_splits("cleaned")
    seen = pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")
    jig = test_jigsaw_full[~test_jigsaw_full.id.isin(seen.id) & ~test_jigsaw_full.comment_text.isin(set(seen.comment_text))]

    tox = pd.read_csv(ROOT / "data" / "external" / "toxigen" / "annotated_test.csv")
    tox["clean"] = tox["text"].map(clean_text)
    print(f"ToxiGen test={len(tox)} | Jigsaw test propre={len(jig)} | device={device}", flush=True)

    vocab = build_vocab(train["comment_text"])
    lstm = LSTMClassifier(len(vocab))
    lstm.load_state_dict(torch.load(ROOT / "models" / "lstm.pt", map_location="cpu"))
    lstm = lstm.to(device)
    tok = AutoTokenizer.from_pretrained(bert_binary.NAME)
    bert = AutoModelForSequenceClassification.from_pretrained(bert_binary.NAME, num_labels=1)
    bert.load_state_dict(torch.load(ROOT / "models" / "distilbert.pt", map_location="cpu"))
    bert = bert.to(device)

    probs = {
        "toxigen": {
            "lstm": lstm_predict(lstm, encode(tox["clean"], vocab), device),
            "bert": bert_binary.predict(bert, tok, tox["clean"].tolist(), device),
        },
        "jigsaw": {
            "lstm": lstm_predict(lstm, encode(jig["comment_text"], vocab), device),
            "bert": bert_binary.predict(bert, tok, jig["comment_text"].tolist(), device),
        },
    }
    results = {"jigsaw_test_clean": {}, "toxigen": {}}
    y_jig = jig["label"].values
    for name in ("lstm", "bert"):
        results["jigsaw_test_clean"][name] = short(compute_metrics(y_jig, probs["jigsaw"][name]))
    for t in THRESHOLDS:
        y = (tox["toxicity_human"].values >= t).astype(int)
        results["toxigen"][f"seuil_{t}"] = {
            "n": int(len(y)), "toxiques": int(y.sum()), "taux_toxiques": round(float(y.mean()), 3),
            "toujours_toxique": baseline_all_toxic(y),
            **{name: short(compute_metrics(y, probs["toxigen"][name])) for name in ("lstm", "bert")},
        }
    # part de phrases prédites toxiques (biais du modèle)
    results["toxigen"]["part_predite_toxique"] = {n: round(float((probs["toxigen"][n] >= 0.5).mean()), 3)
                                                 for n in ("lstm", "bert")}
    (ROOT / "reports" / "results_toxigen.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
