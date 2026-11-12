#!/usr/bin/env bash
# Étape 5 : tous les tests de charge sur le cluster GKE, à la suite (environ 30 minutes).
#
#   PROJECT=<id du projet> ./loadtest/run_gke.sh
#
# Prérequis : gcloud connecté, kubectl configuré sur dss-cluster, locust installé
# (pip install -r requirements-loadtest.txt). Sorties : reports/loadtest/gke_*.
#
# Déroulé :
#   1. lève la limite de requêtes (RATE_LIMIT_PER_MINUTE=0) pour mesurer la vraie capacité ;
#   2. référence, croissance progressive, stress ;
#   3. failover : charge constante, suppression d'un pod puis de tous les pods ;
#   4. remet la limite à 600 et vérifie qu'elle répond bien 429.
# Pendant tout le test, le nombre de pods et le CPU (HPA) sont relevés toutes les 5 secondes.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${PROJECT:?définir PROJECT}
export CLOUDSDK_CORE_PROJECT=$PROJECT
IP=${IP:-$(gcloud compute addresses describe dss-ip --global --format="value(address)")}
API_KEY=${API_KEY:-$(gcloud secrets versions access latest --secret=dss-api-key)}
export API_KEY
OUT=reports/loadtest
LOCUST=${LOCUST:-locust}
mkdir -p "$OUT"

# --- relevé du HPA toutes les 5 s : heure, pods voulus, pods prêts, CPU moyen (% de la demande) ---
echo "timestamp,replicas,ready,cpu_percent" > "$OUT/gke_hpa.csv"
(
  while true; do
    r=$(kubectl get hpa dss-api -o jsonpath='{.status.currentReplicas},{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null || echo ",")
    ready=$(kubectl get deploy dss-api -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "")
    echo "$(date +%s),${r%%,*},${ready},${r##*,}" >> "$OUT/gke_hpa.csv"
    sleep 5
  done
) &
WATCH=$!
trap 'kill $WATCH 2>/dev/null || true' EXIT

events() { echo "$(date +%s),$1" >> "$OUT/gke_events.csv"; echo ">>> $1"; }
echo "timestamp,event" > "$OUT/gke_events.csv"

run() {  # run <scénario>
  events "debut $1"
  SCENARIO=$1 $LOCUST -f loadtest/locustfile.py --headless --host "https://$IP" \
    --csv "$OUT/gke_$1" --csv-full-history --html "$OUT/gke_$1.html" --loglevel WARNING || true
  events "fin $1"
}

set_limit() {  # set_limit <n> : change la variable et attend que les pods redémarrés soient prêts
  kubectl set env deployment/dss-api RATE_LIMIT_PER_MINUTE="$1"
  kubectl rollout status deployment/dss-api --timeout=10m
  events "limite $1"
}

set_limit 0
run baseline
run ramp
run stress

# --- failover : 40 utilisateurs constants pendant 4 minutes ---
events "debut constant"
SCENARIO=constant $LOCUST -f loadtest/locustfile.py --headless --host "https://$IP" \
  --csv "$OUT/gke_failover" --csv-full-history --html "$OUT/gke_failover.html" --loglevel WARNING &
LPID=$!
sleep 60
POD=$(kubectl get pods -l app=dss-api -o jsonpath='{.items[0].metadata.name}')
events "suppression du pod $POD"
kubectl delete pod "$POD" --wait=false
sleep 90
events "suppression de tous les pods"
kubectl delete pod -l app=dss-api --wait=false
wait $LPID || true
events "fin constant"

set_limit 600
run ratelimit
events "termine"
