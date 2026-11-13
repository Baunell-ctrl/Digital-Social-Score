#!/usr/bin/env bash
# Simulation d'attaque complète contre l'API déployée (étape 6) : grille, fuzzing, brute force, extraction.
# Environ 5 minutes ; déclenche les alertes « brute force » et « extraction du modèle ».
#
#   bash deploy/attack.sh           (ou : make attack)
#   PHASES=grille,fuzzing bash deploy/attack.sh
source "$(dirname "$0")/lib.sh"
need kubectl
HOST=https://$(api_host)   # déduit de l'IP réservée maintenant : la clé n'est envoyée qu'à notre load balancer
API_KEY=$(kubectl get secret dss-secrets -o jsonpath='{.data.API_KEY}' | base64 -d)
API_KEY=$API_KEY "${PYTHON:-python3}" loadtest/attack.py --host "$HOST" --phases "${PHASES:-grille,fuzzing,bruteforce,extraction}"
