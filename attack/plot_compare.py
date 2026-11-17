"""Graphique avant/après défense, à partir de deux rapports de attack/flood.py (TP2, étape 3).

    python attack/plot_compare.py reports/attack/flood_20261116_110433 reports/attack/flood_defense_<horodatage>

Lit le champ ``timeline`` de chaque ``rapport.json`` (une ligne par seconde : P50/P95/P99 côté client)
et trace les deux runs sur le même graphique, pour comparer visuellement l'effet de la défense.

Sortie : reports/images/attack_defense_comparaison.png (non versionné, comme les graphes loadtest/plot.py).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
IMG_DIR = ROOT / "reports" / "images"

BLUE, ORANGE, RED = "#2563eb", "#d97706", "#dc2626"


def _resolve(path: Path) -> Path:
    """Si path est un dossier de run, renvoie son rapport.json ; sinon renvoie path tel quel."""
    if path.is_dir():
        candidate = path / "rapport.json"
        if not candidate.exists():
            raise SystemExit(f"pas de rapport.json dans {path}")
        return candidate
    return path


def load(path: Path) -> dict:
    return json.loads(_resolve(path).read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("avant", help="rapport.json ou dossier de run, sans défense")
    ap.add_argument("apres", help="rapport.json ou dossier de run, avec défense")
    ap.add_argument("--out", default=str(IMG_DIR / "attack_defense_comparaison.png"))
    args = ap.parse_args()

    avant, apres = load(Path(args.avant)), load(Path(args.apres))

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for report, style in ((avant, "-"), (apres, "--")):
        label = report.get("label") or ("avant" if style == "-" else "après")
        t = [row["t"] for row in report["timeline"]]
        ax.plot(t, [row["p50_ms"] / 1000 for row in report["timeline"]], style, color=BLUE,
                label=f"P50 ({label})")
        ax.plot(t, [row["p95_ms"] / 1000 for row in report["timeline"]], style, color=ORANGE,
                label=f"P95 ({label})")
        ax.plot(t, [row["p99_ms"] / 1000 for row in report["timeline"]], style, color=RED,
                label=f"P99 ({label})")

    ax.set_xlabel("temps depuis le début de l'attaque (s)")
    ax.set_ylabel("latence côté client (s)")
    ax.set_title("Attaque par coût asymétrique : latence avant / après la défense\n"
                 "(trait plein = sans défense, pointillé = avec défense)")
    ax.legend(loc="upper right", fontsize=8, ncol=2)

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=130)
    print(f"Graphique : {Path(args.out).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
