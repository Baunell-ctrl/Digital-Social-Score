#!/usr/bin/env bash
# Tests rapides de l'API déployée (README, étape 3 partie 4, étape 12, et grille de sécurité de l'étape 4).
# Ne modifie rien : quelques requêtes seulement, sous la limite de 600 requêtes/minute.
#
#   bash deploy/smoke.sh            (ou : make smoke)
#
# Le HTTPS est vérifié avec le nom du certificat Google (<IP fixe>.sslip.io) ; avec CACERT=tls.crt, on vérifie
# l'adresse IP avec le certificat auto-signé à la place.
source "$(dirname "$0")/lib.sh"
need curl kubectl
IP=$(api_ip)
API_KEY=$(kubectl get secret dss-secrets -o jsonpath='{.data.API_KEY}' | base64 -d)
if [ -n "${CACERT:-}" ]; then HOST=https://$IP; TLS=(--cacert "$CACERT"); else HOST=https://$(api_host); TLS=(); fi
PY=${PYTHON:-$(command -v python3 || command -v python)}

FAIL=0
check() {  # check <nom> <code attendu> <arguments curl...>
  local name=$1 want=$2; shift 2
  local got; got=$(curl -s -o /dev/null -w "%{http_code}" -m 15 "${TLS[@]}" "$@" || true)
  if [ "$got" = "$want" ]; then echo "  OK   $name ($got)"; else echo "  NON  $name : attendu $want, obtenu $got"; FAIL=1; fi
}
JSON=(-H "Content-Type: application/json")

step "Requêtes ($HOST)"
check "santé"                       200 "$HOST/health"
check "score avec clé"              200 -X POST "$HOST/score" "${JSON[@]}" -H "X-API-Key: $API_KEY" -d '{"text": "You are a stupid idiot"}'
check "score sans clé"              401 -X POST "$HOST/score" "${JSON[@]}" -d '{"text": "hello"}'
check "/metrics sans clé"           401 "$HOST/metrics"
check "texte vide"                  422 -X POST "$HOST/score" "${JSON[@]}" -H "X-API-Key: $API_KEY" -d '{"text": ""}'

step "Transport"
if curl -s -m 5 -o /dev/null "http://$IP/health"; then echo "  NON  HTTP en clair répond"; FAIL=1; else echo "  OK   HTTP en clair refusé"; fi
if command -v openssl >/dev/null; then
  if openssl s_client -connect "$IP:443" -tls1_1 </dev/null >/dev/null 2>&1; then echo "  NON  TLS 1.1 accepté"; FAIL=1
  else echo "  OK   TLS 1.1 refusé"; fi
fi

if [ "${GRID:-1}" = 1 ] && [ -n "$PY" ]; then
  step "Grille de sécurité complète (loadtest/attack.py --phases grille)"
  if [ -n "${CACERT:-}" ]; then ARGS=(--host "$HOST" --cacert "$CACERT"); else ARGS=(--host "$HOST"); fi
  API_KEY=$API_KEY "$PY" loadtest/attack.py "${ARGS[@]}" --phases grille || FAIL=1
fi

[ $FAIL = 0 ] && step "Tous les tests sont passés" || die "au moins un test a échoué"
