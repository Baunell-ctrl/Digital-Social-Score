"""Attaque adversariale par mots déguisés, et mesure de la défense par normalisation (cours 5, § 15).

    python attack/adversarial.py            # écrit reports/attack/adversarial.json

L'attaquant garde le sens du message mais déguise les mots qui le font détecter : « id1ot », « 𝐢𝐝𝐢𝐨𝐭 »,
« iiidiot », « i.d.i.o.t », « іdiot » (i cyrillique), caractère invisible, « id*ot ».
Les mots visés sont choisis à partir des données, sans liste écrite à la main : les 300 mots les plus
associés aux commentaires toxiques du jeu d'entraînement.

Mesures, sur le test propre (1 308 commentaires jamais vus à l'entraînement), avec le pipeline de l'API :
- taux de détection des commentaires toxiques : originaux / déguisés sans défense / déguisés avec défense ;
- faux positifs sur les commentaires sains, avec et sans défense (la défense ne doit rien casser).
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.api.predictor import ToxicityPredictor  # noqa: E402
from src.models.lstm_binary import load_splits  # noqa: E402

SEED = 42
STYLISE = {c: chr(0x1D41A + i) for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")}  # 𝐚…𝐳 (gras mathématique)
CYRILLIQUE = {"a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "i": "і", "x": "х", "y": "у"}
LEET = {"i": "1", "o": "0", "e": "3", "a": "@", "s": "$", "t": "7"}


def deguiser(mot: str, rng: random.Random) -> str:
    """Une des techniques d'évasion du cours, tirée au hasard."""
    technique = rng.choice(["leet", "stylise", "repetition", "points", "cyrillique", "invisible", "masque"])
    voyelles = [i for i, c in enumerate(mot) if c in "aeiou"]
    if technique == "leet" and any(c in LEET for c in mot):
        i = next(i for i, c in enumerate(mot) if c in LEET)
        return mot[:i] + LEET[mot[i]] + mot[i + 1:]
    if technique == "stylise":
        return "".join(STYLISE.get(c, c) for c in mot)
    if technique == "repetition" and voyelles:
        i = voyelles[0]
        return mot[:i] + mot[i] * 3 + mot[i + 1:]
    if technique == "points":
        return ".".join(mot)
    if technique == "cyrillique" and any(c in CYRILLIQUE for c in mot):
        i = next(i for i, c in enumerate(mot) if c in CYRILLIQUE)
        return mot[:i] + CYRILLIQUE[mot[i]] + mot[i + 1:]
    if technique == "masque" and voyelles and len(mot) >= 4:
        i = voyelles[0]
        return mot[:i] + "*" + mot[i + 1:]
    return mot[:1] + "\u200b" + mot[1:]                     # espace invisible


def mots_toxiques(train: pd.DataFrame, n: int = 300) -> set[str]:
    """Les n mots au plus fort rapport toxique / sain (au moins 20 apparitions) dans le train."""
    tox, sain = Counter(), Counter()
    for texte, label in zip(train.comment_text, train.label):
        (tox if label else sain).update(set(str(texte).split()))
    nt, ns = sum(train.label == 1), sum(train.label == 0)
    score = {w: math.log((tox[w] + 1) / nt) - math.log((sain[w] + 1) / ns)
             for w in tox if tox[w] + sain[w] >= 20 and len(w) >= 3}
    return set(sorted(score, key=score.get, reverse=True)[:n])


def main() -> None:
    rng = random.Random(SEED)
    train, _, test_full = load_splits("cleaned")
    seen = pd.read_csv(ROOT / "data" / "cleaned" / "train_dataset.csv")
    test = test_full[~test_full.id.isin(seen.id) & ~test_full.comment_text.isin(set(seen.comment_text))]
    cibles = mots_toxiques(train)
    toxiques = test[test.label == 1].comment_text.tolist()
    sains = test[test.label == 0].comment_text.tolist()
    deguises = [" ".join(deguiser(w, rng) if w in cibles else w for w in t.split()) for t in toxiques]
    n_mots = sum(w in cibles for t in toxiques for w in t.split())

    sans = ToxicityPredictor(use_ner=True, normalise=False)
    avec = ToxicityPredictor(use_ner=True, normalise=True)
    detecte = lambda p, textes: sum(p.predict(t) >= p.threshold for t in textes) / len(textes)  # noqa: E731

    resultats = {
        "jeu": f"test propre : {len(toxiques)} toxiques, {len(sains)} sains",
        "mots_deguises": n_mots,
        "detection_toxiques": {
            "originaux_sans_defense": round(detecte(sans, toxiques), 3),
            "deguises_sans_defense": round(detecte(sans, deguises), 3),
            "deguises_avec_defense": round(detecte(avec, deguises), 3),
            "originaux_avec_defense": round(detecte(avec, toxiques), 3),
        },
        "faux_positifs_sains": {
            "sans_defense": round(detecte(sans, sains), 3),
            "avec_defense": round(detecte(avec, sains), 3),
        },
        "exemples": [{"original": t[:120], "deguise": d[:120]} for t, d in list(zip(toxiques, deguises))[:3]],
    }
    out = ROOT / "reports" / "attack" / "adversarial.json"
    out.write_text(json.dumps(resultats, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in resultats.items() if k != "exemples"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
