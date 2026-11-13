#!/usr/bin/env bash
# Ajoute un client nommé à l'API : sa clé est créée dans Secret Manager (dss-client-<nom>-key),
# puis ajoutée à API_KEYS sans toucher aux autres clients, et les pods redémarrent un par un (sans coupure).
# Relançable : si le client est déjà déclaré avec cette clé, rien ne change.
#
#   bash deploy/add_client.sh temoins       (ou : make client NAME=temoins)
#
# La clé n'est jamais affichée. Pour la lire :
#   gcloud secrets versions access latest --secret=dss-client-<nom>-key
source "$(dirname "$0")/lib.sh"
need kubectl
NAME=${1:?usage : deploy/add_client.sh <nom du client>}
[[ "$NAME" =~ ^[a-z0-9-]+$ ]] || die "nom de client invalide (lettres minuscules, chiffres, tirets)"

KEY=$(secret_value "dss-client-$NAME-key")
CURRENT=$(kubectl get secret dss-secrets -o jsonpath='{.data.API_KEYS}' | base64 -d 2>/dev/null || true)
if [[ ",$CURRENT," == *",$NAME:$KEY,"* ]]; then
  echo "client $NAME : déjà déclaré"; exit 0
fi
# Retire une éventuelle ancienne clé de ce client, puis ajoute la nouvelle.
KEYS=$(echo "$CURRENT" | tr ',' '\n' | grep -v "^$NAME:" | grep -v '^$' | paste -sd, - || true)
KEYS=${KEYS:+$KEYS,}$NAME:$KEY
kubectl patch secret dss-secrets -p "{\"stringData\":{\"API_KEYS\":\"$KEYS\"}}" >/dev/null
step "Client $NAME ajouté : redémarrage progressif des pods"
kubectl rollout restart deployment/dss-api
kubectl rollout status deployment/dss-api --timeout=10m
