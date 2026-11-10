"""Chargement du BiLSTM binaire et prédiction pour l'API.

Même pipeline qu'à l'entraînement : anonymisation (regex + spaCy) -> nettoyage -> BiLSTM.

Le modèle est entraîné sur **un seul label** : 1 (toxique) si au moins une des 6 colonnes
Jigsaw (toxic, severe_toxic, obscene, threat, insult, identity_hate) vaut 1, sinon 0.
Architecture : embedding 128 -> BiLSTM 2 x 128 -> max-pooling (sans le remplissage) -> 1 sortie sigmoïde.

Fichiers lus dans ``ARTIFACTS_DIR`` (``artifacts/`` par défaut) :
- ``lstm.pt``     : les poids (state_dict) ;
- ``vocab.json``  : le vocabulaire {mot: numéro} (0 = remplissage, 1 = mot inconnu) ;
- ``config.json`` : tailles du modèle, longueur maximale et seuil de décision.
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
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", ROOT / "artifacts"))
MODELS_DIR = Path(os.environ.get("MODELS_DIR", ROOT / "models"))  # LAYA (local uniquement)

PAD, UNK = 0, 1


class BiLSTM(nn.Module):
    def __init__(self, vocab_size: int, emb: int = 128, hidden: int = 128):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, emb, padding_idx=PAD)
        self.lstm = nn.LSTM(emb, hidden, batch_first=True, bidirectional=True)
        self.fc = nn.Linear(2 * hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h, _ = self.lstm(self.emb(x))                     # (batch, len, 2*hidden)
        h = h.masked_fill((x == PAD).unsqueeze(-1), -1e4)  # ignore le remplissage
        return self.fc(h.max(dim=1).values).squeeze(-1)   # logit (batch,)


class ToxicityPredictor:
    def __init__(self, artifacts_dir: Path = ARTIFACTS_DIR, use_ner: bool = True):
        cfg = json.loads((artifacts_dir / "config.json").read_text(encoding="utf-8"))
        self.stoi: dict[str, int] = json.loads((artifacts_dir / "vocab.json").read_text(encoding="utf-8"))
        self.max_len = cfg["max_len"]
        self.threshold = cfg.get("threshold", 0.5)
        self.model = BiLSTM(len(self.stoi), cfg["embedding_dim"], cfg["hidden_dim"])
        state = torch.load(artifacts_dir / "lstm.pt", map_location="cpu", weights_only=True)
        self.model.load_state_dict(state)
        self.model.eval()
        torch.set_num_threads(max(1, os.cpu_count() or 1))
        self.nlp = load_nlp() if use_ner else None

    def preprocess(self, text: str) -> str:
        """Anonymise puis nettoie, comme le jeu d'entraînement."""
        doc = self.nlp(text) if self.nlp is not None else None
        return clean_text(anonymize_text(text, detect(text, doc)))

    def encode(self, text: str) -> torch.Tensor:
        ids = [self.stoi.get(w, UNK) for w in text.split()[: self.max_len]] or [UNK]
        return torch.tensor([ids], dtype=torch.long)

    @torch.no_grad()
    def predict(self, text: str) -> float:
        """Probabilité (0-1) que le texte soit toxique."""
        return float(torch.sigmoid(self.model(self.encode(self.preprocess(text))))[0])


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

    def predict(self, text: str) -> float:
        """Probabilité (0-1) que le texte soit toxique."""
        out = self.agent.predict_batch([{"post": self.preprocess(text)}], self.questions, max_len=self.max_len)
        return float(out[0]["answers"][self.qid]["noul"])
