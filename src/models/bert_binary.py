"""DistilBERT binaire (règle du OU), mêmes splits et même test propre que le LSTM."""
import json
import os
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import pandas as pd
import torch
from torch import nn
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from src.models.lstm_binary import ROOT, SEED, compute_metrics, load_splits

NAME = "distilbert-base-uncased"
MAX_LEN, BATCH, EPOCHS, LR = 96, 8, 2, 2e-5  # réglages allégés pour 8 Go de RAM


def batches(tok, texts, size):
    for i in range(0, len(texts), size):
        yield tok(list(texts[i : i + size]), truncation=True, max_length=MAX_LEN,
                  padding=True, return_tensors="pt")


def predict(model, tok, texts, device):
    model.eval()
    out = []
    with torch.no_grad():
        for enc in batches(tok, texts, 32):
            enc = {k: v.to(device) for k, v in enc.items()}
            out.append(torch.sigmoid(model(**enc).logits.squeeze(-1)).float().cpu())
    return torch.cat(out).numpy()


def main():
    torch.manual_seed(SEED)
    np.random.seed(SEED)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    train, dev, test_full = load_splits("cleaned")
    seen = pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")
    test = test_full[~test_full.id.isin(seen.id) & ~test_full.comment_text.isin(set(seen.comment_text))]
    print(f"device={device} train={len(train)} dev={len(dev)} test propre={len(test)} test complet={len(test_full)}",
          flush=True)

    tok = AutoTokenizer.from_pretrained(NAME)
    model = AutoModelForSequenceClassification.from_pretrained(NAME, num_labels=1).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=0.01)
    loss_fn = nn.BCEWithLogitsLoss()
    texts, labels = train["comment_text"].tolist(), train["label"].values.astype("float32")
    (ROOT / "models").mkdir(exist_ok=True)
    ckpt = ROOT / "models" / "distilbert.pt"
    best, history, t0 = -1, [], time.time()
    for ep in range(1, EPOCHS + 1):
        model.train()
        perm = np.random.permutation(len(texts))
        total = 0.0
        for n, i in enumerate(range(0, len(perm), BATCH)):
            idx = perm[i : i + BATCH]
            enc = tok([texts[j] for j in idx], truncation=True, max_length=MAX_LEN,
                      padding=True, return_tensors="pt")
            enc = {k: v.to(device) for k, v in enc.items()}
            y = torch.tensor(labels[idx]).to(device)
            opt.zero_grad()
            loss = loss_fn(model(**enc).logits.squeeze(-1), y)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            total += loss.item() * len(idx)
            if n % 100 == 0:
                if device == "mps":
                    torch.mps.empty_cache()
                print(f"epoch {ep} step {n}/{len(perm) // BATCH} loss={loss.item():.3f} "
                      f"t={time.time() - t0:.0f}s", flush=True)
        m = compute_metrics(dev["label"].values, predict(model, tok, dev["comment_text"].tolist(), device))
        history.append({"epoch": ep, "train_loss": total / len(perm), "dev_f1": m["f1"], "dev_auc": m["roc_auc"],
                        "elapsed_s": round(time.time() - t0)})
        print(history[-1], flush=True)
        if m["f1"] > best:
            best = m["f1"]
            torch.save(model.state_dict(), ckpt)
        if device == "mps":
            torch.mps.empty_cache()

    model.load_state_dict(torch.load(ckpt, map_location=device))
    t = time.time()
    p_test = predict(model, tok, test["comment_text"].tolist(), device)
    ms = 1000 * (time.time() - t) / len(test)
    results = {"model": NAME, "label_rule": "OR of 6 columns", "seed": SEED,
               "dev": compute_metrics(dev["label"].values, predict(model, tok, dev["comment_text"].tolist(), device)),
               "test_clean": compute_metrics(test["label"].values, p_test),
               "test_full": compute_metrics(test_full["label"].values,
                                            predict(model, tok, test_full["comment_text"].tolist(), device)),
               "inference_ms_per_comment": round(ms, 3), "history": history}
    (ROOT / "reports" / "results_bert.json").write_text(json.dumps(results, indent=2))
    print("TEST CLEAN:", json.dumps(results["test_clean"], indent=2))
    print("ms/comment:", results["inference_ms_per_comment"])


if __name__ == "__main__":
    main()
