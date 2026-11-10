"""Modèle statistique : BiLSTM entraîné de zéro sur les textes nettoyés.

Usage :  python -m src.models.train_lstm [--limit 500] [--epochs 8]

Architecture : mots -> embedding (128) -> BiLSTM (2 x 128) -> max-pooling -> 6 sorties sigmoïdes.
Le modèle apprend seul ses représentations de mots : il ne connaît que ce qu'il voit dans le train.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .common import (LABELS, MODELS_DIR, SEED, compute_metrics, labels_matrix, load_splits,
                     measure_latency, save_report)

VOCAB_SIZE = 30_000
MAX_LEN = 200
PAD, UNK = 0, 1


class Vocab:
    """Associe chaque mot à un entier (0 = remplissage, 1 = mot inconnu)."""

    def __init__(self, texts: list[str], size: int = VOCAB_SIZE):
        counts = Counter(w for t in texts for w in t.split())
        self.itos = ["<pad>", "<unk>"] + [w for w, _ in counts.most_common(size - 2)]
        self.stoi = {w: i for i, w in enumerate(self.itos)}

    def encode(self, texts: list[str], max_len: int = MAX_LEN) -> torch.Tensor:
        out = np.zeros((len(texts), max_len), dtype=np.int64)
        for i, t in enumerate(texts):
            ids = [self.stoi.get(w, UNK) for w in t.split()[:max_len]]
            out[i, : len(ids)] = ids
        return torch.from_numpy(out)


class BiLSTM(nn.Module):
    def __init__(self, vocab_size: int, emb: int = 128, hidden: int = 128, n_out: int = len(LABELS)):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, emb, padding_idx=PAD)
        self.lstm = nn.LSTM(emb, hidden, batch_first=True, bidirectional=True)
        self.drop = nn.Dropout(0.3)
        self.out = nn.Linear(2 * hidden, n_out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h, _ = self.lstm(self.emb(x))                     # (batch, len, 2*hidden)
        h = h.masked_fill((x == PAD).unsqueeze(-1), -1e4)  # ignore le remplissage
        return self.out(self.drop(h.max(dim=1).values))   # logits (batch, 6)


@torch.no_grad()
def predict_proba(model: nn.Module, x: torch.Tensor, device: str, batch_size: int = 512) -> np.ndarray:
    model.eval()
    probs = [torch.sigmoid(model(x[i : i + batch_size].to(device))).cpu() for i in range(0, len(x), batch_size)]
    return torch.cat(probs).numpy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--patience", type=int, default=2)
    args = parser.parse_args()

    torch.manual_seed(SEED)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    train, dev, test = load_splits("cleaned", args.limit)

    vocab = Vocab(train["comment_text"].tolist())
    x_tr, x_dev, x_te = (vocab.encode(d["comment_text"].tolist()) for d in (train, dev, test))
    y_tr = torch.tensor(labels_matrix(train), dtype=torch.float32)

    model = BiLSTM(len(vocab.itos)).to(device)
    # pos_weight : les étiquettes rares (threat 1,4 %) pèsent plus dans la perte.
    pos = y_tr.mean(0).clamp(min=1e-3)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=((1 - pos) / pos).sqrt().to(device))
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loader = DataLoader(TensorDataset(x_tr, y_tr), batch_size=64, shuffle=True)

    MODELS_DIR.mkdir(exist_ok=True)
    ckpt = MODELS_DIR / "lstm.pt"
    best_auc, bad_epochs, history = -1.0, 0, []
    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for xb, yb in loader:
            opt.zero_grad()
            loss = loss_fn(model(xb.to(device)), yb.to(device))
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(xb)
        dev_auc = compute_metrics(labels_matrix(dev), predict_proba(model, x_dev, device))["macro"]["roc_auc"]
        history.append({"epoch": epoch, "loss": round(total / len(x_tr), 4), "dev_macro_auc": dev_auc})
        print(history[-1])
        if dev_auc > best_auc:
            best_auc, bad_epochs = dev_auc, 0
            torch.save(model.state_dict(), ckpt)
        else:
            bad_epochs += 1
            if bad_epochs >= args.patience:
                break
    train_seconds = time.time() - t0

    model.load_state_dict(torch.load(ckpt, map_location=device, weights_only=True))
    (MODELS_DIR / "lstm_vocab.json").write_text(json.dumps(vocab.itos), encoding="utf-8")

    t0 = time.time()
    probs = predict_proba(model, x_te, device)
    test_seconds = time.time() - t0

    def predict_on(dev_name: str):
        model.to(dev_name)
        return lambda texts: predict_proba(model, vocab.encode(texts), dev_name)

    texts = test["comment_text"].tolist()
    latency = {f"{d}_batch{b}_ms": measure_latency(predict_on(d), texts, b)
               for d in (["cuda", "cpu"] if device == "cuda" else ["cpu"]) for b in (1, 64)}

    save_report("lstm", {
        "model": "BiLSTM (statistique)",
        "data": "data/cleaned",
        "params": sum(p.numel() for p in model.parameters()),
        "hyperparams": {"vocab": VOCAB_SIZE, "max_len": MAX_LEN, "emb": 128, "hidden": 128,
                        "batch": 64, "lr": 1e-3, "epochs_run": len(history)},
        "history": history,
        "train_seconds": round(train_seconds, 1),
        "test_seconds": round(test_seconds, 2),
        "latency": latency,
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "test": compute_metrics(labels_matrix(test), probs),
    })


if __name__ == "__main__":
    main()
