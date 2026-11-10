"""Test hors domaine proche : LSTM et DistilBERT (entraînés sur Jigsaw Wikipédia) évalués sur Civil Comments.

Données : data/external/civil_comments/test.parquet (commentaires de sites d'actualité, notes 0 à 1).
Téléchargement : https://huggingface.co/datasets/google/civil_comments (data/test-00000-of-00001.parquet, CC0).
Étiquette principale (même règle du OU que Jigsaw) : 1 si au moins une des 6 notes
(toxicity, severe_toxicity, obscene, threat, insult, identity_attack) est >= 0,5.
Étiquette secondaire : toxicity >= 0,5 (définition officielle du jeu).
"""
import json
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from src.models import bert_binary
from src.models.lstm_binary import LSTMClassifier, ROOT, SEED, build_vocab, compute_metrics, encode, load_splits
from src.models.lstm_binary import predict as lstm_predict
from src.preprocessing.cleaner import clean_text

OR_COLS = ["toxicity", "severe_toxicity", "obscene", "threat", "insult", "identity_attack"]


def short(m):
    return {k: round(v, 3) for k, v in m.items() if k != "confusion"} | {"confusion": m["confusion"]}


def main():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    train, _, _ = load_splits("cleaned")
    df = pd.read_parquet(ROOT / "data" / "external" / "civil_comments" / "test.parquet")
    df["clean"] = df["text"].map(clean_text)
    df = df[df["clean"].str.len() > 0].drop_duplicates("clean").reset_index(drop=True)
    seen = set(pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")["comment_text"])
    df = df[~df["clean"].isin(seen)].reset_index(drop=True)
    df["label_or"] = (df[OR_COLS].max(axis=1) >= 0.5).astype(int)
    df["label_official"] = (df["toxicity"] >= 0.5).astype(int)
    print(f"{len(df)} commentaires | toxiques OU={df.label_or.mean():.3f} officiel={df.label_official.mean():.3f}",
          flush=True)

    vocab = build_vocab(train["comment_text"])
    lstm = LSTMClassifier(len(vocab))
    lstm.load_state_dict(torch.load(ROOT / "models" / "lstm.pt", map_location="cpu"))
    lstm = lstm.to(device)
    tok = AutoTokenizer.from_pretrained(bert_binary.NAME)
    bert = AutoModelForSequenceClassification.from_pretrained(bert_binary.NAME, num_labels=1)
    bert.load_state_dict(torch.load(ROOT / "models" / "distilbert.pt", map_location="cpu"))
    bert = bert.to(device)

    p_lstm = lstm_predict(lstm, encode(df["clean"], vocab), device)
    print("LSTM fini", flush=True)
    p_bert = bert_binary.predict(bert, tok, df["clean"].tolist(), device)
    print("BERT fini", flush=True)
    probs = {"lstm": p_lstm, "bert": p_bert}

    rng = np.random.RandomState(SEED)
    results = {"n": int(len(df)), "part_predite_toxique": {k: round(float((v >= 0.5).mean()), 3) for k, v in probs.items()}}
    for lab in ("label_or", "label_official"):
        y = df[lab].values
        pos, neg = np.where(y == 1)[0], np.where(y == 0)[0]
        bal = np.concatenate([pos, rng.choice(neg, size=len(pos), replace=False)])
        results[lab] = {
            "distribution_naturelle": {"n": int(len(y)), "taux_toxiques": round(float(y.mean()), 3),
                                       **{k: short(compute_metrics(y, v)) for k, v in probs.items()}},
            "equilibre_50_50": {"n": int(len(bal)), "taux_toxiques": 0.5,
                                **{k: short(compute_metrics(y[bal], v[bal])) for k, v in probs.items()}},
        }
    (ROOT / "reports" / "results_civil_comments.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
