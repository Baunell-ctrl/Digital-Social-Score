"""Simulation de montée en charge de l'API Digital Social Score (étape 5 du TP).

Un seul fichier, plusieurs scénarios choisis avec la variable SCENARIO :

    baseline   1 utilisateur pendant 1 minute : latence « à vide », point de comparaison
    ramp       croissance progressive : 5 -> 20 -> 50 -> 100 utilisateurs
    stress     montée jusqu'à 300 utilisateurs pour trouver le point de rupture
    constant   charge fixe (CONSTANT_USERS, 40 par défaut) : sert au test de panne (failover)
    ratelimit  une seule clé, RATELIMIT_USERS utilisateurs sans pause (5 par défaut) : au-delà de 600 requêtes/minute
               sur un pod, la limite doit répondre 429 (le compteur est par pod : prévoir plus d'utilisateurs avec 10 pods)

Exemple (API lancée avec API_KEY=test) :

    SCENARIO=ramp API_KEY=test locust -f loadtest/locustfile.py --headless \\
        --host http://localhost:8000 --csv reports/loadtest/ramp --csv-full-history \\
        --html reports/loadtest/ramp.html

Contre le cluster (certificat auto-signé accepté : on mesure la charge, pas le certificat) :

    SCENARIO=ramp API_KEY=$API_KEY locust -f loadtest/locustfile.py --headless --host https://$IP ...

La durée du test est fixée par le scénario (LoadTestShape) : pas besoin de --users ni --run-time.
Toute réponse autre que 200 (401, 429, 5xx, délai dépassé) est comptée comme une erreur.
"""

import os
import random

from locust import HttpUser, LoadTestShape, between, constant, task

API_KEY = os.environ.get("API_KEY", "")
SCENARIO = os.environ.get("SCENARIO", "ramp")

# Textes du README (partie « Résultats des tests sur le cloud ») : un mélange de toxiques et de neutres.
TEXTS = [
    "Honestly you are the dumbest person I have ever had the misfortune to talk to",
    "I swear if I ever see you at that bar again I am going to beat you up",
    "Look at this clown thinking his opinion matters",
    "Nice profile picture, sweetheart. Send me something a little more revealing",
    "You are a complete waste of oxygen",
    "Thank you so much for your help, I really appreciate it",
    "I disagree with this change, but I understand your reasoning and I see your point",
    "Great update, the new version is much faster than the previous one",
    "Could you explain how the second paragraph relates to the main argument?",
    "Have a nice day and thanks for the quick answer",
]

# (fin du palier en secondes, nombre d'utilisateurs, utilisateurs ajoutés par seconde)
STAGES = {
    "baseline": [(60, 1, 1)],
    "ramp": [(60, 5, 1), (150, 20, 1), (240, 50, 2), (330, 100, 2), (420, 100, 2)],
    "stress": [(60, 20, 5), (120, 60, 5), (180, 120, 10), (240, 200, 10), (300, 300, 10), (360, 300, 10)],
    "constant": [(240, int(os.environ.get("CONSTANT_USERS", "40")), 5)],
    "ratelimit": [(90, int(os.environ.get("RATELIMIT_USERS", "5")), 20)],
}


class ApiUser(HttpUser):
    # Un utilisateur réel lit le résultat avant d'envoyer le texte suivant.
    # Le test de limite (ratelimit) enchaîne en revanche les requêtes sans pause.
    wait_time = constant(0.05) if SCENARIO == "ratelimit" else between(0.2, 0.6)

    def on_start(self):
        self.client.verify = False  # certificat auto-signé du cluster
        self.headers = {"X-API-Key": API_KEY}

    @staticmethod
    def pick() -> str:
        # un suffixe aléatoire évite que le même texte exact soit rejoué : pas de biais de cache éventuel
        return random.choice(TEXTS) + " " + str(random.randint(0, 9999))

    def _post(self):
        with self.client.post("/score", json={"text": self.pick()}, headers=self.headers,
                              name="POST /score", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")

    @task
    def score(self):
        self._post()


class Shape(LoadTestShape):
    stages = STAGES[SCENARIO]

    def tick(self):
        t = self.get_run_time()
        for end, users, spawn_rate in self.stages:
            if t < end:
                return users, spawn_rate
        return None  # fin du test
