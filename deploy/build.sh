#!/usr/bin/env bash
# Construit l'image Docker de l'API et la pousse dans Artifact Registry (README, étape 3 partie 4, étape 6).
#
#   TAG=v7 bash deploy/build.sh     (ou : make build TAG=v7)
#
# Cloud Build lit le Dockerfile (5 à 10 minutes) ; .gcloudignore limite ce qui est envoyé.
# Avec LOCAL=1, l'image est construite par le Docker du poste puis poussée.
source "$(dirname "$0")/lib.sh"

if gcloud artifacts docker images describe "$IMAGE" >/dev/null 2>&1; then
  die "l'image $IMAGE existe déjà : choisir un nouveau TAG (une version publiée ne se remplace pas)"
fi

step "Construction de $IMAGE"
if [ "${LOCAL:-0}" = 1 ]; then
  need docker
  gcloud auth configure-docker "$REGION-docker.pkg.dev" -q
  docker build -t "$IMAGE" .
  docker push "$IMAGE"
else
  gcloud builds submit --tag "$IMAGE" .
fi
gcloud artifacts docker images list "$REGISTRY" --include-tags --filter="tags:$TAG"

step "Terminé. Suite : make deploy TAG=$TAG"
