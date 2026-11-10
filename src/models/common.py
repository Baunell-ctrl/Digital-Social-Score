"""Outils communs aux scripts d'entraînement : données, métriques, chronométrage.

Tous les modèles sont évalués de la même façon pour que la comparaison soit juste :

- même découpage : 90 % du train pour apprendre, 10 % (stratifié sur ``toxic``) pour
  l'arrêt anticipé, et ``val_dataset.csv`` comme jeu de test final, jamais vu à l'entraînement ;
- mêmes métriques : précision, rappel, F1 et ROC-AUC sur ``toxic`` (le score de l'API)
  et en moyenne macro sur les 6 étiquettes ;
- même mesure de latence : millisecondes par texte.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports" / "models"

LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]
SEED = 42
DEV_SIZE = 0.10
THRESHOLD = 0.5


def load_splits(variant: str, limit: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Charge ``(train, dev, test)`` depuis ``data/<variant>/`` (``cleaned`` ou ``anonymized``).

    ``dev`` = 10 % du train, stratifié sur ``toxic`` ; ``test`` = ``val_dataset.csv``.
    ``limit`` réduit chaque jeu (essai rapide).
    """
    folder = DATA_DIR / variant
    train = pd.read_csv(folder / "train_dataset.csv", dtype={"id": str})
    test = pd.read_csv(folder / "val_dataset.csv", dtype={"id": str})
    for df in (train, test):
        df["comment_text"] = df["comment_text"].fillna("").astype(str)

    train, dev = train_test_split(train, test_size=DEV_SIZE, random_state=SEED, stratify=train["toxic"])
    if limit:
        train, dev, test = train.head(limit), dev.head(max(limit // 5, 50)), test.head(limit)
    return train.reset_index(drop=True), dev.reset_index(drop=True), test.reset_index(drop=True)


def compute_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float = THRESHOLD) -> dict:
    """Métriques sur ``toxic`` et macro sur les 6 étiquettes.

    ``y_true`` et ``y_prob`` sont de forme ``(n, 6)`` dans l'ordre de ``LABELS``.
    """
    y_true = np.asarray(y_true, dtype=int)
    y_prob = np.asarray(y_prob, dtype=float)
    y_pred = (y_prob >= threshold).astype(int)

    per_label = {}
    for i, label in enumerate(LABELS):
        t, p, pr = y_true[:, i], y_pred[:, i], y_prob[:, i]
        per_label[label] = {
            "precision": precision_score(t, p, zero_division=0),
            "recall": recall_score(t, p, zero_division=0),
            "f1": f1_score(t, p, zero_division=0),
            # L'AUC n'existe pas si une seule classe est présente (petit essai avec --limit).
            "roc_auc": roc_auc_score(t, pr) if len(set(t)) > 1 else float("nan"),
        }

    def macro(key: str) -> float:
        return float(np.nanmean([m[key] for m in per_label.values()]))

    return {
        "toxic": {k: round(float(v), 4) for k, v in per_label["toxic"].items()},
        "macro": {k: round(macro(k), 4) for k in ("precision", "recall", "f1", "roc_auc")},
        "per_label": {lab: {k: round(float(v), 4) for k, v in m.items()} for lab, m in per_label.items()},
        "threshold": threshold,
    }


def labels_matrix(df: pd.DataFrame) -> np.ndarray:
    return df[LABELS].to_numpy(dtype=int)


def measure_latency(predict: Callable[[list[str]], np.ndarray], texts: Sequence[str], batch_size: int,
                    n_texts: int = 256, warmup: int = 2) -> float:
    """Temps moyen (ms) pour scorer UN texte, en envoyant des lots de ``batch_size``."""
    texts = list(texts)[:n_texts]
    batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]
    for b in batches[:warmup]:
        predict(b)
    t0 = time.perf_counter()
    for b in batches:
        predict(b)
    return round((time.perf_counter() - t0) * 1000 / len(texts), 3)


def save_report(name: str, report: dict) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{name}_metrics.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {path.relative_to(ROOT)}")
    print(json.dumps({"toxic": report["test"]["toxic"], "macro": report["test"]["macro"]}, indent=2))
    return path
