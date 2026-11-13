#!/usr/bin/env bash
# Crée (ou remet à jour) le compte Grafana "lecteur" : rôle Viewer, lecture seule.
# C'est ce compte, et jamais "admin", que l'on donne aux personnes qui utilisent le bouton
# « Monitoring » de la page web. Son mot de passe est dans Secret Manager (grafana-viewer-password).
#
#   bash deploy/create_viewer.sh
#
# Appelé par deploy/monitoring.sh. À relancer seul après chaque redémarrage du pod Grafana : sa base
# (SQLite sur emptyDir) est perdue au redémarrage, et le compte avec elle.
source "$(dirname "$0")/lib.sh"
need kubectl curl python3
TMP=$(mktemp -d)
ADMIN_PASSWORD=$(gcloud secrets versions access latest --secret=grafana-admin-password)
VIEWER_PASSWORD=$(secret_value grafana-viewer-password)

POD=$(kubectl get pods -l app=grafana -o jsonpath='{.items[0].metadata.name}')
kubectl port-forward "pod/$POD" 3301:3000 >"$TMP/pf.log" 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null || true; rm -rf "$TMP"' EXIT
for i in $(seq 60); do
  curl -sf http://localhost:3301/grafana/api/health >/dev/null && break
  [ "$i" -eq 60 ] && { echo "Grafana ne répond pas :"; cat "$TMP/pf.log"; exit 1; }
  sleep 1
done
g() { curl -sS --fail-with-body -u "admin:$ADMIN_PASSWORD" -H "Content-Type: application/json" "http://localhost:3301/grafana$@"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

# Création du compte, ou remise du mot de passe s'il existe déjà.
ID=$(g "/api/users/lookup?loginOrEmail=lecteur" 2>/dev/null | json "d['id']" 2>/dev/null || true)
if [ -z "$ID" ]; then
  ID=$(g /api/admin/users -X POST -d "{\"name\": \"Lecteur\", \"login\": \"lecteur\", \"password\": \"$VIEWER_PASSWORD\"}" | json "d['id']")
else
  g "/api/admin/users/$ID/password" -X PUT -d "{\"password\": \"$VIEWER_PASSWORD\"}" >/dev/null
fi
# Rôle Viewer dans l'organisation principale : voir les tableaux de bord, rien modifier.
g "/api/org/users/$ID" -X PATCH -d '{"role": "Viewer"}' >/dev/null
# Page d'accueil du lecteur : le tableau de bord du projet.
g /api/org/preferences -X PUT -d '{"homeDashboardUID": "dss-api"}' >/dev/null || true
echo "Compte lecteur prêt. Mot de passe : gcloud secrets versions access latest --secret=grafana-viewer-password"
