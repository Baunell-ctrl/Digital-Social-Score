#!/usr/bin/env bash
# Étape 6, partie 4.4 : attaque AVEC des utilisateurs témoins, pour mesurer l'effet sur les autres clients.
# Environ 12 minutes. À lancer dans Cloud Shell après : make client NAME=temoins
#
#   bash deploy/attack_temoins.sh           (ou : make attack-temoins)
#
#   0 à 3 min    témoins seuls (référence)
#   3 à 6 min    témoins + brute force (clés au hasard, 2 par seconde)
#   6 à 8 min    témoins seuls (retour au calme)
#   8 à 10 min   témoins + flood d'une clé valide (extraction, 20 requêtes en parallèle)
#   10 à 12 min  témoins seuls
#
# Témoins : 10 utilisateurs Locust, un message toutes les 5 s, avec la clé du client "temoins".
# Attaquant : loadtest/attack.py avec la clé principale (client "default"), qui sera bloquée par la limite.
# Sorties : reports/attack/temoins_<date>_* (Locust, latence des témoins seuls) et attack_<date>.json (attaquant).
# Les horaires de chaque phase sont affichés : ils servent à cadrer les captures Grafana.
source "$(dirname "$0")/lib.sh"
need kubectl
PY=${PYTHON:-python3}
LOCUST=${LOCUST:-locust}
command -v "$LOCUST" >/dev/null || die "locust introuvable : pip install -r requirements-loadtest.txt"
HOST=https://$(api_host)
API_KEY=$(kubectl get secret dss-secrets -o jsonpath='{.data.API_KEY}' | base64 -d)
gcloud secrets describe dss-client-temoins-key >/dev/null 2>&1 || die "client temoins absent : make client NAME=temoins"
TEMOINS_KEY=$(gcloud secrets versions access latest --secret=dss-client-temoins-key)
LIMIT=$(kubectl get deployment dss-api -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="RATE_LIMIT_PER_MINUTE")].value}')
[ "${LIMIT:-600}" != 0 ] || die "la limite de requêtes est levée (RATE_LIMIT_PER_MINUTE=0) : la remettre à 600 avant ce test"

OUT=reports/attack/temoins_$(date +%Y%m%d_%H%M)
mkdir -p reports/attack
at() { echo; echo ">>> $(date -u +%H:%M:%S) UTC : $*"; }

at "témoins : démarrage (12 min)"
SCENARIO=temoins TEMOINS_S=720 API_KEY=$TEMOINS_KEY "$LOCUST" -f loadtest/locustfile.py --headless --host "$HOST" \
  --csv "$OUT" --csv-full-history --html "$OUT.html" --loglevel WARNING --only-summary &
TEMOINS=$!
trap 'kill $TEMOINS 2>/dev/null || true' EXIT

sleep 180
at "attaquant : brute force (3 min)"
API_KEY=$API_KEY "$PY" loadtest/attack.py --host "$HOST" --phases bruteforce --bruteforce-s 180
at "retour au calme (2 min)"
sleep 120
at "attaquant : flood d'une clé / extraction (2 min)"
API_KEY=$API_KEY "$PY" loadtest/attack.py --host "$HOST" --phases extraction --extraction-s 120
at "retour au calme : fin des témoins"
wait $TEMOINS || true
trap - EXIT

at "terminé"
echo "Témoins : $OUT.html et ${OUT}_stats.csv (taux de succès, P95, P99)"
echo "Captures : Grafana sur la période ci-dessus ; alertes « Brute force » et « Extraction » dans Monitoring > Alerting"
