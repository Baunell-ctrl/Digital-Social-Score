#!/usr/bin/env bash
# Cluster GKE Autopilot (README, étape 3 partie 4, étapes 7 et 8). Après deploy/bootstrap.sh.
#
#   bash deploy/cluster.sh          (ou : make cluster)
#
# Crée le cluster s'il n'existe pas (5 à 10 minutes), configure kubectl, autorise le cluster à lire
# les images du dépôt et chiffre les Secrets Kubernetes avec la clé KMS du projet.
# Sur un cluster existant, ne fait que configurer kubectl et vérifier ces réglages.
source "$(dirname "$0")/lib.sh"
need kubectl
NUM=$(num)
KEY=projects/$PROJECT/locations/$REGION/keyRings/dss-keyring/cryptoKeys/dss-secrets-key

step "1. Cluster $CLUSTER ($REGION)"
gcloud container clusters describe "$CLUSTER" --region "$REGION" >/dev/null 2>&1 || \
  gcloud container clusters create-auto "$CLUSTER" --region "$REGION"
gcloud container clusters get-credentials "$CLUSTER" --region "$REGION"

step "2. Lecture des images"
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location="$REGION" \
  --member="serviceAccount:$NUM-compute@developer.gserviceaccount.com" --role=roles/artifactregistry.reader >/dev/null

step "3. Chiffrement des Secrets par KMS"
if [ "$(gcloud container clusters describe "$CLUSTER" --region "$REGION" --format='value(databaseEncryption.keyName)')" != "$KEY" ]; then
  gcloud container clusters update "$CLUSTER" --region "$REGION" --database-encryption-key "$KEY"
fi
gcloud container clusters describe "$CLUSTER" --region "$REGION" --format="value(databaseEncryption.state)"   # ENCRYPTED

step "Terminé. Suite : make build, puis make deploy"
