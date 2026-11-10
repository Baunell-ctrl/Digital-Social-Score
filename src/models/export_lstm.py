"""Exporte les fichiers nécessaires au déploiement du LSTM dans artifacts/.

Usage : python -m src.models.export_lstm
Écrit : lstm.pt (poids), vocab.json (30 000 mots -> numéros, indispensable), config.json (paramètres).
"""
import json
import shutil

from src.models.lstm_binary import MAX_LEN, MAX_VOCAB, ROOT, build_vocab, load_splits

OUT = ROOT / "artifacts"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    train, _, _ = load_splits("cleaned")
    vocab = build_vocab(train["comment_text"])
    (OUT / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False))
    shutil.copy(ROOT / "models" / "lstm.pt", OUT / "lstm.pt")
    config = {"model": "lstm_binary", "label_rule": "OR of 6 Jigsaw columns", "vocab_size": len(vocab),
              "max_len": MAX_LEN, "embedding_dim": 128, "hidden_dim": 128, "threshold": 0.5,
              "max_vocab": MAX_VOCAB}
    (OUT / "config.json").write_text(json.dumps(config, indent=2))
    print("exporté dans", OUT, "| vocabulaire :", len(vocab), "mots")


if __name__ == "__main__":
    main()
