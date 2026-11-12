"""Graphes et tableau de synthèse à partir des CSV de Locust (reports/loadtest/*.csv).

    python loadtest/plot.py                 # tous les scénarios trouvés
    python loadtest/plot.py ramp stress     # certains scénarios

Sorties : reports/images/loadtest_<scénario>.png et un tableau Markdown affiché dans le terminal
(à recopier dans le README).
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = ROOT / "reports" / "loadtest"
IMG_DIR = ROOT / "reports" / "images"
SCENARIOS = ["gke_baseline", "gke_ramp", "gke_stress", "gke_failover", "gke_ratelimit_5users", "gke_ratelimit"]

BLUE, ORANGE, RED, GREY = "#2563eb", "#d97706", "#dc2626", "#6b7280"


def load_history(name: str) -> pd.DataFrame:
    df = pd.read_csv(CSV_DIR / f"{name}_stats_history.csv")
    df = df[df["Name"] == "Aggregated"].copy()
    df["t"] = df["Timestamp"] - df["Timestamp"].iloc[0]
    for col in ("50%", "95%", "99%", "Requests/s", "Failures/s"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_optional(file: str) -> pd.DataFrame | None:
    path = CSV_DIR / file
    return pd.read_csv(path) if path.exists() else None


def plot(name: str) -> None:
    h = load_history(name)
    t0, t1 = h["Timestamp"].iloc[0], h["Timestamp"].iloc[-1]
    hpa = load_optional("gke_hpa.csv")
    if hpa is not None:
        hpa = hpa[(hpa["timestamp"] >= t0) & (hpa["timestamp"] <= t1)].copy()
        hpa["t"] = hpa["timestamp"] - t0
        for c in ("replicas", "ready", "cpu_percent"):
            hpa[c] = pd.to_numeric(hpa[c], errors="coerce")
    ev = load_optional("gke_events.csv")
    if ev is not None:
        ev = ev[(ev["timestamp"] > t0) & (ev["timestamp"] < t1) & ev["event"].str.startswith("suppression")]

    n = 4 if hpa is not None and len(hpa) else 3
    fig, ax = plt.subplots(n, 1, figsize=(9, 2.6 * n + 0.4), sharex=True, constrained_layout=True)

    ax[0].plot(h["t"], h["User Count"], color=GREY)
    ax[0].set_ylabel("utilisateurs simultanés")
    ax2 = ax[0].twinx()
    ax2.plot(h["t"], h["Requests/s"], color=BLUE)
    ax2.set_ylabel("requêtes par seconde", color=BLUE)
    ax[0].set_title(f"Scénario « {name} » : charge envoyée (gris) et débit obtenu (bleu)")

    ax[1].plot(h["t"], h["50%"], color=BLUE, label="P50 (médiane)")
    ax[1].plot(h["t"], h["95%"], color=ORANGE, label="P95")
    ax[1].plot(h["t"], h["99%"], color=RED, label="P99")
    ax[1].set_ylabel("latence (ms)")
    ax[1].set_title("Latence côté client")
    ax[1].legend(loc="upper left")

    ax[2].plot(h["t"], h["Failures/s"], color=RED)
    ax[2].set_ylabel("erreurs par seconde")
    ax[2].set_xlabel("temps (s)")
    ax[2].set_title("Erreurs (réponses autres que 200)")

    if n == 4:
        ax[3].step(hpa["t"], hpa["replicas"], where="post", color=GREY, label="pods voulus (HPA)")
        ax[3].step(hpa["t"], hpa["ready"], where="post", color=BLUE, label="pods prêts")
        ax[3].set_ylabel("pods")
        ax[3].set_ylim(bottom=0)
        ax4 = ax[3].twinx()
        ax4.plot(hpa["t"], hpa["cpu_percent"], color=ORANGE, alpha=0.7)
        ax4.axhline(70, color=ORANGE, ls=":", lw=1)
        ax4.set_ylabel("CPU moyen (% demandé)", color=ORANGE)
        ax[3].set_title("Autoscaling : pods (gris = voulus, bleu = prêts), CPU (orange, seuil 70 %)")
        ax[3].legend(loc="upper left")
        ax[3].set_xlabel("temps (s)")
        ax[2].set_xlabel("")

    for a in ax:
        a.grid(alpha=0.25)
        if ev is not None:
            for _, e in ev.iterrows():
                a.axvline(e["timestamp"] - t0, color=RED, ls="--", lw=1)
    if ev is not None and len(ev):
        ax[0].set_title(ax[0].get_title() + "\n(tirets rouges : suppression de pods)")
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    out = IMG_DIR / f"loadtest_{name}.png"
    fig.savefig(out, dpi=130)
    plt.close(fig)
    print(f"graphe : {out.relative_to(ROOT)}")


def summary(names: list[str]) -> None:
    print("\n| Scénario | Requêtes | Req/s moyen | Moyenne (ms) | P50 | P95 | P99 | Max (ms) | Erreurs |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name in names:
        s = pd.read_csv(CSV_DIR / f"{name}_stats.csv")
        a = s[s["Name"] == "Aggregated"].iloc[0]
        n, f = int(a["Request Count"]), int(a["Failure Count"])
        print(f"| {name} | {n} | {a['Requests/s']:.1f} | {a['Average Response Time']:.0f} | {a['50%']:.0f} | "
              f"{a['95%']:.0f} | {a['99%']:.0f} | {a['Max Response Time']:.0f} | {f} ({100 * f / max(n, 1):.1f} %) |")


if __name__ == "__main__":
    names = sys.argv[1:] or [n for n in SCENARIOS if (CSV_DIR / f"{n}_stats.csv").exists()]
    for n in names:
        plot(n)
    summary(names)
