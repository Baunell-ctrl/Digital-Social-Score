"""Génère les versions anonymisées des jeux de données.

Usage (depuis la racine du repo) :

    python -m src.anonymization.build_datasets

Entrées  : data/train_dataset.csv, data/val_dataset.csv  (colonne ``comment_text``)
Sorties  :
    data/anonymized/{train,val}_dataset.csv      -> [PERSON], [EMAIL]...   (irréversible)
    reports/anonymization_stats.json             -> nombre de détections par étiquette
    reports/before_after_sample.csv              -> exemples avant / après
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pandas as pd

from .anonymizer import count_labels, load_nlp, process_texts

ROOT = Path(__file__).resolve().parents[2]
SPLITS = ("train", "val")
SAMPLE_SIZE = 25


def build(data_dir: Path, out_root: Path, limit: int | None, n_process: int) -> None:
    reports_dir = ROOT / "reports"
    anon_dir = data_dir / "anonymized"
    for d in (reports_dir, anon_dir):
        d.mkdir(parents=True, exist_ok=True)

    nlp = load_nlp()
    stats: dict[str, dict] = {}
    samples: list[dict] = []

    for split in SPLITS:
        src = data_dir / f"{split}_dataset.csv"
        df = pd.read_csv(src, dtype={"id": str})
        if limit:
            df = df.head(limit)

        t0 = time.time()
        results = list(process_texts(df["comment_text"].tolist(), nlp, n_process=n_process))
        elapsed = time.time() - t0

        anon_df = df.copy()
        anon_df["comment_text"] = [r[0] for r in results]
        anon_df.to_csv(anon_dir / src.name, index=False)

        counts = count_labels(r[1] for r in results)
        with_pii = sum(1 for r in results if r[1])
        stats[split] = {
            "comments": len(df),
            "comments_with_personal_data": with_pii,
            "share_with_personal_data": round(with_pii / len(df), 4),
            "detections_by_label": dict(sorted(counts.items())),
            "total_detections": sum(counts.values()),
            "seconds": round(elapsed, 1),
        }
        print(f"[{split}] {len(df)} commentaires, {with_pii} contenaient des données personnelles "
              f"({elapsed:.0f}s)  {dict(counts)}")

        if split == "train":
            # Exemples avant/après, uniquement des commentaires où quelque chose a été détecté.
            for i, r in enumerate(results):
                if r[1] and len(samples) < SAMPLE_SIZE:
                    samples.append({
                        "id": df.iloc[i]["id"],
                        "original": df.iloc[i]["comment_text"],
                        "anonymized": r[0],
                        "detected": "; ".join(f"{s.label}:{s.text}" for s in r[1]),
                    })

    (reports_dir / "anonymization_stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    pd.DataFrame(samples).to_csv(reports_dir / "before_after_sample.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que N lignes par split (test rapide)")
    parser.add_argument("--n-process", type=int, default=2, help="processus spaCy en parallèle")
    args = parser.parse_args()
    build(args.data_dir, ROOT, args.limit, args.n_process)


if __name__ == "__main__":
    main()
