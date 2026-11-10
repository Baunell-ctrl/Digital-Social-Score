"""Chargement du BiLSTM et prédiction pour l'API.

Même pipeline qu'à l'entraînement : anonymisation (regex + spaCy) -> nettoyage -> BiLSTM.
Architecture identique à ``src/models/train_lstm.py`` (branche training-6-labels) :
embedding 128 -> BiLSTM 2 x 128 -> max-pooling -> 6 sorties sigmoïdes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import torch
from torch import nn

from src.anonymization.anonymizer import anonymize_text, detect, load_nlp
from src.preprocessing.cleaner import clean_text

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = Path(os.environ.get("MODELS_DIR", ROOT / "models"))

LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]
MAX_LEN = 200
PAD, UNK = 0, 1


class BiLSTM(nn.Module):
    def __init__(self, vocab_size: int, emb: int = 128, hidden: int = 128, n_out: int = len(LABELS)):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, emb, padding_idx=PAD)
        self.lstm = nn.LSTM(emb, hidden, batch_first=True, bidirectional=True)
        self.drop = nn.Dropout(0.3)
        self.out = nn.Linear(2 * hidden, n_out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h, _ = self.lstm(self.emb(x))
        h = h.masked_fill((x == PAD).unsqueeze(-1), -1e4)  # ignore le remplissage
        return self.out(self.drop(h.max(dim=1).values))


class ToxicityPredictor:
    def __init__(self, models_dir: Path = MODELS_DIR, use_ner: bool = True):
        itos = json.loads((models_dir / "lstm_vocab.json").read_text(encoding="utf-8"))
        self.stoi = {w: i for i, w in enumerate(itos)}
        self.model = BiLSTM(len(itos))
        state = torch.load(models_dir / "lstm.pt", map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        self.model.eval()
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        self.nlp = load_nlp() if use_ner else None

    def preprocess(self, text: str) -> str:
        """Anonymise puis nettoie, comme le jeu d'entraînement."""
        doc = self.nlp(text) if self.nlp is not None else None
        return clean_text(anonymize_text(text, detect(text, doc)))

    def encode(self, text: str) -> torch.Tensor:
        ids = [self.stoi.get(w, UNK) for w in text.split()[:MAX_LEN]] or [UNK]
        return torch.tensor([ids], dtype=torch.long)

    @torch.no_grad()
    def predict(self, text: str) -> dict[str, float]:
        """Probabilité (0-1) de chacune des 6 étiquettes."""
        probs = torch.sigmoid(self.model(self.encode(self.preprocess(text))))[0]
        return {label: float(p) for label, p in zip(LABELS, probs)}


class LayaPredictor:
    """LAYA entraîné en binaire (``src/models/laya_binary.py``) : une seule probabilité ``toxic``.

    Réutilise l'anonymisation et le nettoyage du LSTM pour voir le texte comme à l'entraînement.
    """

    def __init__(self, preprocess, models_dir: Path = MODELS_DIR):
        import laya  # dépendance lourde, importée seulement si LAYA est demandé

        from src.models.laya_binary import MAX_LEN, QUESTION_ID, QUESTIONS

        path = models_dir / "laya_binary"
        if not (path / "model.safetensors").exists():
            raise FileNotFoundError(f"modèle LAYA absent : {path}")
        self.agent = laya.load(str(path), device="cuda" if torch.cuda.is_available() else "cpu")
        self.preprocess = preprocess
        self.questions, self.qid, self.max_len = QUESTIONS, QUESTION_ID, MAX_LEN

    def predict(self, text: str) -> dict[str, float]:
        out = self.agent.predict_batch([{"post": self.preprocess(text)}], self.questions, max_len=self.max_len)
        return {"toxic": float(out[0]["answers"][self.qid]["noul"])}
