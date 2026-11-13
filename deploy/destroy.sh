#!/usr/bin/env bash
# Supprime tout ce qui est facturé (README, étape 3 partie 4, étape 15) : cluster, IP fixe, load balancer,
# politique TLS, règles d'alerte. Demande de taper le nom du cluster avant de commencer.
#
#   bash deploy/destroy.sh          (ou : make destroy)
#
# Gardés volontairement : les images (Artifact Registry) et les secrets (Secret Manager), peu coûteux,
# pour pouvoir tout recréer avec la même clé API. La clé KMS ne peut pas être supprimée d'un coup :
# ses versions sont détruites (effectif après 30 jours).
source "$(dirname "$0")/lib.sh"
need kubectl

echo "Projet $PROJECT : suppression du cluster $CLUSTER, de l'IP dss-ip, de la politique dss-tls12,"
echo "des règles d'alerte DSS et des versions de la clé KMS dss-secrets-key."
confirm "$CLUSTER" "Action irréversible."

step "1. Ressources Kubernetes (libère le load balancer)"
if gcloud container clusters describe "$CLUSTER" --region "$REGION" >/dev/null 2>&1; then
  gcloud container clusters get-credentials "$CLUSTER" --region "$REGION"
  kubectl delete -f k8s/ingress.yaml --ignore-not-found --wait=true
  step "2. Cluster"
  gcloud container clusters delete "$CLUSTER" --region "$REGION" -q
fi

step "3. IP fixe et politique TLS"
gcloud compute addresses delete dss-ip --global -q 2>/dev/null || true
gcloud compute ssl-policies delete dss-tls12 -q 2>/dev/null || true

step "4. Règles d'alerte"
for f in monitoring/alerts/*.yaml; do
  NAME=$(sed -n 's/^displayName: "\(.*\)"/\1/p' "$f")
  for P in $(gcloud monitoring policies list --filter="displayName=\"$NAME\"" --format="value(name)" 2>/dev/null); do
    gcloud monitoring policies delete "$P" -q
  done
done

step "5. Versions de la clé KMS"
for V in $(gcloud kms keys versions list --key dss-secrets-key --keyring dss-keyring --location "$REGION" \
             --filter="state=ENABLED OR state=DISABLED" --format="value(name.basename())" 2>/dev/null); do
  gcloud kms keys versions destroy "$V" --key dss-secrets-key --keyring dss-keyring --location "$REGION" -q
done

step "Terminé"
