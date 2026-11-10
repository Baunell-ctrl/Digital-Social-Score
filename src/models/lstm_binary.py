"""LSTM binaire : un commentaire est toxique si AU MOINS UNE des 6 colonnes vaut 1 (règle OU)."""
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import train_test_split
from torch import nn

ROOT = Path(__file__).resolve().parents[2]
LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]
SEED, MAX_LEN, MAX_VOCAB, BATCH, EPOCHS, PATIENCE = 42, 128, 30000, 64, 8, 2


def add_binary_label(df):
    df = df.copy()
    df["label"] = (df[LABELS].max(axis=1) >= 1).astype(int)  # règle OU
    return df


def load_splits(kind="cleaned"):
    train_all = add_binary_label(pd.read_csv(ROOT / "data" / kind / "train_dataset.csv"))
    test = add_binary_label(pd.read_csv(ROOT / "data" / kind / "val_dataset.csv"))
    train, dev = train_test_split(train_all, test_size=0.1, random_state=SEED, stratify=train_all["label"])
    return train.reset_index(drop=True), dev.reset_index(drop=True), test.reset_index(drop=True)


def tokenize(text):
    return re.findall(r"[a-z0-9']+", str(text).lower())


def build_vocab(texts):
    c = Counter(t for x in texts for t in tokenize(x))
    vocab = {"<pad>": 0, "<unk>": 1}
    for w, _ in c.most_common(MAX_VOCAB - 2):
        vocab[w] = len(vocab)
    return vocab


def encode(texts, vocab):
    out = np.zeros((len(texts), MAX_LEN), dtype=np.int64)
    for i, x in enumerate(texts):
        ids = [vocab.get(t, 1) for t in tokenize(x)][:MAX_LEN]
        out[i, : len(ids)] = ids
    return torch.from_numpy(out)


class LSTMClassifier(nn.Module):
    def __init__(self, vocab_size, emb=128, hidden=128):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, emb, padding_idx=0)
        self.lstm = nn.LSTM(emb, hidden, batch_first=True, bidirectional=True)
        self.drop = nn.Dropout(0.3)
        self.fc = nn.Linear(2 * hidden, 1)

    def forward(self, x):
        mask = (x != 0).unsqueeze(-1)
        out, _ = self.lstm(self.emb(x))
        pooled = (out * mask).max(dim=1).values  # max-pooling sur les tokens non-pad
        return self.fc(self.drop(pooled)).squeeze(-1)


def predict(model, X, device):
    model.eval()
    probs = []
    with torch.no_grad():
        for i in range(0, len(X), 256):
            probs.append(torch.sigmoid(model(X[i : i + 256].to(device))).cpu())
    return torch.cat(probs).numpy()


def compute_metrics(y_true, y_prob, threshold=0.5):
    y_pred = (y_prob >= threshold).astype(int)
    p, r, f, _ = precision_recall_fscore_support(y_true, y_pred, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {"precision": float(p), "recall": float(r), "f1": float(f),
            "roc_auc": float(roc_auc_score(y_true, y_prob)),
            "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}}


def main():
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    train, dev, test = load_splits("cleaned")
    print(f"device={device} train={len(train)} dev={len(dev)} test={len(test)} "
          f"positifs train={train.label.mean():.3f}")
    vocab = build_vocab(train["comment_text"])
    Xtr, Xdv, Xte = (encode(d["comment_text"], vocab) for d in (train, dev, test))
    ytr = torch.tensor(train["label"].values, dtype=torch.float32)
    model = LSTMClassifier(len(vocab)).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.BCEWithLogitsLoss()
    (ROOT / "models").mkdir(exist_ok=True)
    ckpt = ROOT / "models" / "lstm.pt"
    best, bad, history = -1, 0, []
    for ep in range(1, EPOCHS + 1):
        model.train()
        perm = torch.randperm(len(Xtr))
        total = 0.0
        for i in range(0, len(perm), BATCH):
            idx = perm[i : i + BATCH]
            opt.zero_grad()
            loss = loss_fn(model(Xtr[idx].to(device)), ytr[idx].to(device))
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(idx)
        m = compute_metrics(dev["label"].values, predict(model, Xdv, device))
        history.append({"epoch": ep, "train_loss": total / len(perm), "dev_f1": m["f1"], "dev_auc": m["roc_auc"]})
        print(history[-1])
        if m["f1"] > best:
            best, bad = m["f1"], 0
            torch.save(model.state_dict(), ckpt)
        else:
            bad += 1
            if bad >= PATIENCE:
                break
    model.load_state_dict(torch.load(ckpt, map_location=device))
    results = {"model": "lstm_binary", "label_rule": "OR of 6 columns", "seed": SEED,
               "dev": compute_metrics(dev["label"].values, predict(model, Xdv, device)),
               "test": compute_metrics(test["label"].values, predict(model, Xte, device)),
               "history": history}
    (ROOT / "reports" / "results_lstm.json").write_text(json.dumps(results, indent=2))
    print("TEST:", json.dumps(results["test"], indent=2))


if __name__ == "__main__":
    main()
