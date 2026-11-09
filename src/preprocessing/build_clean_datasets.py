"""Génère les versions nettoyées des jeux de données anonymisés.

Usage (depuis la racine du repo) :

    python -m src.preprocessing.build_clean_datasets

Entrées  : data/anonymized/{train,val}_dataset.csv
Sorties  :
    data/cleaned/{train,val}_dataset.csv        -> minuscules, sans emojis / sauts de ligne / ponctuation
    reports/cleaning_before_after_sample.csv    -> exemples avant / après
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .cleaner import clean_text

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ("train", "val")
SAMPLE_SIZE = 25


def build(data_dir: Path, limit: int | None) -> None:
    out_dir = data_dir / "cleaned"
    reports_dir = ROOT / "reports"
    for d in (out_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    for split in SPLITS:
        src = data_dir / "anonymized" / f"{split}_dataset.csv"
        df = pd.read_csv(src, dtype={"id": str})
        if limit:
            df = df.head(limit)

        cleaned = df.copy()
        cleaned["comment_text"] = df["comment_text"].map(clean_text)
        cleaned.to_csv(out_dir / src.name, index=False)

        empty = int((cleaned["comment_text"] == "").sum())
        print(f"[{split}] {len(df)} commentaires nettoyés, {empty} vides après nettoyage")

        if split == "train":
            sample = pd.DataFrame({
                "id": df["id"].head(SAMPLE_SIZE),
                "before": df["comment_text"].head(SAMPLE_SIZE),
                "after": cleaned["comment_text"].head(SAMPLE_SIZE),
            })
            sample.to_csv(reports_dir / "cleaning_before_after_sample.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que N lignes par split (test rapide)")
    args = parser.parse_args()
    build(args.data_dir, args.limit)


if __name__ == "__main__":
    main()
