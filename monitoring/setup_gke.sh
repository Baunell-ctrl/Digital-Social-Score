#!/usr/bin/env bash
# Étape 6 : supervision de l'API sur GKE avec Managed Service for Prometheus (GMP), Grafana et
# Cloud Monitoring. À lancer dans Cloud Shell, à la racine du dépôt, avec kubectl déjà configuré
# sur dss-cluster (étape 3, partie 4 du README).
#
#   PROJECT=<id du projet> EMAIL=<adresse des alertes> ./monitoring/setup_gke.sh
#
# Le script peut être relancé : chaque étape vérifie ce qui existe déjà.
#   1. clé de collecte "prometheus" (Secret Manager -> Secret Kubernetes, ajoutée à API_KEYS)
#   2. collecte GMP : PodMonitoring + droit de lire ce seul Secret
#   3. Grafana dans le cluster (sans accès public) + datasource-syncer (Workload Identity)
#   4. métrique basée sur les logs (auth_failed) + canal email + 7 règles d'alerte en PromQL
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT=${PROJECT:?définir PROJECT}
EMAIL=${EMAIL:?définir EMAIL (adresse qui reçoit les alertes)}
export CLOUDSDK_CORE_PROJECT=$PROJECT
NUM=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
step() { echo; echo "=== $1"; }
secret_value() {  # secret_value <nom> : valeur d'un secret Secret Manager, créé avec 32 octets aléatoires s'il n'existe pas
  if ! gcloud secrets describe "$1" >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" | gcloud secrets create "$1" --data-file=- >/dev/null
  fi
  gcloud secrets versions access latest --secret="$1"
}

# --------------------------------------------------------------------------------------------- 1
step "1. Clé de collecte Prometheus"
PROM_KEY=$(secret_value dss-prometheus-key)
kubectl create secret generic dss-prometheus --from-literal=token="$PROM_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

# Ajoute "prometheus:<clé>" à API_KEYS sans toucher aux autres clients déjà déclarés.
CURRENT=$(kubectl get secret dss-secrets -o jsonpath='{.data.API_KEYS}' | base64 -d 2>/dev/null || true)
if [[ ",$CURRENT," != *",prometheus:$PROM_KEY,"* ]]; then
  KEYS=$(echo "$CURRENT" | tr ',' '\n' | grep -v '^prometheus:' | grep -v '^$' | paste -sd, - || true)
  KEYS=${KEYS:+$KEYS,}prometheus:$PROM_KEY
  kubectl patch secret dss-secrets -p "{\"stringData\":{\"API_KEYS\":\"$KEYS\"}}"
  kubectl rollout restart deployment/dss-api
  kubectl rollout status deployment/dss-api --timeout=10m
fi

# --------------------------------------------------------------------------------------------- 2
step "2. Collecte GMP"
kubectl apply -f k8s/monitoring/rbac.yaml -f k8s/monitoring/podmonitoring.yaml

# --------------------------------------------------------------------------------------------- 3
step "3. Grafana"
GRAFANA_PASSWORD=$(secret_value grafana-admin-password)
kubectl create secret generic grafana-admin --from-literal=password="$GRAFANA_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap grafana-dashboards --from-file=monitoring/grafana/dss-dashboard.json \
  --dry-run=client -o yaml | kubectl apply -f -

# Workload Identity : le compte Kubernetes "datasource-syncer" peut LIRE les métriques, rien d'autre.
gcloud projects add-iam-policy-binding "$PROJECT" --condition=None -q >/dev/null \
  --member="principal://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/$PROJECT.svc.id.goog/subject/ns/default/sa/datasource-syncer" \
  --role=roles/monitoring.viewer

# Le secret du synchroniseur doit exister avant le CronJob (jeton provisoire, remplacé plus bas).
kubectl get secret grafana-syncer >/dev/null 2>&1 || kubectl create secret generic grafana-syncer \
  --from-literal=token=pending --from-literal=project="$PROJECT"
EXISTED=$(kubectl get deployment grafana -o name 2>/dev/null || true)
kubectl apply -f k8s/monitoring/grafana.yaml
# Grafana déjà installé : redémarrage pour recharger le tableau de bord s'il a changé.
[ -z "$EXISTED" ] || kubectl rollout restart deployment/grafana
kubectl rollout status deployment/grafana --timeout=5m

# Attendre qu'il ne reste qu'UN pod Grafana : sinon le port-forward peut viser l'ancien pod en cours d'arrêt.
for _ in $(seq 60); do
  [ "$(kubectl get pods -l app=grafana --no-headers 2>/dev/null | wc -l)" -eq 1 ] && break; sleep 2
done
POD=$(kubectl get pods -l app=grafana -o jsonpath='{.items[0].metadata.name}')
kubectl wait --for=condition=ready "pod/$POD" --timeout=3m

# Source de données "gmp" et jeton du synchroniseur, par l'API de Grafana (via un port-forward local).
kubectl port-forward "pod/$POD" 3300:3000 >/tmp/dss-grafana-pf.log 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true' EXIT
for i in $(seq 60); do
  curl -sf http://localhost:3300/api/health >/dev/null && break
  [ "$i" -eq 60 ] && { echo "Grafana ne répond pas :"; cat /tmp/dss-grafana-pf.log; exit 1; }
  sleep 1
done
# --fail-with-body : en cas d'erreur, Grafana explique pourquoi au lieu d'une réponse vide
g() { curl -sS --fail-with-body -u "admin:$GRAFANA_PASSWORD" -H "Content-Type: application/json" "http://localhost:3300$@"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

g /api/datasources/uid/gmp >/dev/null 2>&1 || g /api/datasources -X POST -d "{
  \"name\": \"Managed Service for Prometheus\", \"uid\": \"gmp\", \"type\": \"prometheus\",
  \"access\": \"proxy\", \"isDefault\": true,
  \"url\": \"https://monitoring.googleapis.com/v1/projects/$PROJECT/location/global/prometheus\"}" >/dev/null

SA_ID=$(g "/api/serviceaccounts/search?query=datasource-syncer" \
  | json "next((a['id'] for a in d['serviceAccounts'] if a['name'] == 'datasource-syncer'), '')")
[ -n "$SA_ID" ] || SA_ID=$(g /api/serviceaccounts -X POST \
  -d '{"name": "datasource-syncer", "role": "Admin"}' | json "d['id']")
TOKEN=$(g "/api/serviceaccounts/$SA_ID/tokens" -X POST -d "{\"name\": \"syncer-$(date +%s)\"}" | json "d['key']")
kubectl create secret generic grafana-syncer --from-literal=token="$TOKEN" --from-literal=project="$PROJECT" \
  --dry-run=client -o yaml | kubectl apply -f -

# Première synchronisation tout de suite, sans attendre le CronJob.
kubectl delete job datasource-syncer-now --ignore-not-found
kubectl create job datasource-syncer-now --from=cronjob/datasource-syncer
kubectl wait --for=condition=complete job/datasource-syncer-now --timeout=3m

# --------------------------------------------------------------------------------------------- 4
step "4. Logs et alertes"
# Métrique basée sur les logs : deuxième source, indépendante de /metrics, pour les échecs d'authentification.
gcloud logging metrics describe dss_auth_failed >/dev/null 2>&1 || gcloud logging metrics create dss_auth_failed \
  --description="Échecs d'authentification de l'API Digital Social Score (journal d'audit)" \
  --log-filter='resource.type="k8s_container" AND resource.labels.container_name="api" AND jsonPayload.event="auth_failed"'

CHANNEL=$(gcloud beta monitoring channels list --filter="labels.email_address=\"$EMAIL\"" --format="value(name)" | head -1)
[ -n "$CHANNEL" ] || CHANNEL=$(gcloud beta monitoring channels create --display-name="DSS - alertes ($EMAIL)" \
  --type=email --channel-labels=email_address="$EMAIL" --format="value(name)")

for f in monitoring/alerts/*.yaml; do
  NAME=$(sed -n 's/^displayName: "\(.*\)"/\1/p' "$f")
  if [ -n "$(gcloud alpha monitoring policies list --filter="displayName=\"$NAME\"" --format="value(name)")" ]; then
    echo "déjà créée : $NAME"; continue
  fi
  sed "s|^notificationChannels: \[\].*|notificationChannels: [\"$CHANNEL\"]|" "$f" > /tmp/dss-policy.yaml
  gcloud alpha monitoring policies create --policy-from-file=/tmp/dss-policy.yaml >/dev/null
  echo "créée : $NAME"
done

step "Terminé"
cat <<EOF
Grafana   : kubectl port-forward svc/grafana 3000:3000   puis http://localhost:3000
            utilisateur admin, mot de passe : gcloud secrets versions access latest --secret=grafana-admin-password
Collecte  : Metrics Explorer > PromQL > up{job="dss-api"}   (1 par pod, après environ 1 minute)
Alertes   : console > Monitoring > Alerting
EOF
