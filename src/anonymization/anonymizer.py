"""Détection et remplacement des données personnelles dans des textes.

Deux stratégies sont proposées, qui correspondent aux deux notions du RGPD :

* ``anonymize``     -> remplace chaque donnée personnelle par une étiquette générique
                       (``[PERSON]``, ``[EMAIL]``...). Irréversible : on ne peut pas
                       retrouver la personne.
* ``pseudonymize``  -> remplace chaque donnée personnelle par un identifiant stable
                       (``PERSON_3fa9c1d2``) calculé avec un HMAC-SHA256 et une clé secrète.
                       La même valeur donne toujours le même pseudonyme, ce qui conserve
                       la cohérence du texte. Seul le détenteur de la clé (et de la table
                       de correspondance, stockée à part) peut ré-identifier.

La détection combine :

1. des expressions régulières (email, URL, IP, téléphone, @pseudo), très fiables
   pour des motifs au format strict ;
2. un modèle NER spaCy (PERSON, GPE, LOC, FAC) pour les noms propres et les lieux.

Les groupes ``NORP`` (nationalités, religions, groupes politiques) et les ``ORG``
(entreprises : ni données personnelles, ni signal utile) ne sont volontairement
PAS remplacés : ce ne sont pas des données directement identifiantes et leur suppression
détruirait le signal utile à la détection de toxicité (propos racistes, haineux...).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SPACY_MODEL = "en_core_web_sm"

# Étiquettes spaCy que l'on traite comme données personnelles, et leur étiquette finale.
NER_LABEL_MAP = {
    "PERSON": "PERSON",
    "GPE": "LOCATION",  # pays, villes, états
    "LOC": "LOCATION",  # lieux non administratifs
    "FAC": "LOCATION",  # bâtiments, aéroports, routes
}
# ORG est volontairement absent : une entreprise n'est pas une donnée personnelle au sens du
# RGPD et le modèle y classe du jargon (Wikipedia, UTC, POV...), ce qui sur-anonymiserait.

# Termes du jargon Wikipédia/internet que le NER confond avec des noms propres (insensible à la casse).
NER_STOPLIST = {
    "wikipedia", "wiki", "wp", "pov", "npov", "utc", "msm", "afd", "rfc", "talk", "ip",
}

# Motifs regex. L'ordre sert de priorité en cas de chevauchement.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# L'URL ne doit pas finir par une ponctuation (virgule, point final de phrase...).
_URL_RE = re.compile(r"(?:https?://|(?<![@\w.])www\.)[^\s<>\"')\]]*[^\s<>\"')\].,;:!?]", re.IGNORECASE)
_IP_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w/])\+?\d[\d\s().\-]{7,}\d(?!\w)")
_USERNAME_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b")

# Un téléphone doit avoir entre 9 et 15 chiffres (écarte dates, années, identifiants courts).
_PHONE_MIN_DIGITS = 9
_PHONE_MAX_DIGITS = 15

# Priorité : plus petit = prioritaire quand deux détections se chevauchent.
_SOURCE_PRIORITY = {"regex": 0, "ner": 1}
# Entre motifs regex : une URL/email/IP prime sur un téléphone ou un @pseudo qui le recouvre.
_LABEL_PRIORITY = {"URL": 0, "EMAIL": 1, "IP": 2, "USERNAME": 3, "PHONE": 4}


@dataclass(frozen=True)
class Span:
    """Une donnée personnelle détectée dans un texte."""

    start: int
    end: int
    label: str
    source: str  # "regex" ou "ner"
    text: str


# --------------------------------------------------------------------------- #
# Détection
# --------------------------------------------------------------------------- #


def find_regex_spans(text: str) -> list[Span]:
    """Détecte emails, URLs, IP, téléphones et @pseudos avec des expressions régulières."""
    spans: list[Span] = []

    for pattern, label in (
        (_EMAIL_RE, "EMAIL"),
        (_URL_RE, "URL"),
        (_IP_RE, "IP"),
        (_USERNAME_RE, "USERNAME"),
    ):
        for m in pattern.finditer(text):
            spans.append(Span(m.start(), m.end(), label, "regex", m.group()))

    for m in _PHONE_CANDIDATE_RE.finditer(text):
        digits = sum(c.isdigit() for c in m.group())
        if _PHONE_MIN_DIGITS <= digits <= _PHONE_MAX_DIGITS:
            spans.append(Span(m.start(), m.end(), "PHONE", "regex", m.group()))

    return spans


def find_ner_spans(doc) -> list[Span]:
    """Convertit les entités d'un ``Doc`` spaCy en ``Span`` (labels filtrés)."""
    spans: list[Span] = []
    for ent in doc.ents:
        label = NER_LABEL_MAP.get(ent.label_)
        if label is not None and ent.text.strip().lower() not in NER_STOPLIST:
            spans.append(Span(ent.start_char, ent.end_char, label, "ner", ent.text))
    return spans


def resolve_overlaps(spans: Iterable[Span]) -> list[Span]:
    """Supprime les chevauchements : regex prioritaire (URL > email > IP > pseudo > tél),
    puis NER, puis le span le plus long."""
    ordered = sorted(
        spans,
        key=lambda s: (
            _SOURCE_PRIORITY[s.source],
            _LABEL_PRIORITY.get(s.label, 9),
            -(s.end - s.start),
            s.start,
        ),
    )
    kept: list[Span] = []
    for span in ordered:
        if all(span.end <= k.start or span.start >= k.end for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def detect(text: str, doc=None) -> list[Span]:
    """Détecte toutes les données personnelles d'un texte (regex + NER si ``doc`` fourni)."""
    spans = find_regex_spans(text)
    if doc is not None:
        spans += find_ner_spans(doc)
    return resolve_overlaps(spans)


# --------------------------------------------------------------------------- #
# Remplacement
# --------------------------------------------------------------------------- #


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def make_pseudonym(label: str, value: str, key: bytes, length: int = 8) -> str:
    """Pseudonyme stable ``LABEL_<hmac>`` : même valeur + même clé => même pseudonyme."""
    digest = hmac.new(key, f"{label}:{_normalize(value)}".encode("utf-8"), hashlib.sha256)
    return f"{label}_{digest.hexdigest()[:length]}"


def _apply(text: str, spans: list[Span], replacer: Callable[[Span], str]) -> str:
    out: list[str] = []
    cursor = 0
    for span in spans:
        out.append(text[cursor : span.start])
        out.append(replacer(span))
        cursor = span.end
    out.append(text[cursor:])
    return "".join(out)


def anonymize_text(text: str, spans: list[Span]) -> str:
    """Version anonymisée : ``[PERSON]``, ``[EMAIL]``... (irréversible)."""
    return _apply(text, spans, lambda s: f"[{s.label}]")


def pseudonymize_text(
    text: str,
    spans: list[Span],
    key: bytes,
    mapping: dict[str, tuple[str, str]] | None = None,
) -> str:
    """Version pseudonymisée : ``PERSON_3fa9c1d2``.

    Si ``mapping`` est fourni, on y consigne ``pseudonyme -> (label, valeur originale)``.
    Cette table ré-identifie les personnes : elle doit être stockée séparément
    et protégée (jamais committée).
    """

    def replacer(span: Span) -> str:
        pseudo = make_pseudonym(span.label, span.text, key)
        if mapping is not None:
            mapping.setdefault(pseudo, (span.label, span.text))
        return pseudo

    return _apply(text, spans, replacer)


# --------------------------------------------------------------------------- #
# Pipeline complet sur une liste de textes
# --------------------------------------------------------------------------- #


def load_nlp(model: str = SPACY_MODEL):
    """Charge spaCy en ne gardant que ce qui sert au NER (plus rapide)."""
    import spacy

    return spacy.load(
        model, disable=["parser", "lemmatizer", "attribute_ruler", "tagger", "senter"]
    )


def process_texts(
    texts: list[str],
    nlp,
    key: bytes,
    mapping: dict[str, tuple[str, str]] | None = None,
    batch_size: int = 256,
    n_process: int = 1,
) -> Iterator[tuple[str, str, list[Span]]]:
    """Pour chaque texte, renvoie ``(version_anonymisée, version_pseudonymisée, spans)``."""
    cleaned = [t if isinstance(t, str) else "" for t in texts]
    docs = nlp.pipe(cleaned, batch_size=batch_size, n_process=n_process)
    for text, doc in zip(cleaned, docs):
        spans = detect(text, doc)
        yield (
            anonymize_text(text, spans),
            pseudonymize_text(text, spans, key, mapping),
            spans,
        )


def count_labels(all_spans: Iterable[list[Span]]) -> Counter:
    """Compte les données personnelles détectées par étiquette."""
    counter: Counter = Counter()
    for spans in all_spans:
        counter.update(s.label for s in spans)
    return counter
