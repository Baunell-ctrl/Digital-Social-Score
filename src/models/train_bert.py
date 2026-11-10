"""Modèle transformer : fine-tuning de BERT (bert-base-uncased) sur les textes anonymisés.

Usage :  python -m src.models.train_bert [--limit 500] [--epochs 3]

BERT est pré-entraîné sur un énorme corpus anglais : il connaît déjà la langue, on ne lui
apprend que la tâche (6 sorties sigmoïdes). On lui donne le texte anonymisé non nettoyé,
car son tokenizer gère lui-même casse et ponctuation (« !!! » est un signal de toxicité).
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

from .common import (LABELS, MODELS_DIR, SEED, compute_metrics, labels_matrix, load_splits,
                     measure_latency, save_report)

MODEL_NAME = "bert-base-uncased"
MAX_LEN = 256


@torch.no_grad()
def predict_proba(model, tokenizer, texts: list[str], device: str, batch_size: int = 64) -> np.ndarray:
    model.eval()
    out = []
    for i in range(0, len(texts), batch_size):
        enc = tokenizer(texts[i : i + batch_size], truncation=True, max_length=MAX_LEN,
                        padding=True, return_tensors="pt").to(device)
        with torch.autocast(device_type=device, dtype=torch.float16, enabled=device == "cuda"):
            out.append(torch.sigmoid(model(**enc).logits.float()).cpu())
    return torch.cat(out).numpy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    args = parser.parse_args()

    torch.manual_seed(SEED)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    train, dev, test = load_splits("anonymized", args.limit)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME, num_labels=len(LABELS), problem_type="multi_label_classification").to(device)

    texts, y = train["comment_text"].tolist(), torch.tensor(labels_matrix(train), dtype=torch.float32)
    loader = DataLoader(list(range(len(texts))), batch_size=args.batch, shuffle=True)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    steps = len(loader) * args.epochs
    sched = get_linear_schedule_with_warmup(opt, int(0.06 * steps), steps)
    scaler = torch.amp.GradScaler(enabled=device == "cuda")
    loss_fn = nn.BCEWithLogitsLoss()

    out_dir = MODELS_DIR / "bert"
    best_auc, history = -1.0, []
    t0 = time.time()
    for epoch in range(1, args.epochs + 1):
        model.train()
        total = 0.0
        for step, idx in enumerate(loader, 1):
            enc = tokenizer([texts[i] for i in idx], truncation=True, max_length=MAX_LEN,
                            padding=True, return_tensors="pt").to(device)
            opt.zero_grad()
            with torch.autocast(device_type=device, dtype=torch.float16, enabled=device == "cuda"):
                logits = model(**enc).logits
            loss = loss_fn(logits.float(), y[idx].to(device))
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            total += loss.item() * len(idx)
            if step % 200 == 0:
                print(f"  epoch {epoch} step {step}/{len(loader)} loss {total / (step * args.batch):.4f}")
        dev_auc = compute_metrics(labels_matrix(dev), predict_proba(model, tokenizer, dev["comment_text"].tolist(), device))["macro"]["roc_auc"]
        history.append({"epoch": epoch, "loss": round(total / len(texts), 4), "dev_macro_auc": dev_auc})
        print(history[-1])
        if dev_auc > best_auc:
            best_auc = dev_auc
            model.save_pretrained(out_dir)
            tokenizer.save_pretrained(out_dir)
    train_seconds = time.time() - t0

    model = AutoModelForSequenceClassification.from_pretrained(out_dir).to(device)
    test_texts = test["comment_text"].tolist()
    t0 = time.time()
    probs = predict_proba(model, tokenizer, test_texts, device)
    test_seconds = time.time() - t0

    def predict_on(dev_name: str):
        model.to(dev_name)
        return lambda b: predict_proba(model, tokenizer, b, dev_name)

    latency = {}
    for d in (["cuda", "cpu"] if device == "cuda" else ["cpu"]):
        for b in (1, 64):
            # Sur CPU, BERT est lent : on mesure sur moins de textes.
            latency[f"{d}_batch{b}_ms"] = measure_latency(predict_on(d), test_texts, b, n_texts=256 if d == "cuda" else 64)

    save_report("bert", {
        "model": f"BERT fine-tuné ({MODEL_NAME}, transformer)",
        "data": "data/anonymized",
        "params": sum(p.numel() for p in model.parameters()),
        "hyperparams": {"max_len": MAX_LEN, "batch": args.batch, "lr": args.lr, "epochs": args.epochs, "fp16": device == "cuda"},
        "history": history,
        "train_seconds": round(train_seconds, 1),
        "test_seconds": round(test_seconds, 2),
        "latency": latency,
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "test": compute_metrics(labels_matrix(test), probs),
    })


if __name__ == "__main__":
    main()
