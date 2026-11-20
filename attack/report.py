"""Affichage lisible d'un rapport JSON produit par attack/flood.py.

    python attack/report.py                                   # dernier run de reports/attack/
    python attack/report.py reports/attack/flood_test_.../     # un run precis (dossier ou rapport.json)
    python attack/report.py ... --out resume.txt               # sauvegarde en plus de l'affichage

Reprend le JSON de flood.py (cible, codes, latences, timeline) et le met en forme :
en-tete, resume global (meme diagnostic que flood.py), puis la chronologie seconde
par seconde.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "attack"

COLS = (f"{'temps':>6}  {'envoyees':>9}  {'req/s':>6}  {'200':>6}  {'429':>6}  {'5xx':>5}  "
        f"{'timeout':>7}  {'P50':>7}  {'P95':>7}  {'P99':>7}  {'infer.srv':>9}")


SEUIL_SATURATION = 0.01   # au-dela de 1 % d'erreurs (ou au premier timeout), l'API est consideree saturee


def verdict(codes: dict) -> str:
    """Conclusion d'une ligne. Les 503 (refus volontaires du limiteur) et 429 ne sont pas des erreurs."""
    total = sum(codes.values()) or 1
    erreurs = codes.get("5xx", 0) + codes.get("timeout", 0) + codes.get("erreur_reseau", 0)
    if codes.get("timeout") or erreurs / total >= SEUIL_SATURATION:
        return "  -> des 5xx/timeouts : l'API a ete saturee (defense insuffisante ou absente)."
    if erreurs:
        return (f"  -> {erreurs} erreurs 5xx/reseau ({erreurs / total:.3%}), aucun timeout : erreurs marginales, "
                "l'API a tenu (seuil de saturation : 1 %).")
    if codes.get("429"):
        return "  -> surtout des 429 : la limite de requetes tient. Comparez avec/sans defense."
    return "  -> aucune erreur."


def render_report(report: dict) -> str:
    """Construit le texte lisible d'un rapport flood.py (dict charge depuis rapport.json)."""
    label = report.get("label") or "(sans etiquette)"
    codes = report.get("codes", {})
    lat = report.get("latence_client_ms", {})

    lines = [
        f"Attaque cout asymetrique -> {report['cible']}",
        f"profil={report['profil']}  workers={report['workers']}  duree={report['duree_s']}s  "
        f"label={label}  debut={report.get('debut', '?')}",
        "",
        "=== Resume ===",
        f"  envoyees        : {report['total_envoyees']}  ({report['debit_moyen_req_s']} req/s en moyenne)",
        f"  codes           : {codes}",
        f"  latence client  : P50 {lat.get('p50', 0):.0f} ms  P95 {lat.get('p95', 0):.0f} ms  P99 {lat.get('p99', 0):.0f} ms"
        "  (toutes reponses)",
    ]
    ok = report.get("latence_client_200_ms")
    if ok:  # absent des rapports produits avant l'ajout de cette mesure
        lines.append(f"  latence des 200 : P50 {ok['p50']:.0f} ms  P95 {ok['p95']:.0f} ms  P99 {ok['p99']:.0f} ms"
                     "  (service rendu)")
    lines += [
        f"  inference serveur: {report.get('inference_serveur_ms_moyenne', 0):.0f} ms en moyenne (reponses 200)",
    ]
    t = report.get("temoins")
    if t:
        lines.append(f"  temoins         : {t['taux_de_succes']:.0%} de succes sur {t['envoyees']} messages, "
                     f"latence P50 {t['latence_200_ms']['p50']:.0f} ms  P95 {t['latence_200_ms']['p95']:.0f} ms  "
                     f"codes {t['codes']}")
    lines.append(verdict(codes))
    lines.append("")

    timeline = report.get("timeline", [])
    if timeline:
        lines.append("=== Chronologie (1 ligne / seconde) ===")
        lines.append(COLS)
        for row in timeline:
            c = row.get("codes", {})
            m, s = divmod(int(row.get("t", 0)), 60)
            lines.append(
                f"{m:>3}:{s:02d}  {row.get('envoyees', 0):>9}  {row.get('req_s', 0):>6.1f}  "
                f"{c.get('200', 0):>6}  {c.get('429', 0):>6}  {c.get('5xx', 0):>5}  {c.get('timeout', 0):>7}  "
                f"{row.get('p50_ms', 0):>6.0f}m  {row.get('p95_ms', 0):>6.0f}m  {row.get('p99_ms', 0):>6.0f}m  "
                f"{row.get('inference_ms', 0):>7.0f}m"
            )
    else:
        lines.append("(pas de chronologie : run trop court ou vide)")

    return "\n".join(lines) + "\n"


def _resolve(path: Path) -> Path:
    """Si path est un dossier de run, renvoie son rapport.json ; sinon renvoie path tel quel."""
    if path.is_dir():
        candidate = path / "rapport.json"
        if not candidate.exists():
            raise SystemExit(f"pas de rapport.json dans {path}")
        return candidate
    return path


def _latest() -> Path:
    """Rapport le plus recent dans reports/attack/ : dossiers de run ou anciens fichiers a plat."""
    candidates = list(OUT_DIR.glob("*.json")) + list(OUT_DIR.glob("*/rapport.json"))
    if not candidates:
        raise SystemExit(f"aucun rapport trouve dans {OUT_DIR}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("chemin", nargs="?", help="rapport.json ou dossier de run (defaut : le plus recent)")
    ap.add_argument("--out", help="sauvegarder le texte dans ce fichier en plus de l'affichage")
    args = ap.parse_args()

    path = _resolve(Path(args.chemin)) if args.chemin else _latest()
    report = json.loads(path.read_text(encoding="utf-8"))
    text = render_report(report)

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    print(text)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
