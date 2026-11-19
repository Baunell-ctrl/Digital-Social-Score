"""Défense contre les exemples adversariaux (cours 5, § 15 : « normalisation stricte des entrées »).

Un attaquant déguise une insulte pour qu'elle échappe au modèle : « id1ot », « 𝐢𝐝𝐢𝐨𝐭 », « iiidiot »,
« i.d.i.o.t », « f*ck », lettres cyrilliques identiques à l'œil (« іdiot »), caractères invisibles.
Le LSTM ne connaît que les mots de son vocabulaire : un mot déguisé devient « mot inconnu » et ne
compte plus.

Règle de sûreté : un mot DÉJÀ connu du vocabulaire n'est jamais modifié. Seuls les mots inconnus sont
essayés sous des formes « décodées », et remplacés seulement si l'une d'elles est un mot connu. Un texte
normal n'est donc pas transformé (vérifié : `python -m src.preprocessing.robustesse`).
"""
from __future__ import annotations

import re
import unicodedata

# Caractères invisibles : espaces de largeur nulle, joiners, marques de direction, trait d'union conditionnel.
_INVISIBLES = re.compile("[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Lettres cyrilliques et grecques qui ressemblent aux lettres latines (homoglyphes).
_HOMOGLYPHES = str.maketrans("аеорсухіјѕԁԛԝАЕОРСХІЈЅВНКМТαορτυνκιΑΒΕΗΙΚΜΝΟΡΤΥΧ",
                             "aeopcyxijsdqwAEOPCXIJSBHKMTaoptuvkiABEHIKMNOPTYX")
# Chiffres et symboles utilisés comme lettres (« leetspeak »).
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
                       "@": "a", "$": "s", "!": "i", "|": "l", "€": "e"})
_SEPARATEURS = re.compile(r"[.\-_*·•'`~^]+")
_REPETITIONS = re.compile(r"(.)\1{2,}")       # 3 fois ou plus la même lettre
_DOUBLES = re.compile(r"(.)\1+")
_LETTRES_ISOLEES = re.compile(r"\b(?:[a-z][ .\-_*]){2,}[a-z]\b", re.IGNORECASE)   # « i d i o t », « i.d.i.o.t »


def normaliser_unicode(texte: str) -> str:
    """Forme canonique : lettres stylisées et pleine largeur → lettres simples, invisibles retirés, homoglyphes."""
    texte = unicodedata.normalize("NFKC", texte)
    texte = _INVISIBLES.sub("", texte)
    return texte.translate(_HOMOGLYPHES)


def _candidats(mot: str) -> list[str]:
    """Formes décodées d'un mot inconnu, de la plus prudente à la plus large."""
    bas = mot.lower()
    leet = bas.translate(_LEET)
    sans_sep = _SEPARATEURS.sub("", leet)
    formes = [leet, sans_sep]
    for f in (bas, leet, sans_sep):
        formes += [_REPETITIONS.sub(r"\1\1", f), _REPETITIONS.sub(r"\1", f), _DOUBLES.sub(r"\1", f)]
    vus, uniques = set(), []
    for f in formes:
        f = re.sub(r"[^a-z']", "", f)
        if f and f != bas and f not in vus:
            vus.add(f)
            uniques.append(f)
    return uniques


def _joker(mot: str, vocab: dict[str, int]) -> str | None:
    """« f*ck » : une seule lettre masquée par * → le mot le plus fréquent du vocabulaire qui correspond."""
    bas = mot.lower()
    if bas.count("*") != 1 or len(bas) < 3 or not re.fullmatch(r"[a-z*]+", bas):
        return None
    motif = re.compile(bas.replace("*", "[a-z]"))
    trouves = [w for w in vocab if len(w) == len(bas) and motif.fullmatch(w)]
    return min(trouves, key=vocab.__getitem__) if trouves else None


def normaliser(texte: str, vocab: dict[str, int]) -> str:
    """Rend lisibles par le modèle les mots déguisés ; ne touche jamais un mot déjà connu."""
    texte = normaliser_unicode(texte)
    # « i d i o t » / « i.d.i.o.t » : lettres isolées recollées si le mot obtenu est connu.
    texte = _LETTRES_ISOLEES.sub(
        lambda m: (w if (w := re.sub(r"[^a-z]", "", m.group(0).lower())) in vocab else m.group(0)), texte)
    sortie = []
    for brut in texte.split():
        coeur = brut.strip(".,;:?\"()[]{}<>").lower()
        # Ce que verra le modèle : le nettoyeur coupe sur tout ce qui n'est pas lettre, chiffre ou apostrophe.
        vu = [p for p in re.split(r"[^a-z0-9']+", coeur) if p]
        if not coeur or (len(vu) == 1 and vu[0] in vocab) or not re.search(r"[a-z]", coeur):
            sortie.append(brut)
            continue
        remplacement = _joker(coeur, vocab) or next((c for c in _candidats(coeur) if c in vocab), None)
        sortie.append(remplacement or brut)
    return " ".join(sortie)


if __name__ == "__main__":  # démonstration : python -m src.preprocessing.robustesse
    import json
    from pathlib import Path

    vocab = json.loads((Path(__file__).resolve().parents[2] / "artifacts" / "vocab.json").read_text())
    for t in ["you are an id1ot", "you are an 𝐢𝐝𝐢𝐨𝐭", "you are an іdiot", "you are an iiidiooot",
              "you are an i.d.i.o.t", "f*ck you", "s.t.u.p.i.d", "Thank you, have a nice day!"]:
        print(f"{t!r:35} → {normaliser(t, vocab)!r}")
