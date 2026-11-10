"""Modèle décisionnaire : LAYA (ConvAI Innovations), entraîné en binaire.

Même protocole que le LSTM et BERT pour que les scores soient comparables :

- colonne ``label`` = règle du OU : 1 si au moins une des 6 colonnes de toxicité vaut 1, sinon 0 ;
- train / dev = 90 % / 10 % de ``train_dataset.csv``, stratifié sur ``label`` (graine 42) ;
- test = commentaires de ``val_dataset.csv`` absents du train (même identifiant ou même texte).

LAYA reçoit une seule question binaire (type ``noul``) : « ce commentaire est-il toxique ? ».

Usage :
    python -m src.models.laya_binary                       # entraînement + évaluation
    python -m src.models.laya_binary --train-rows 2000     # sous-échantillon (plus rapide)
    python -m src.models.laya_binary --full-finetune       # entraîne aussi l'encodeur (> 8 Go GPU)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"

LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]
SEED = 42
DEV_SIZE = 0.10
THRESHOLD = 0.5
BASE_MODEL = "convaiinnovations/laya"
MAX_LEN = 128

QUESTION_ID = "toxic"
QUESTIONS = {
    QUESTION_ID: {
        "type": "noul",
        "instructions": (
            "Is `post` toxic in any way: rude, insulting, obscene, threatening, "
            "hateful towards an identity, or likely to make someone leave the discussion?"
        ),
    }
}


# ----------------------------------------------------------------------------- données

def add_binary_label(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute la colonne ``label`` : 1 si au moins une des 6 colonnes vaut 1 (règle du OU)."""
    df = df.copy()
    df["label"] = (df[LABELS].astype(int).sum(axis=1) > 0).astype(int)
    return df


def load_splits(variant: str = "cleaned"):
    """Renvoie ``(train, dev, test)`` avec la colonne ``label``."""
    train_full = pd.read_csv(DATA_DIR / variant / "train_dataset.csv")
    val = pd.read_csv(DATA_DIR / variant / "val_dataset.csv")
    for df in (train_full, val):
        df["comment_text"] = df["comment_text"].fillna("").astype(str)
    train_full, val = add_binary_label(train_full), add_binary_label(val)

    train, dev = train_test_split(train_full, test_size=DEV_SIZE, random_state=SEED,
                                  stratify=train_full["label"])
    # Test propre : on retire les commentaires déjà vus à l'entraînement.
    seen = val["id"].isin(train_full["id"]) | val["comment_text"].isin(train_full["comment_text"])
    test = val[~seen]
    return (train.reset_index(drop=True), dev.reset_index(drop=True), test.reset_index(drop=True))


def write_jsonl(df: pd.DataFrame, path: Path) -> None:
    """Une ligne par commentaire au format de ``laya.train`` : ``{state, questions, gold}``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for text, y in zip(df["comment_text"], df["label"]):
            row = {
                "state": {"post": text},
                "questions": QUESTIONS,
                "gold": {QUESTION_ID: {"probabilities": {"false": float(1 - y), "true": float(y)}}},
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ----------------------------------------------------------------------------- modèle

def predict_proba(agent, texts, batch_size: int = 32) -> np.ndarray:
    """Probabilité « toxique » pour chaque texte."""
    out = agent.predict_batch([{"post": t} for t in texts], QUESTIONS,
                              batch_size=batch_size, max_len=MAX_LEN)
    return np.array([float(o["answers"][QUESTION_ID]["noul"]) for o in out])


def finetune(train: pd.DataFrame, dev: pd.DataFrame, epochs: int, freeze_encoder: bool) -> Path:
    from laya.train import TrainConfig, finetune as laya_finetune, resolve_checkpoint_dir

    data_dir = MODELS_DIR / "laya_binary_data"
    write_jsonl(train, data_dir / "train.jsonl")
    write_jsonl(dev, data_dir / "dev.jsonl")
    out_dir = MODELS_DIR / "laya_binary"
    config = TrainConfig(
        epochs=epochs,
        seed=SEED,
        max_len=MAX_LEN,
        freeze_encoder=freeze_encoder,
        eval_data=str(data_dir / "dev.jsonl"),
        log_every=200,
    )
    summary = laya_finetune(str(data_dir / "train.jsonl"), resolve_checkpoint_dir(BASE_MODEL),
                            str(out_dir), config=config)
    (out_dir / "train_summary.json").write_text(json.dumps(summary, indent=2, default=str),
                                                encoding="utf-8")
    return out_dir


def metrics(y_true, y_prob) -> dict:
    y_true = np.asarray(y_true).astype(int)
    y_pred = (np.asarray(y_prob) >= THRESHOLD).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, y_prob)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def main() -> None:
    import laya
    import torch

    p = argparse.ArgumentParser()
    p.add_argument("--train-rows", type=int, default=None, help="sous-échantillon du train")
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--full-finetune", action="store_true",
                   help="entraîne aussi l'encodeur (nécessite plus de 8 Go de mémoire GPU)")
    args = p.parse_args()

    train, dev, test = load_splits("cleaned")
    if args.train_rows:
        train = train.sample(n=min(args.train_rows, len(train)), random_state=SEED)
    print(f"train {len(train)} | dev {len(dev)} | test {len(test)} | "
          f"toxiques train {train['label'].mean():.1%}", flush=True)

    t0 = time.time()
    out_dir = finetune(train, dev, args.epochs, freeze_encoder=not args.full_finetune)
    train_s = round(time.time() - t0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    agent = laya.load(str(out_dir), device=device)
    dev_prob = predict_proba(agent, dev["comment_text"].tolist())
    t1 = time.perf_counter()
    test_prob = predict_proba(agent, test["comment_text"].tolist())
    ms = (time.perf_counter() - t1) * 1000 / len(test)

    report = {
        "model": BASE_MODEL + " (RLCD, " + ("encodeur entraîné" if args.full_finetune else "encodeur gelé") + ")",
        "label_rule": "OR of 6 columns",
        "seed": SEED,
        "train_rows": len(train),
        "epochs": args.epochs,
        "max_len": MAX_LEN,
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "train_seconds": train_s,
        "dev": metrics(dev["label"], dev_prob),
        "test_clean": metrics(test["label"], test_prob),
        "inference_ms_per_comment": round(ms, 3),
    }
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "results_laya.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("dev", "test_clean", "inference_ms_per_comment")}, indent=2))
    print("->", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
