#!/usr/bin/env bash
# État du déploiement, en lecture seule : image, pods, autoscaling, Ingress, certificat, collecte GMP.
#
#   bash deploy/status.sh           (ou : make status)
source "$(dirname "$0")/lib.sh"
need kubectl

step "Image déployée"
kubectl get deployment dss-api -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
step "Pods"
kubectl get pods -l 'app in (dss-api, grafana)' -o wide
step "Autoscaling (HPA) et budget de pannes (PDB)"
kubectl get hpa,pdb
step "Ingress et certificat Google"
kubectl get ingress dss-api
kubectl get managedcertificate -o custom-columns=NOM:.metadata.name,DOMAINE:.spec.domains[0],ETAT:.status.certificateStatus 2>/dev/null || true
step "Collecte des métriques (GMP) : up{job=\"dss-api\"} = 1 par pod"
curl -s -G -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://monitoring.googleapis.com/v1/projects/$PROJECT/location/global/prometheus/api/v1/query" \
  --data-urlencode 'query=up{job="dss-api"}' \
  | python3 -c "import json,sys; r=json.load(sys.stdin)['data']['result']; [print(' ', x['metric'].get('instance','?'), '=', x['value'][1]) for x in r] or print('  aucune série (collecte non configurée ?)')" \
  2>/dev/null || echo "  lecture impossible (droits monitoring ?)"
