"""Nettoyage simple des textes avant entraînement (étape 2).

Ordre : minuscules -> emojis -> sauts de ligne -> ponctuation -> espaces multiples.
"""

from __future__ import annotations

import re

# Emojis, pictogrammes, symboles, drapeaux, sélecteurs de variation, joiners.
_EMOJI = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # pictogrammes, emoticons, transports, symboles étendus
    "\U00002600-\U000027BF"  # symboles divers, dingbats
    "\U00002B00-\U00002BFF"  # flèches / étoiles
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "]+"
)
_NEWLINES = re.compile(r"[\r\n\t]+")
# Tout sauf lettres, chiffres, espaces et apostrophes (contractions : don't).
_PUNCT = re.compile(r"[^\w\s']|_")
_SPACES = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Retourne le texte en minuscules, sans emojis, sauts de ligne ni ponctuation."""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = _EMOJI.sub(" ", text)
    text = _NEWLINES.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    # Apostrophes isolées (ex. guillemets simples autour d'un mot) : on les retire.
    text = re.sub(r"(?<![a-z0-9])'|'(?![a-z0-9])", " ", text)
    return _SPACES.sub(" ", text).strip()
