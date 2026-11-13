"""Charges « pire-cas » pour l'attaque par coût asymétrique (TP2, étape 2).

Le but n'est pas d'envoyer beaucoup de requêtes, mais des requêtes **valides** (texte de 1 à
5 000 caractères, acceptées par l'API) qui coûtent le plus cher possible à traiter côté serveur.

Le pipeline d'inférence (src/api/predictor.py::preprocess) est :
    anonymize_text  -> spaCy NER (de loin le plus lourd : il cherche les entités, noms, lieux)
    clean_text      -> regex sur les emojis, sélecteurs de variation, ponctuation
    BiLSTM          -> borné à 128 mots (max_len), donc peu sensible à la longueur

On maximise donc le travail des DEUX premières étapes. Trois profils :
    names   beaucoup de faux prénoms/lieux, phrases « réalistes » -> le NER travaille au maximum
    emoji   emojis + sélecteurs de variation + caractères invisibles -> la regex travaille au maximum
    mixed   les deux mélangés (profil conseillé)

Aucun de ces textes n'est toxique : l'attaque vise la disponibilité (CPU, latence), pas le score.
"""

from __future__ import annotations

import random

MAX_CHARS = 5000  # limite de l'API (ScoreRequest.text) ; on vise juste en dessous

# Prénoms et lieux : spaCy les repère comme entités (PERSON, GPE, ORG) -> coût NER élevé.
FIRST_NAMES = [
    "Alexander", "Catherine", "Maximilian", "Anastasia", "Christopher", "Isabella", "Sebastian",
    "Genevieve", "Nathaniel", "Josephine", "Theodore", "Marguerite", "Benedict", "Evangeline",
    "Montgomery", "Wilhelmina", "Fitzgerald", "Clementine", "Bartholomew", "Henrietta",
]
LAST_NAMES = [
    "Harrington", "Fitzgerald", "Vanderbilt", "Montgomery", "Richardson", "Castellano",
    "Abernathy", "Blackwood", "Thistlewood", "Pemberton", "Kowalczyk", "Nakamura",
]
PLACES = [
    "San Francisco", "Johannesburg", "Buenos Aires", "Kuala Lumpur", "Saint Petersburg",
    "Rio de Janeiro", "New South Wales", "Addis Ababa", "Ulaanbaatar", "Reykjavik",
]
ORGS = ["Goldman Sachs", "United Nations", "Deutsche Bahn", "Banco Santander", "Mitsubishi Heavy Industries"]

# Emojis variés + modificateurs : chaque séquence fait plusieurs points de code à nettoyer.
EMOJIS = ["😀", "🔥", "💀", "🎉", "🚀", "🌍", "🏴‍☠️", "👨‍👩‍👧‍👦", "🧑🏽‍💻", "👍🏿", "❤️", "🤦‍♂️", "🇫🇷", "⚡"]
# Caractères invisibles / de largeur nulle : passent la validation, cassent la tokenisation.
INVISIBLES = ["​", "‌", "‍", "️", "⁠", "­"]


def _names_sentence(rng: random.Random) -> str:
    """Une phrase dense en entités nommées (personnes, lieux, organisations)."""
    return (
        f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)} met {rng.choice(FIRST_NAMES)} "
        f"{rng.choice(LAST_NAMES)} in {rng.choice(PLACES)} to discuss {rng.choice(ORGS)} and "
        f"{rng.choice(PLACES)} before flying to {rng.choice(PLACES)} with {rng.choice(FIRST_NAMES)}. "
    )


def _emoji_sentence(rng: random.Random) -> str:
    """Des mots courts entrecoupés d'emojis, de sélecteurs de variation et de caractères invisibles."""
    out = []
    for _ in range(12):
        out.append(rng.choice(["ok", "yes", "fine", "sure", "nice", "good", "hello", "today"]))
        out.append(rng.choice(EMOJIS))
        out.append(rng.choice(INVISIBLES))
    return "".join(out) + " "


def build(profile: str = "mixed", max_chars: int = MAX_CHARS, seed: int | None = None) -> str:
    """Construit une charge pire-cas d'au plus ``max_chars`` caractères pour le profil donné.

    ``seed`` fixé rend la charge reproductible (utile pour comparer les mesures)."""
    rng = random.Random(seed)
    make = {"names": _names_sentence, "emoji": _emoji_sentence}.get(profile)
    parts: list[str] = []
    total = 0
    while total < max_chars - 120:  # marge pour ne jamais dépasser la limite
        chunk = make(rng) if make else (_names_sentence(rng) if rng.random() < 0.5 else _emoji_sentence(rng))
        parts.append(chunk)
        total += len(chunk)
    text = "".join(parts)[:max_chars]
    return text


if __name__ == "__main__":  # aperçu rapide : python -m attack.payloads
    for p in ("names", "emoji", "mixed"):
        t = build(p, seed=1)
        print(f"\n=== {p} ({len(t)} caractères) ===")
        print(t[:300], "…")
