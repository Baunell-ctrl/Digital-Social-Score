#!/usr/bin/env bash
# Déploie l'API sur le cluster, ou la met à jour sans coupure (README, étape 3 partie 4, étapes 9 à 13).
#
#   TAG=v7 bash deploy/deploy.sh            (ou : make deploy TAG=v7)
#   TAG=v7 DRY_RUN=1 bash deploy/deploy.sh  (ou : make diff TAG=v7)  -> montre ce qui changerait, sans rien appliquer
#
#   1. Secrets Kubernetes, seulement s'ils n'existent pas encore :
#      - dss-secrets : API_KEY et LOG_SALT copiés depuis Secret Manager (jamais écrits sur le disque) ;
#        une fois créé, il n'est plus touché (il contient aussi API_KEYS, géré à l'étape 4 du README) ;
#      - dss-tls : certificat auto-signé pour l'IP fixe ; tls.crt est gardé pour les clients, tls.key supprimé.
#   2. Manifestes de k8s/ (IMAGE remplacé par l'image du TAG). Si l'API tourne déjà, le changement
#      d'image déclenche une mise à jour progressive : un nouveau pod prêt avant chaque arrêt d'un ancien.
#   Retour arrière : make rollback (kubectl rollout undo).
source "$(dirname "$0")/lib.sh"
need kubectl

MANIFESTS=(k8s/backendconfig.yaml k8s/frontendconfig.yaml k8s/service.yaml k8s/managed-cert.yaml
           k8s/ingress.yaml k8s/hpa.yaml k8s/pdb.yaml)
# Le nombre de pods est piloté par le HPA (3 à 15) : si l'API tourne déjà, on garde le nombre actuel
# au lieu du "replicas: 2" du fichier, sinon chaque déploiement ferait d'abord retomber l'API à 2 pods.
CURRENT=$(kubectl get deployment dss-api -o jsonpath='{.spec.replicas}' 2>/dev/null || true)
rendered() {
  sed -e "s|IMAGE|$IMAGE|" ${CURRENT:+-e "s|^  replicas: [0-9]*|  replicas: $CURRENT|"} k8s/deployment.yaml
  for f in "${MANIFESTS[@]}"; do echo "---"; cat "$f"; done; }

gcloud artifacts docker images describe "$IMAGE" >/dev/null 2>&1 || die "image introuvable : $IMAGE (make build TAG=$TAG)"

if [ "${DRY_RUN:-0}" = 1 ]; then
  step "Différences avec le cluster (rien n'est appliqué)"
  # kubectl diff : code 0 = aucune différence, 1 = différences affichées, >1 = erreur
  if rendered | kubectl diff -f -; then echo "aucune différence : le cluster est déjà dans cet état"; else
    [ $? -eq 1 ] || die "kubectl diff a échoué"; fi
  exit 0
fi

step "1. Secrets Kubernetes"
if kubectl get secret dss-secrets >/dev/null 2>&1; then echo "dss-secrets : existe déjà (non modifié)"; else
  kubectl create secret generic dss-secrets \
    --from-literal=API_KEY="$(gcloud secrets versions access latest --secret=dss-api-key)" \
    --from-literal=LOG_SALT="$(gcloud secrets versions access latest --secret=dss-log-salt)"
fi
if kubectl get secret dss-tls >/dev/null 2>&1; then echo "dss-tls : existe déjà (non modifié)"; else
  need openssl
  IP=$(gcloud compute addresses describe dss-ip --global --format="value(address)")
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout tls.key -out tls.crt \
    -subj "/CN=dss-api" -addext "subjectAltName=IP:$IP"
  kubectl create secret tls dss-tls --cert=tls.crt --key=tls.key
  rm tls.key   # la clé privée ne reste que dans le Secret, chiffré par KMS
fi

step "2. Déploiement de $IMAGE"
rendered | kubectl apply -f -
kubectl rollout status deployment/dss-api --timeout=10m

step "Terminé"
kubectl get deployment dss-api -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
echo "Premier déploiement : l'Ingress met 5 à 10 minutes à obtenir son adresse (make status)."
echo "Le chemin /grafana de l'Ingress ne répond qu'après : make monitoring"
