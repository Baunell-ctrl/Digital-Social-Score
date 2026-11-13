#!/usr/bin/env bash
# Préparation du projet Google Cloud, UNE fois par projet (README, étape 3 partie 4, étapes 3 à 5, 8 et 10).
# Relançable : chaque ressource déjà présente est laissée telle quelle.
#
#   bash deploy/bootstrap.sh        (ou : make bootstrap)
#
#   1. APIs Google utilisées par le projet
#   2. secrets dans Secret Manager : clé API (dss-api-key) et clé des logs (dss-log-salt)
#   3. dépôt Artifact Registry (images Docker privées)
#   4. droits de Cloud Build : écriture dans ce seul dépôt, lecture du code envoyé, journaux
#   5. clé Cloud KMS qui chiffrera les Secrets du cluster (rotation 90 jours)
#   6. IP fixe et politique TLS 1.2 minimum du load balancer
source "$(dirname "$0")/lib.sh"
NUM=$(num)
SA="serviceAccount:$NUM-compute@developer.gserviceaccount.com"

step "1. APIs"
gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com container.googleapis.com \
  secretmanager.googleapis.com compute.googleapis.com cloudkms.googleapis.com monitoring.googleapis.com

step "2. Secrets (Secret Manager)"
secret_value dss-api-key >/dev/null
gcloud secrets describe dss-log-salt >/dev/null 2>&1 || \
  python3 -c "import secrets; print(secrets.token_hex(32), end='')" | gcloud secrets create dss-log-salt --data-file=-
echo "clé API : gcloud secrets versions access latest --secret=dss-api-key"

step "3. Artifact Registry"
gcloud artifacts repositories describe "$REPO" --location="$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format=docker --location="$REGION" \
    --description="Images de l'API Digital Social Score"

step "4. Droits de Cloud Build (moindre privilège)"
# Cloud Build utilise le compte de service par défaut de Compute Engine. Il peut écrire des images
# dans le SEUL dépôt "$REPO" (droit sur le dépôt, pas sur le projet), lire l'archive du code envoyée
# par gcloud builds submit et écrire ses journaux.
gcloud artifacts repositories add-iam-policy-binding "$REPO" --location="$REGION" \
  --member="$SA" --role=roles/artifactregistry.writer >/dev/null
for ROLE in roles/storage.objectViewer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="$SA" --role=$ROLE --condition=None -q >/dev/null
done

step "5. Clé KMS des Secrets du cluster"
gcloud kms keyrings describe dss-keyring --location "$REGION" >/dev/null 2>&1 || \
  gcloud kms keyrings create dss-keyring --location "$REGION"
gcloud kms keys describe dss-secrets-key --keyring dss-keyring --location "$REGION" >/dev/null 2>&1 || \
  gcloud kms keys create dss-secrets-key --keyring dss-keyring --location "$REGION" --purpose encryption \
    --rotation-period 90d --next-rotation-time "$(date -u -d "+90 days" +%Y-%m-%dT%H:%M:%SZ)"
# seul le service GKE du projet peut chiffrer / déchiffrer avec cette clé
gcloud kms keys add-iam-policy-binding dss-secrets-key --keyring dss-keyring --location "$REGION" \
  --member="serviceAccount:service-$NUM@container-engine-robot.iam.gserviceaccount.com" \
  --role=roles/cloudkms.cryptoKeyEncrypterDecrypter >/dev/null

step "6. IP fixe et politique TLS"
gcloud compute addresses describe dss-ip --global >/dev/null 2>&1 || gcloud compute addresses create dss-ip --global
gcloud compute ssl-policies describe dss-tls12 >/dev/null 2>&1 || \
  gcloud compute ssl-policies create dss-tls12 --profile MODERN --min-tls-version 1.2
echo "IP fixe : $(gcloud compute addresses describe dss-ip --global --format='value(address)')"

step "Terminé. Suite : make cluster, puis make build et make deploy"
