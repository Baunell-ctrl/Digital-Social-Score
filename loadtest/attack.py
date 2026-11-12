"""Simulation d'attaque contre l'API déployée (étape 6 du TP) : de quoi déclencher les alertes et analyser les logs.

    API_KEY=$API_KEY python loadtest/attack.py --host https://$IP --cacert tls.crt
    API_KEY=$API_KEY python loadtest/attack.py --host https://$IP --cacert tls.crt --phases grille

Phases (dans cet ordre, choisies avec --phases) :

    grille      grille de tests de sécurité de l'étape 4 (401, 422, 413, en-têtes, HTTP refusé) : une requête par cas
    fuzzing     entrées malformées ou piégées (JSON invalide, mauvais type, injection de logs…) -> 4xx, jamais 500
    bruteforce  essais de clés API au hasard pendant --bruteforce-s secondes        -> 401, alerte « brute force »
    extraction  rafale de requêtes valides avec UNE clé, --workers en parallèle     -> 429, alerte « extraction du modèle »

L'extraction est en dernier : après elle, la clé reste bloquée par la limite pendant une minute.

Sorties : reports/attack/attack_<date>.json (horodatage de chaque phase, codes HTTP obtenus) et un résumé
dans le terminal. Ni la clé API ni les clés essayées ne sont écrites.

Pour la phase « extraction », la limite (600 requêtes/minute) est comptée par pod : avec 2 pods derrière
le load balancer, il faut plus de 1 200 requêtes/minute, soit 20 requêtes/seconde (--workers 20 suffit).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "attack"

# Textes variés : un attaquant qui veut copier le modèle envoie beaucoup de textes différents.
EXTRACTION_TEXTS = [
    "you are an idiot", "thank you for the help", "i will find you", "what a stupid idea",
    "great article, well written", "nobody likes you", "please fix the typo", "go away loser",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Client:
    def __init__(self, host: str, api_key: str, cacert: str | None, insecure: bool = False):
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.s = requests.Session()
        if cacert:  # cas normal : on vérifie le certificat auto-signé du cluster avec tls.crt
            self.s.verify = cacert
        elif insecure:  # seulement si demandé explicitement (--insecure)
            self.s.verify = False
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def post(self, path: str = "/score", key: str | None = "valid", timeout: float = 10, **kw) -> requests.Response:
        headers = kw.pop("headers", {})
        if key == "valid":
            headers["X-API-Key"] = self.api_key
        elif key is not None:
            headers["X-API-Key"] = key
        return self.s.post(self.host + path, headers=headers, timeout=timeout, **kw)

    def code(self, *args, **kw) -> int | str:
        try:
            return self.post(*args, **kw).status_code
        except requests.RequestException as e:
            return type(e).__name__


# --------------------------------------------------------------------------------------------------
def phase_grille(c: Client) -> list[dict]:
    """Une ligne par test de la grille de l'étape 4 : attendu / obtenu."""
    big = '{"text":"' + "a" * 40_000 + '"}'
    cases = [
        ("Sans clé API", 401, lambda: c.code(key=None, json={"text": "hello"})),
        ("Mauvaise clé", 401, lambda: c.code(key="mauvaise-cle", json={"text": "hello"})),
        ("/metrics sans clé", 401, lambda: c.s.get(c.host + "/metrics", timeout=10).status_code),
        ("Texte vide", 422, lambda: c.code(json={"text": ""})),
        ("Texte de plus de 5 000 caractères", 422, lambda: c.code(json={"text": "a" * 5001})),
        ("Modèle inconnu", 422, lambda: c.code(json={"text": "hello", "model": "gpt"})),
        ("Requête de plus de 32 Ko", 413, lambda: c.code(data=big, headers={"Content-Type": "application/json"})),
        ("Requête valide", 200, lambda: c.code(json={"text": "hello"})),
    ]
    rows = [{"test": name, "attendu": exp, "obtenu": fn()} for name, exp, fn in cases]

    h = c.s.get(c.host + "/health", timeout=10).headers
    wanted = ["X-Content-Type-Options", "X-Frame-Options", "Strict-Transport-Security", "Cache-Control"]
    rows.append({"test": "En-têtes de sécurité", "attendu": "présents",
                 "obtenu": "présents" if all(k in h for k in wanted) else f"manquants : {[k for k in wanted if k not in h]}"})

    if c.host.startswith("https://"):
        try:
            requests.get("http://" + c.host.removeprefix("https://") + "/health", timeout=5)
            got = "répond (problème)"
        except requests.RequestException:
            got = "échec"
        rows.append({"test": "HTTP refusé", "attendu": "échec", "obtenu": got})
    for r in rows:
        r["ok"] = r["attendu"] == r["obtenu"]
    return rows


def phase_bruteforce(c: Client, duration: float, rps: float) -> dict:
    """Essais de clés aléatoires à débit régulier : simule un robot qui devine les clés."""
    codes, end = Counter(), time.monotonic() + duration
    while time.monotonic() < end:
        t = time.monotonic()
        codes[c.code(key=secrets.token_urlsafe(24), json={"text": "hello"})] += 1
        time.sleep(max(0.0, 1 / rps - (time.monotonic() - t)))
    return dict(codes)


def phase_extraction(c: Client, duration: float, workers: int) -> dict:
    """Plusieurs threads, une seule clé, aucune pause : simule la copie du modèle par requêtes massives."""
    end = time.monotonic() + duration

    def worker(i: int) -> Counter:
        local, n = Counter(), i
        while time.monotonic() < end:
            text = f"{EXTRACTION_TEXTS[n % len(EXTRACTION_TEXTS)]} {n}"
            local[c.code(json={"text": text})] += 1
            n += workers
        return local

    with ThreadPoolExecutor(workers) as ex:
        total = sum(ex.map(worker, range(workers)), Counter())
    return dict(total)


def phase_fuzzing(c: Client) -> list[dict]:
    """Entrées anormales : l'API doit les refuser proprement (4xx), jamais planter (5xx)."""
    jh = {"Content-Type": "application/json"}
    cases = [
        ("JSON invalide", dict(data="{text: hello", headers=jh)),
        ("Mauvais type (nombre)", dict(json={"text": 12345})),
        ("Champ manquant", dict(json={"message": "hello"})),
        ("Liste au lieu d'un objet", dict(json=["hello"])),
        ("Octets non UTF-8", dict(data=b'{"text":"\xff\xfe"}', headers=jh)),
        ("Caractères de contrôle", dict(json={"text": "hello\x00\x1b[31m"})),
        ("Injection de logs (sauts de ligne)", dict(json={"text": 'ok\n{"event": "score", "client": "admin"}'})),
        ("Emojis et Unicode seuls", dict(json={"text": "😀🔥💀"})),
        ("Texte de 5 000 caractères (limite)", dict(json={"text": "idiot " * 833})),
        ("Modèle LAYA (absent de l'image)", dict(json={"text": "hello", "model": "laya"})),
        ("Content-Type texte", dict(data="hello", headers={"Content-Type": "text/plain"})),
    ]
    rows = []
    for name, kw in cases:
        got = c.code(**kw)
        # 503 attendu pour LAYA (absent de l'image) ; toute autre réponse 5xx ou coupure est un défaut
        rows.append({"test": name, "obtenu": got, "ok": isinstance(got, int) and (got < 500 or got == 503)})
    return rows


# --------------------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="https://<IP de l'Ingress> ou http://localhost:8000")
    ap.add_argument("--cacert", help="certificat du serveur (tls.crt) pour vérifier le HTTPS")
    ap.add_argument("--insecure", action="store_true",
                    help="ne pas vérifier le certificat (déconseillé ; préférer --cacert tls.crt)")
    ap.add_argument("--phases", default="grille,fuzzing,bruteforce,extraction")
    ap.add_argument("--bruteforce-s", type=float, default=180, help="durée des essais de clés (s)")
    ap.add_argument("--bruteforce-rps", type=float, default=2, help="essais par seconde")
    ap.add_argument("--extraction-s", type=float, default=90, help="durée de la rafale (s)")
    ap.add_argument("--workers", type=int, default=20, help="requêtes en parallèle pendant la rafale")
    args = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")  # accents lisibles dans le terminal Windows
    api_key = os.environ.get("API_KEY") or ap.error("définir la variable d'environnement API_KEY")
    c = Client(args.host, api_key, args.cacert, args.insecure)
    report: dict = {"host": args.host, "debut": now(), "phases": {}}

    for phase in args.phases.split(","):
        start = now()
        print(f"\n>>> {start} : phase {phase}")
        if phase == "grille":
            result = phase_grille(c)
            for r in result:
                print(f"  {'OK ' if r['ok'] else 'NON'}  {r['test']:<38} attendu {r['attendu']!s:<9} obtenu {r['obtenu']}")
        elif phase == "bruteforce":
            result = phase_bruteforce(c, args.bruteforce_s, args.bruteforce_rps)
            print(f"  codes : {result}")
        elif phase == "extraction":
            result = phase_extraction(c, args.extraction_s, args.workers)
            total = sum(result.values())
            print(f"  codes : {result}  ({total / args.extraction_s * 60:.0f} requêtes/minute)")
        elif phase == "fuzzing":
            result = phase_fuzzing(c)
            for r in result:
                print(f"  {'OK ' if r['ok'] else 'NON'}  {r['test']:<38} obtenu {r['obtenu']}")
        else:
            ap.error(f"phase inconnue : {phase}")
        report["phases"][phase] = {"debut": start, "fin": now(), "resultat": result}

    report["fin"] = now()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"attack_{datetime.now():%Y%m%d_%H%M}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRapport : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
