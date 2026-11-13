# Fonctions communes aux scripts de deploy/ (à charger avec : source "$(dirname "$0")/lib.sh").
# - se place à la racine du dépôt ;
# - lit deploy/config.env et calcule PROJECT, NUM (numéro du projet) et IMAGE ;
# - ne lit ni n'affiche aucun secret.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# shellcheck source=deploy/config.env
source deploy/config.env

step() { echo; echo "=== $*"; }
die() { echo "ERREUR : $*" >&2; exit 1; }
need() { for c in "$@"; do command -v "$c" >/dev/null || die "commande introuvable : $c"; done; }

# confirm <mot> <message> : l'utilisateur doit taper le mot exact (pour les actions destructrices).
confirm() {
  local answer
  read -r -p "$2 Taper « $1 » pour confirmer : " answer
  [ "$answer" = "$1" ] || die "annulé"
}

# secret_value <nom> : valeur d'un secret Secret Manager, créé avec 32 octets aléatoires s'il n'existe pas.
secret_value() {
  if ! gcloud secrets describe "$1" >/dev/null 2>&1; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" | gcloud secrets create "$1" --data-file=- >/dev/null
  fi
  gcloud secrets versions access latest --secret="$1"
}

# Git Bash sous Windows : le script "gcloud" (bash) se trompe de chemin ; "gcloud.cmd" fonctionne.
if [[ "${OSTYPE:-}" == msys* || "${OSTYPE:-}" == cygwin* ]] && command -v gcloud.cmd >/dev/null; then
  gcloud() { gcloud.cmd "$@"; }
  python3() { "${PYTHON:-python}" "$@"; }
fi
need gcloud
PROJECT=${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}
[ -n "$PROJECT" ] || die "aucun projet : définir PROJECT dans deploy/config.env ou faire gcloud config set project <id>"
export CLOUDSDK_CORE_PROJECT=$PROJECT
REGISTRY=$REGION-docker.pkg.dev/$PROJECT/$REPO
IMAGE=$REGISTRY/api:$TAG
# Adresse de l'API, toujours déduite de l'IP fixe ACTUELLEMENT réservée par le projet (jamais écrite en dur) :
# si l'IP était libérée puis réattribuée à quelqu'un d'autre, un nom figé enverrait la clé API à ce tiers.
api_ip() { gcloud compute addresses describe dss-ip --global --format="value(address)"; }
api_host() { local ip; ip=$(api_ip) && [ -n "$ip" ] || die "IP fixe dss-ip introuvable"; echo "${ip//./-}.sslip.io"; }
num() { gcloud projects describe "$PROJECT" --format="value(projectNumber)"; }
