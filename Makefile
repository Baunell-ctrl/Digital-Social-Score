# Digital Social Score : point d'entrée unique. « make » ou « make help » liste les cibles.
# Les cibles cloud appellent les scripts de deploy/ (paramètres dans deploy/config.env).
# Sans make (Windows) : lancer directement le script indiqué, par exemple  bash deploy/status.sh
#
# Chaîne complète sur un projet neuf :
#   make bootstrap cluster build deploy monitoring EMAIL=<adresse> smoke

SHELL  := bash
PYTHON ?= python
TAG    ?= $(shell sed -n 's/^TAG=\$${TAG:-\([^}]*\)}.*/\1/p' deploy/config.env)
export TAG EMAIL PROJECT

.DEFAULT_GOAL := help
.PHONY: help test api docker bootstrap cluster build deploy diff rollback monitoring grafana-reset \
        grafana smoke attack loadtest status logs destroy

help: ## liste des cibles
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-14s %s\n", $$1, $$2}'
	@echo; echo "  TAG=$(TAG) (deploy/config.env ; changer avec make deploy TAG=v7)"

# ---------------------------------------------------------------------------------- en local
test: ## tests unitaires (pytest)
	$(PYTHON) -m pytest tests -q

api: ## API en local sur http://localhost:8000/docs (clé : test)
	API_KEY=test $(PYTHON) -m uvicorn src.api.main:app --reload

docker: ## image Docker en local, sur http://localhost:8080 (clé : test)
	docker build -t digital-social-score .
	docker run --rm -p 8080:8080 -e API_KEY=test digital-social-score

# ---------------------------------------------------------------------------------- Google Cloud
bootstrap: ## une fois par projet : APIs, secrets, registre, clé KMS, IP fixe
	bash deploy/bootstrap.sh

cluster: ## cluster GKE Autopilot + kubectl + chiffrement KMS
	bash deploy/cluster.sh

build: ## image dans Artifact Registry (TAG=...)
	bash deploy/build.sh

deploy: ## déploie ou met à jour l'API sans coupure (TAG=...)
	bash deploy/deploy.sh

diff: ## ce que « make deploy » changerait, sans rien appliquer
	DRY_RUN=1 bash deploy/deploy.sh

rollback: ## revient à la version précédente de l'API
	kubectl rollout undo deployment/dss-api && kubectl rollout status deployment/dss-api

monitoring: ## GMP + Grafana + alertes (EMAIL=...)
	bash deploy/monitoring.sh

grafana-reset: ## après un redémarrage du pod Grafana : source de données et compte lecteur
	ALERTS=0 bash deploy/monitoring.sh

grafana: ## Grafana administrateur sur http://localhost:3000/grafana/ (Ctrl+C pour fermer)
	kubectl port-forward svc/grafana 3000:3000

status: ## état du déploiement (lecture seule)
	bash deploy/status.sh

logs: ## 50 dernières lignes de logs des pods de l'API
	kubectl logs -l app=dss-api --tail=50 --prefix

# ---------------------------------------------------------------------------------- tests du cloud
smoke: ## tests rapides de l'API déployée (santé, 401, 422, HTTP et TLS 1.1 refusés, grille)
	bash deploy/smoke.sh

attack: ## simulation d'attaque complète, environ 5 min (déclenche les alertes)
	PYTHON=$(PYTHON) bash deploy/attack.sh

loadtest: ## tous les tests de charge de l'étape 5, environ 30 min
	PROJECT=$${PROJECT:-$$(gcloud config get-value project)} bash loadtest/run_gke.sh

destroy: ## supprime le cluster et tout ce qui est facturé (confirmation demandée)
	bash deploy/destroy.sh
