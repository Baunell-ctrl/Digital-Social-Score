"""Rassemble les métriques des 4 modèles dans reports/model_comparison.md.

Usage :  python -m src.models.compare
"""

from __future__ import annotations

import json

from .common import LABELS, REPORTS_DIR, ROOT

MODELS = [
    ("lstm", "LSTM (statistique)"),
    ("bert", "BERT (transformer)"),
    ("laya_zeroshot", "LAYA non entraîné (décisionnaire)"),
    ("laya_finetuned", "LAYA entraîné (décisionnaire)"),
]


def fmt(v, digits=3) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def main() -> None:
    reports = {}
    for key, _ in MODELS:
        path = REPORTS_DIR / f"{key}_metrics.json"
        if path.exists():
            reports[key] = json.loads(path.read_text(encoding="utf-8"))

    lines = ["# Comparaison des modèles (jeu de test : val_dataset.csv, 6 490 commentaires)", "",
             "Seuil de décision : 0,5. Score de l'API = P(toxic) × 100.", "",
             "## Étiquette `toxic` (score de l'API)", "",
             "| Modèle | Précision | Rappel | F1 | ROC-AUC |", "| --- | --- | --- | --- | --- |"]
    for key, name in MODELS:
        if key in reports:
            t = reports[key]["test"]["toxic"]
            lines.append(f"| {name} | {fmt(t['precision'])} | {fmt(t['recall'])} | {fmt(t['f1'])} | {fmt(t['roc_auc'])} |")

    lines += ["", "## Moyenne macro sur les 6 étiquettes", "",
              "| Modèle | Précision | Rappel | F1 | ROC-AUC |", "| --- | --- | --- | --- | --- |"]
    for key, name in MODELS:
        if key in reports:
            m = reports[key]["test"]["macro"]
            lines.append(f"| {name} | {fmt(m['precision'])} | {fmt(m['recall'])} | {fmt(m['f1'])} | {fmt(m['roc_auc'])} |")

    lines += ["", "## F1 par étiquette", "", "| Modèle | " + " | ".join(LABELS) + " |",
              "| --- |" + " --- |" * len(LABELS)]
    for key, name in MODELS:
        if key in reports:
            pl = reports[key]["test"]["per_label"]
            lines.append(f"| {name} | " + " | ".join(fmt(pl[lab]["f1"]) for lab in LABELS) + " |")

    lines += ["", "## Temps de traitement", "",
              "| Modèle | Paramètres | Entraînement | Test complet | ms/texte GPU (lot 1) | ms/texte GPU (lot 64) "
              "| ms/texte CPU (lot 1) | ms/texte CPU (lot 64) |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for key, name in MODELS:
        if key in reports:
            r, lat = reports[key], reports[key]["latency"]
            params = f"{r['params'] / 1e6:.1f} M" if "params" in r else "≈ 421 M"
            lines.append(
                f"| {name} | {params} | {r['train_seconds']:.0f} s | {r['test_seconds']:.1f} s | "
                f"{fmt(lat.get('cuda_batch1_ms'), 2)} | {fmt(lat.get('cuda_batch64_ms'), 2)} | "
                f"{fmt(lat.get('cpu_batch1_ms'), 2)} | {fmt(lat.get('cpu_batch64_ms'), 2)} |")

    devices = {r.get("device") for r in reports.values()}
    lines += ["", f"Matériel : {', '.join(sorted(d for d in devices if d))}.", ""]
    out = ROOT / "reports" / "model_comparison.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
