"""Modèle décisionnaire : LAYA (ConvAI Innovations), non entraîné puis entraîné.

Usage :
    python -m src.models.train_laya --mode zeroshot            # LAYA tel que publié
    python -m src.models.train_laya --mode finetune            # LAYA entraîné sur nos données

LAYA est un « decision model » : encodeur ModernBERT + têtes de décision. Il ne génère pas
de texte, il répond à des questions typées. On lui pose une question oui/non (``noul``)
par étiquette ; il renvoie P(oui), qui joue le rôle de la probabilité multi-label.

- zeroshot : aucune donnée vue, uniquement les consignes en anglais -> référence.
- finetune : ``laya.train.finetune`` (méthode RLCD des auteurs) sur le train anonymisé.
  Chaque couple (commentaire, question) est un exemple : 6 exemples par commentaire.
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np

from .common import (LABELS, MODELS_DIR, SEED, compute_metrics, labels_matrix, load_splits,
                     measure_latency, save_report)

BASE_MODEL = "convaiinnovations/laya"

# Une question oui/non par étiquette, dans l'ordre de LABELS.
QUESTIONS = {
    "toxic": "Is `post` toxic: rude, disrespectful or likely to make someone leave the discussion?",
    "severe_toxic": "Is `post` severely toxic: extremely hateful, aggressive or vulgar?",
    "obscene": "Does `post` contain obscene, vulgar or sexually explicit language?",
    "threat": "Does `post` threaten violence, harm or intimidation against someone?",
    "insult": "Does `post` insult or call names to a person or group?",
    "identity_hate": "Does `post` attack people because of their identity (race, religion, gender, sexual orientation, nationality)?",
}
QUESTION_SCHEMA = {k: {"type": "noul", "instructions": v} for k, v in QUESTIONS.items()}
assert list(QUESTIONS) == LABELS


def state(text: str) -> dict:
    return {"post": text}


MAX_LEN = 256  # même troncature que BERT ; au-delà, la mémoire du GPU (8 Go) déborde


def predict_proba(agent, texts: list[str], batch_size: int = 16) -> np.ndarray:
    # sort_by_length : regroupe les textes de longueur proche -> moins de remplissage, moins de mémoire.
    results = agent.predict_batch([state(t) for t in texts], QUESTION_SCHEMA, batch_size=batch_size,
                                  max_len=MAX_LEN, sort_by_length=True)
    return np.array([[r["answers"][lab]["noul"] for lab in LABELS] for r in results], dtype=float)


def write_jsonl(df, path) -> None:
    """Une ligne par commentaire : ``{state, questions, gold}`` (format de laya.train)."""
    with open(path, "w", encoding="utf-8") as f:
        for text, y in zip(df["comment_text"], labels_matrix(df)):
            gold = {lab: {"probabilities": {"false": float(1 - v), "true": float(v)}} for lab, v in zip(LABELS, y)}
            f.write(json.dumps({"state": state(text), "questions": QUESTION_SCHEMA, "gold": gold}) + "\n")


def finetune(train, dev, epochs: int, max_len: int, freeze_encoder: bool) -> tuple[str, float, dict]:
    from laya.train import TrainConfig, finetune as laya_finetune, resolve_checkpoint_dir

    data_dir = MODELS_DIR / "laya_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(train, data_dir / "train.jsonl")
    write_jsonl(dev, data_dir / "dev.jsonl")

    out_dir = MODELS_DIR / "laya"
    # freeze_encoder : on n'entraîne que les têtes de décision. Entraîner tout l'encodeur
    # (395 M paramètres + AdamW) demande ~6,4 Go avant activations : impossible sur 8 Go.
    config = TrainConfig(epochs=epochs, micro_batch=8, grad_accum=8, max_len=max_len, seed=SEED,
                         freeze_encoder=freeze_encoder, eval_data=str(data_dir / "dev.jsonl"), log_every=20)
    t0 = time.time()
    summary = laya_finetune(str(data_dir / "train.jsonl"), resolve_checkpoint_dir(BASE_MODEL),
                            str(out_dir), config=config)
    return str(out_dir), time.time() - t0, {"epochs": epochs, "micro_batch": 8, "grad_accum": 8,
                                            "max_len": max_len, "loss": config.loss,
                                            "freeze_encoder": freeze_encoder,
                                            "encoder_lr": config.encoder_lr, "head_lr": config.head_lr,
                                            "summary": json.loads(json.dumps(summary, default=str))}


def main() -> None:
    import laya
    import torch

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["zeroshot", "finetune"], required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--train-rows", type=int, default=None,
                        help="sous-échantillon du train pour le fine-tuning (temps de calcul)")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--max-len", type=int, default=MAX_LEN)
    parser.add_argument("--full-finetune", action="store_true",
                        help="entraîne aussi l'encodeur (nécessite plus de 8 Go de mémoire GPU)")
    parser.add_argument("--cpu-latency", action="store_true",
                        help="mesure aussi la latence sur CPU (lent pour un modèle de 421 M paramètres)")
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    train, dev, test = load_splits("anonymized", args.limit)

    if args.mode == "zeroshot":
        model_path, train_seconds, hyper = BASE_MODEL, 0.0, {"questions": QUESTIONS}
    else:
        if args.train_rows:
            train = train.sample(n=min(args.train_rows, len(train)), random_state=SEED)
        model_path, train_seconds, hyper = finetune(train, dev, args.epochs, args.max_len,
                                                    freeze_encoder=not args.full_finetune)
        hyper.update({"train_rows": len(train), "questions": QUESTIONS})

    agent = laya.load(model_path, device=device)
    texts = test["comment_text"].tolist()
    print(f"évaluation sur {len(texts)} textes de test...", flush=True)
    t0 = time.time()
    probs = predict_proba(agent, texts)
    test_seconds = time.time() - t0

    latency = {}
    devices = [device] + (["cpu"] if args.cpu_latency and device == "cuda" else [])
    for d in devices:
        a = agent if d == device else laya.load(model_path, device=d)
        for b in (1, 64):
            print(f"latence {d} lot {b}...", flush=True)
            latency[f"{d}_batch{b}_ms"] = measure_latency(lambda x, a=a, b=b: predict_proba(a, x, b), texts, b,
                                                          n_texts=256 if d == "cuda" else 32)

    name = f"laya_{'zeroshot' if args.mode == 'zeroshot' else 'finetuned'}"
    save_report(name, {
        "model": f"LAYA {'non entraîné (zero-shot)' if args.mode == 'zeroshot' else 'entraîné (RLCD)'} (décisionnaire)",
        "data": "data/anonymized",
        "base_model": BASE_MODEL,
        "hyperparams": hyper,
        "train_seconds": round(train_seconds, 1),
        "test_seconds": round(test_seconds, 2),
        "latency": latency,
        "device": torch.cuda.get_device_name(0) if device == "cuda" else "cpu",
        "test": compute_metrics(labels_matrix(test), probs),
    })


if __name__ == "__main__":
    main()
