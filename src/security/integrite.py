"""Défense contre l'empoisonnement et le remplacement du modèle (cours 5, § 16 : hash, provenance, audit).

- Empreintes SHA-256 des fichiers du modèle et des jeux de données, écrites dans artifacts/manifest.json au
  moment de l'export. L'API les vérifie au démarrage et REFUSE DE DÉMARRER si un fichier a changé : un
  modèle remplacé ou un vocabulaire modifié (porte dérobée, poids empoisonnés) ne peut pas être servi.
- Audit du jeu d'entraînement : part de toxiques (règle du OU), textes vides, doublons, et surtout doublons
  aux étiquettes CONTRADICTOIRES, signe classique d'un étiquetage empoisonné.

    python -m src.security.integrite            # vérifie les empreintes et audite les données
    python -m src.security.integrite --ecrire   # recalcule le manifeste (après un nouvel entraînement)
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"
MANIFESTE = ARTIFACTS / "manifest.json"
FICHIERS_MODELE = ("lstm.pt", "vocab.json", "config.json")
JEUX = ("data/cleaned/train_dataset.csv", "data/cleaned/val_dataset.csv")
LABELS = ["toxic", "severe_toxic", "obscene", "threat", "insult", "identity_hate"]


def empreinte(chemin: Path) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def ecrire_manifeste() -> dict:
    manifeste = {
        "date": date.today().isoformat(),
        "modele": {nom: empreinte(ARTIFACTS / nom) for nom in FICHIERS_MODELE},
        "donnees": {jeu: empreinte(ROOT / jeu) for jeu in JEUX if (ROOT / jeu).exists()},
    }
    MANIFESTE.write_text(json.dumps(manifeste, indent=2) + "\n")
    return manifeste


def verifier_artefacts(dossier: Path = ARTIFACTS, manifeste: Path | None = None) -> None:
    """Lève RuntimeError si un fichier du modèle ne correspond pas à son empreinte."""
    attendu = json.loads((manifeste or dossier / "manifest.json").read_text())["modele"]
    for nom, h in attendu.items():
        if empreinte(dossier / nom) != h:
            raise RuntimeError(f"Fichier du modèle modifié : {nom} (empreinte SHA-256 différente du manifeste). "
                               "L'API refuse de démarrer.")


def auditer_jeu(chemin: Path) -> dict:
    """Statistiques de contrôle d'un jeu d'entraînement."""
    import pandas as pd

    df = pd.read_csv(chemin)
    label = (df[LABELS].sum(axis=1) > 0).astype(int)
    texte = df["comment_text"].fillna("").astype(str).str.strip()
    groupes = pd.DataFrame({"t": texte, "l": label})[texte.duplicated(keep=False)].groupby("t")["l"].nunique()
    return {
        "lignes": len(df),
        "part_toxique": round(float(label.mean()), 3),
        "textes_vides": int((texte == "").sum()),
        "doublons": int(texte.duplicated().sum()),
        "doublons_contradictoires": int((groupes > 1).sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ecrire", action="store_true", help="recalculer artifacts/manifest.json")
    args = ap.parse_args()
    if args.ecrire:
        print(json.dumps(ecrire_manifeste(), indent=2))
        return
    verifier_artefacts()
    print("modèle : empreintes conformes au manifeste")
    donnees = json.loads(MANIFESTE.read_text())["donnees"]
    for jeu, h in donnees.items():
        etat = "conforme" if empreinte(ROOT / jeu) == h else "MODIFIÉ"
        print(f"{jeu} : {etat} · {auditer_jeu(ROOT / jeu)}")


if __name__ == "__main__":
    main()
