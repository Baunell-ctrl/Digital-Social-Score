# Digital Social Score

Compte rendu du TP « API Digital Social Score : de l'analyse de texte à l'infrastructure Cloud sécurisée, scalable et conforme » (énoncé : `docs/TP1_Digital_Social_Score.pdf`).

Chaque étape commence par l'**énoncé du TP** (objectifs pédagogiques et exercice), puis répond aux questions de l'exercice, dans l'ordre.

## Objectif global du TP et réponses

| Le TP demande une API qui…                                                      | Notre réponse                                                                                     | Où            |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------------- |
| détecte la toxicité d'un texte (injure, racisme, harcèlement, propos haineux) | LSTM entraîné sur les 6 étiquettes du jeu Jigsaw, regroupées en une seule (règle du OU)       | étape 2       |
| attribue un score numérique de 0 à 100                                         | `POST /score` renvoie la probabilité du modèle × 100                                          | étape 3       |
| respecte le RGPD : aucune donnée personnelle stockée en clair                  | anonymisation par NER, texte jamais stocké, logs pseudonymes (HMAC), deux registres de traitement | étapes 1 et 4 |
| est capable de varier d'échelle                                                 | GKE Autopilot, 3 à 15 pods (autoscaling), mesuré jusqu'à 195 req/s                              | étape 5       |
| est observable et auditable                                                      | métriques Prometheus (Managed Service for Prometheus), Grafana, 7 alertes, journal d'audit        | étape 6       |
| est documentée avec un schéma d'architecture                                   | vue d'ensemble ci-dessous ; schéma final en cours                                                 | étape 7       |

## Livrables du TP

| Livrable                                        | Où                                                                                                            |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| API fonctionnelle et documentée                | étape 3 ; documentation interactive sur `/docs`                                                             |
| Exemples de requêtes et réponses              | étape 3, parties 1 et 5                                                                                       |
| Schéma d'architecture Cloud + texte explicatif | vue d'ensemble ci-dessous ; étape 7                                                                           |
| Registre RGPD                                   | étape 1, partie 4 (jeu de données) ; étape 4, partie 6 (API)                                                |
| Tableau de bord / captures supervision          | étape 6, partie 2                                                                                             |
| Rapport de simulation de charge                 | étape 5                                                                                                       |
| Grille de tests sécurité / stress / failover  | sécurité : étape 4, partie 7 ; stress et failover : étape 5, parties 3 et 4 ; attaque : étape 6, partie 4 |

## Vue d'ensemble : architecture et connexions

**Accès** (les valeurs secrètes sont dans Secret Manager, et en local dans un fichier `.env` jamais poussé) :

| Quoi                       | Adresse                                       | Identifiant                        |
| -------------------------- | --------------------------------------------- | ---------------------------------- |
| Page web (tester un texte) | https://34-54-213-190.sslip.io/               | clé API, à saisir dans la page   |
| API                        | `POST https://34-54-213-190.sslip.io/score` | en-tête `X-API-Key`             |
| Documentation de l'API     | https://34-54-213-190.sslip.io/docs           | –                                 |
| Supervision (Grafana)      | https://34-54-213-190.sslip.io/grafana/       | compte `lecteur` (lecture seule) |

Lire un secret (toujours préciser le projet, sinon gcloud cherche dans le projet par défaut) :
`gcloud secrets versions access latest --secret=dss-api-key --project=project-1dc38f00-079d-4d45-996; echo`
(et `grafana-viewer-password` pour Grafana).

```
                           Internet
  Navigateur, client ──HTTPS (certificat Google, TLS 1.2+)──▶ Load balancer Google (Ingress, IP fixe)
                                                                 │ "/"                   │ "/grafana"
                                                                 ▼                       ▼
                                                   pods dss-api (API + page)      pod Grafana
                                                   3 à 15, autoscaling (HPA)      (connexion obligatoire)
                                                    │ logs JSON    ▲ /metrics          │ PromQL
                                                    ▼              │ (clé "prometheus") ▼
                                              Cloud Logging   collecteurs Prometheus ──▶ Cloud Monitoring ──▶ alertes par email
                                                              gérés par Google (GMP)    (métriques)

  À l'installation : Secret Manager ──▶ Secrets Kubernetes ──▶ variables d'environnement des pods
                     Cloud Build ──▶ Artifact Registry (image Docker) ──▶ nœuds GKE Autopilot
```

| Connexion                               | Comment                                                                 | Qui s'authentifie, et avec quoi                                                                                                                 | Chiffrement                                                              |
| --------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| Client → load balancer                 | HTTPS sur l'IP fixe, nom `34-54-213-190.sslip.io`                     | la page et `/docs` sont publiques ; `/score` exige la **clé API**                                                                    | TLS 1.2+, certificat **géré par Google** ; port 80 fermé        |
| Load balancer → pods de l'API          | HTTP sur le réseau privé de Google ; les pods n'ont pas d'IP publique | – (seul le load balancer peut les joindre)                                                                                                     | réseau privé Google                                                    |
| Page web → `/score`                  | la page envoie le texte avec la clé saisie par l'utilisateur           | **clé API** (en-tête `X-API-Key`), gardée dans l'onglet du navigateur                                                                | HTTPS                                                                    |
| Load balancer → Grafana                | même load balancer, chemin `/grafana`                                | **identifiant Grafana** (compte `lecteur` : voir, rien modifier)                                                                        | HTTPS jusqu'au load balancer                                             |
| Prometheus géré (GMP) → `/metrics` | lecture toutes les 30 s, dans le cluster                                | clé API dédiée `prometheus`, lue dans un Secret que seul le collecteur peut lire                                                           | réseau du cluster                                                       |
| Grafana → Cloud Monitoring             | requêtes PromQL vers l'API de Google                                   | jeton Google renouvelé toutes les 10 min par le `datasource-syncer` (**Workload Identity**, rôle lecture seule `monitoring.viewer`) | HTTPS                                                                    |
| Pods → Cloud Logging                   | GKE collecte la sortie standard des pods                                | automatique (agent de GKE)                                                                                                                      | HTTPS                                                                    |
| Secret Manager → pods                  | copie dans des Secrets Kubernetes à l'installation                     | votre compte Google (IAM)                                                                                                                       | Secrets chiffrés par Google (clé KMS du projet : à activer, étape 4) |
| Cloud Build → Artifact Registry → GKE | construction puis téléchargement de l'image                           | comptes de service, rôles minimaux (IAM, étape 4)                                                                                             | HTTPS                                                                    |

Chaque connexion vers l'extérieur est authentifiée et chiffrée. Les seuls points en clair sont internes à Google (load balancer → pods, collecte des métriques), sur des pods sans adresse publique.

## Arborescence du dépôt et commandes

| Dossier / fichier | Contenu |
| --- | --- |
| `src/anonymization/`, `src/preprocessing/` | étape 1 et début de l'étape 2 : anonymisation (NER), nettoyage, construction des jeux de données |
| `src/models/` | étape 2 : entraînement LSTM, BERT, LAYA et export du LSTM (`export_lstm.py`) |
| `src/api/` | étape 3 : API FastAPI (`main.py`), chargement du modèle (`predictor.py`), page web (`static/`) |
| `artifacts/` | les 3 fichiers du LSTM déployé (copiés dans l'image Docker) |
| `data/`, `reports/` | jeux de données (brut, anonymisé, nettoyé) ; résultats, rapports de charge et d'attaque, images |
| `tests/` | tests automatiques (`pytest`) |
| `Dockerfile`, `requirements-api.txt` | image de l'API |
| `k8s/` | manifestes Kubernetes : API, Service, Ingress, HTTPS, HPA, PDB ; `k8s/monitoring/` : collecte GMP et Grafana |
| `monitoring/` | configuration de la supervision : tableau de bord Grafana (JSON) et 7 règles d'alerte (YAML) |
| `deploy/` | **scripts de déploiement** sur Google Cloud, réglages dans `deploy/config.env` (aucun secret) |
| `loadtest/` | tests de charge (Locust, `run_gke.sh`), simulation d'attaque (`attack.py`), graphes (`plot.py`) |
| `Makefile` | point d'entrée : `make help` liste toutes les commandes |
| `docs/` | sujets du TP et supports de cours |

**Tout le cycle cloud en commandes.** À lancer dans Cloud Shell, où `make`, `gcloud` et `kubectl` sont installés. Sans `make`, par exemple sous Windows avec Git Bash, lancer directement le script indiqué (`bash deploy/status.sh`).

| Commande | Script | Rôle | Détail dans le README |
| --- | --- | --- | --- |
| `make bootstrap` | `deploy/bootstrap.sh` | une fois par projet : APIs, secrets, Artifact Registry, clé KMS, IP fixe, politique TLS | étape 3, partie 4, étapes 3 à 5, 8 et 10 |
| `make cluster` | `deploy/cluster.sh` | cluster GKE Autopilot, `kubectl`, chiffrement KMS des Secrets | étapes 7 et 8 |
| `make build TAG=v7` | `deploy/build.sh` | image construite par Cloud Build et poussée dans Artifact Registry | étape 6 |
| `make diff TAG=v7` | `deploy/deploy.sh` | ce que le déploiement changerait sur le cluster, sans rien appliquer | – |
| `make deploy TAG=v7` | `deploy/deploy.sh` | premier déploiement ou mise à jour sans coupure | étapes 9, 11 et 13 |
| `make rollback` | – | retour à la version précédente | étape 13 |
| `make monitoring EMAIL=...` | `deploy/monitoring.sh` | collecte GMP, Grafana, alertes | étape 6, partie 2 |
| `make grafana-reset` | `deploy/monitoring.sh` | après un redémarrage du pod Grafana | étape 6, partie 2 |
| `make status` | `deploy/status.sh` | image, pods, HPA, Ingress, certificat, collecte (lecture seule) | – |
| `make smoke` | `deploy/smoke.sh` | tests rapides et grille de sécurité contre le cluster | étape 3 (12) et étape 4 (7) |
| `make attack` / `make loadtest` | `loadtest/` | attaque complète (5 min) / tests de charge (30 min) | étapes 6 et 5 |
| `make destroy` | `deploy/destroy.sh` | supprime tout ce qui est facturé (confirmation demandée) | étape 15 |
| `make test` / `make api` / `make docker` | – | tests, API en local, image en local | « Lancer le projet » |

Sur un projet neuf : `make bootstrap cluster build deploy` puis `make monitoring EMAIL=<adresse> smoke`. Les scripts peuvent être relancés : ce qui existe déjà n'est pas recréé, et aucun secret n'est affiché ni écrit sur le disque.

## Étape 1 : exploration, analyse et anonymisation des données

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* repérer et traiter les données personnelles dans des textes, comprendre le cadre RGPD ; mettre en œuvre l'anonymisation et la pseudonymisation.
>
> *Exercice :*
>
> 1. Télécharger un jeu de données adapté (ex : Toxic Comment Classification Dataset ou GameTox).
> 2. Utiliser un algorithme NER (ex : spaCy) pour anonymiser les données.
> 3. Comparer la version initiale et anonymisée et justifier chaque choix.
> 4. Documenter votre registre de traitement des données.

L'**anonymisation** (irréversible) est appliquée au jeu de données dans cette étape. La **pseudonymisation** (identifiant HMAC, réversible seulement avec la clé secrète) est utilisée dans les logs de l'API : voir l'étape 4, partie 4.

### 1. Télécharger un jeu de données adapté

[Toxic Comment Classification Dataset](https://www.kaggle.com/competitions/jigsaw-toxic-comment-classification-challenge) (commentaires Wikipédia, 6 étiquettes de toxicité).

| Fichier                    | Lignes |
| -------------------------- | ------ |
| `data/train_dataset.csv` | 25 960 |
| `data/val_dataset.csv`   | 6 490  |

Colonnes : `id`, `comment_text`, `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`.

### 2. Utiliser un algorithme NER pour anonymiser les données

Script : `python -m src.anonymization.build_datasets` (code dans `src/anonymization/anonymizer.py`, tests dans `tests/`).

- **Expressions régulières** pour les formats stricts : email, URL, adresse IP, téléphone, `@pseudo`.
- **spaCy** (`en_core_web_sm`) pour les noms de personnes et les lieux.
- Chaque donnée trouvée est remplacée par une étiquette : `[PERSON]`, `[LOCATION]`, `[EMAIL]`, `[URL]`, `[IP]`, `[PHONE]`, `[USERNAME]`.
- L'opération est **irréversible** : on ne peut pas retrouver la personne.

Sorties : `data/anonymized/{train,val}_dataset.csv`, `reports/anonymization_stats.json`, `reports/before_after_sample.csv` (exemples avant / après).

### 3. Comparer la version initiale et anonymisée, et justifier chaque choix

**Ce que l'anonymisation a changé.** 42 % des commentaires ont été modifiés (42,8 % en train, 43,2 % en val). Environ 40 000 données personnelles ont été remplacées, surtout des noms, des lieux et des adresses IP.

| Étiquette | Train  | Val   |
| ---------- | ------ | ----- |
| PERSON     | 19 636 | 5 027 |
| LOCATION   | 8 698  | 2 361 |
| IP         | 1 873  | 425   |
| URL        | 1 151  | 227   |
| PHONE      | 353    | 101   |
| EMAIL      | 64     | 15    |
| USERNAME   | 33     | 14    |

**Ce qui n'a pas changé.** Mêmes lignes, mêmes identifiants, mêmes étiquettes. Il y a toujours 47,1 % de commentaires `toxic`, avant comme après.

**Ce qui a un peu changé.** Le vocabulaire du train diminue d'environ 15 % (59 932 mots distincts avant, 50 831 après, mesuré après nettoyage) : les milliers de noms propres deviennent quelques étiquettes. Les commentaires sont aussi un peu plus courts (356 → 351 caractères en moyenne en train).

**Justification des choix.**

- **Regex pour email, URL, IP, téléphone** : ces formats sont stricts, donc la détection est fiable. Un téléphone doit avoir 9 à 15 chiffres, pour ne pas confondre avec des dates ou des années.
- **spaCy pour les noms et les lieux** : une regex ne peut pas reconnaître un nom propre.
- **Organisations conservées** : une entreprise n'est pas une donnée personnelle. Le NER y classe aussi du jargon Wikipédia (Wikipedia, POV, UTC…) et cela fausserait le texte.
- **Nationalités et religions conservées** : elles ne sont pas directement identifiantes et elles servent à détecter les propos racistes et haineux (`identity_hate`).
- **Liste d'exclusion** pour le jargon Wikipédia que le NER prend pour des noms propres.

**Compromis.** Il y a une légère perte de contexte (la cible d'une insulte, l'origine d'une adresse IP) et le NER peut se tromper (oubli ou faux positif). C'est acceptable pour la détection de toxicité : les modèles seront entraînés sur la version anonymisée.

### 4. Documenter le registre de traitement des données

Modèle inspiré de celui de la CNIL.

| Rubrique                         | Contenu                                                                                                                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Traitement**             | Anonymisation de commentaires en ligne pour entraîner un modèle de détection de toxicité                                                                                     |
| **Responsable**            | Équipe projet Digital Social Score (TP)                                                                                                                                         |
| **Finalité**              | Préparer un jeu de données sans donnée personnelle pour entraîner et évaluer un modèle de scoring de toxicité                                                             |
| **Base légale**           | Intérêt légitime (modération de contenus haineux). Jeu de données public, utilisé dans un cadre pédagogique                                                               |
| **Personnes concernées**  | Auteurs des commentaires et personnes citées dans les commentaires                                                                                                              |
| **Données traitées**     | Texte libre des commentaires. Données personnelles détectées : noms de personnes, lieux, emails, URL, adresses IP, téléphones, noms d'utilisateur. Étiquettes de toxicité |
| **Données sensibles**     | Aucune collectée volontairement. Un commentaire peut mentionner une origine ou une religion : ces mentions sont conservées (voir plus haut)                                    |
| **Minimisation**           | Les données personnelles détectées sont remplacées par des étiquettes génériques. Seul le jeu anonymisé est utilisé pour la suite du projet                             |
| **Destinataires**          | Équipe projet uniquement                                                                                                                                                        |
| **Durée de conservation** | Durée du projet. Le jeu brut est supprimé à la fin                                                                                                                            |
| **Transferts hors UE**     | Aucun (traitement local)                                                                                                                                                         |
| **Mesures de sécurité**  | Accès limité à l'équipe, jeu brut distinct du jeu anonymisé, tests automatiques de l'anonymisation, aucune donnée personnelle dans les rapports de suivi                   |
| **Limites connues**        | Le NER n'est pas parfait : des données personnelles peuvent subsister (noms rares, écrits atypiques)                                                                           |

## Étape 2 : préparation et entraînement d'un modèle IA

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* appréhender le nettoyage de texte et les modèles d'IA ; entraîner et comparer un modèle statistique (LSTM), un modèle transformer (BERT) et un modèle de décision (LAYA).
>
> *Exercice :*
>
> 1. Nettoyage des textes (ponctuation, emojis, casse).
> 2. Entraîner trois modèles : statistique, transformer et décisionnaire (LSTM, BERT et LAYA).
> 3. Comparer précision, rappel, et temps de traitement.

### 1. Nettoyage des textes (ponctuation, emojis, casse)

Script : `python -m src.preprocessing.build_clean_datasets` (code dans `src/preprocessing/cleaner.py`).
Entrée : `data/anonymized/`. Sortie : `data/cleaned/{train,val}_dataset.csv` et `reports/cleaning_before_after_sample.csv`.

Le nettoyage fait 4 choses, dans cet ordre :

- **Minuscules** : « Hate » et « hate » deviennent le même mot, le vocabulaire est plus petit.
- **Emojis supprimés** : ils n'apportent rien aux modèles de texte simples (LSTM, LAYA).
- **Sauts de ligne supprimés** : remplacés par un espace.
- **Ponctuation supprimée** : les apostrophes sont gardées (`don't`).

Mêmes lignes, mêmes identifiants, mêmes étiquettes qu'avant. Les étiquettes d'anonymisation perdent leurs crochets (`[PERSON]` devient `person`) : accepté, c'est simple et sans effet sur l'entraînement.

### 2. Entraîner trois modèles : statistique (LSTM), transformer (BERT) et décisionnaire (LAYA)

**Notre choix pour l'entraînement : une seule étiquette.** Le jeu d'origine décrit chaque commentaire avec 6 colonnes (`toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`). Nous les regroupons en **une seule colonne**, avec la règle du OU : le commentaire est **inapproprié (1) si au moins une des 6 colonnes vaut 1**, sinon il est acceptable (0).

- **Pourquoi** : l'objectif est de savoir globalement si un contenu est inapproprié ou non, pas de dire de quel type d'insulte il s'agit. Une seule question donne un modèle plus simple et un seul score facile à lire.
- **Pourquoi pas la seule colonne `toxic`** : un commentaire peut être obscène ou insultant sans être marqué `toxic`. Avec la règle du OU, il est bien détecté (50,0 % de commentaires inappropriés contre 47,1 % avec `toxic` seul).
- **Ce qu'on perd** : le détail du type de toxicité (menace, haine…).

Les trois modèles utilisent le même jeu de données, la même règle du OU et le même découpage, donc leurs scores sont comparables.

| Jeu   | Commentaires | Rôle                                        |
| ----- | ------------ | -------------------------------------------- |
| Train | 23 364       | apprentissage                                |
| Dev   | 2 596        | choix du meilleur epoch                      |
| Test  | 1 308        | évaluation finale, jamais vu par le modèle |

#### LSTM (modèle statistique)

Script : `python -m src.models.lstm_binary`. Résultats détaillés : `reports/results_lstm.json`.

**Architecture.** Embedding (128) → BiLSTM (2 × 128) → max-pooling sur les mots (sans le remplissage) → **1 seule sortie** sigmoïde : la probabilité que le commentaire soit inapproprié (règle du OU). 128 mots maximum par commentaire, vocabulaire de 30 000 mots, seuil de décision 0,5.

**Fichiers produits** (dossier `artifacts/`, utilisé par l'API) :

| Fichier         | Contenu                                                              |
| --------------- | -------------------------------------------------------------------- |
| `lstm.pt`     | les poids du modèle (16 Mo)                                         |
| `vocab.json`  | le vocabulaire `{mot: numéro}` (0 = remplissage, 1 = mot inconnu) |
| `config.json` | les tailles du modèle, la longueur maximale et le seuil             |

Les trois fichiers vont ensemble : des poids chargés avec un autre vocabulaire donneraient des scores faux sans aucune erreur.

**Jeu de données.** 32 450 commentaires Wikipédia nettoyés (25 960 en train + 6 490 en val), avec 6 colonnes de toxicité. Règle du OU : un commentaire est **toxique (1) si au moins une des 6 colonnes vaut 1**, sinon non toxique (0). On obtient 50 % de toxiques et 50 % de non toxiques, donc un jeu équilibré.

Le test garde uniquement les commentaires de `val_dataset.csv` absents du train : 5 182 commentaires étaient déjà présents dans le train (même identifiant ou même texte) et auraient faussé le score.

**Résultats sur le test.**

| Précision | Rappel | F1    | Temps d'inférence     |
| ---------- | ------ | ----- | ---------------------- |
| 0,894      | 0,911  | 0,902 | 0,4 ms par commentaire |

**Interprétation.**

- **Précision 0,89** : quand le modèle dit « toxique », il a raison 9 fois sur 10 (69 faux positifs sur 1 308).
- **Rappel 0,91** : il retrouve 91 % des commentaires réellement toxiques (57 oubliés).
- **Équilibré** : précision et rappel sont proches, le modèle ne favorise pas une classe. Le jeu est équilibré et la règle du OU regroupe tous les types de toxicité.
- **Rapide** : 0,4 ms par commentaire, il peut analyser des milliers de textes par seconde.
- **Limites** : il ne comprend pas le contexte (ironie, citation d'une insulte) et ne connaît que les mots vus à l'entraînement.

#### BERT (modèle transformer)

Script : `python -m src.models.bert_binary`. Résultats détaillés : `reports/results_bert.json`.

**Modèle.** DistilBERT (`distilbert-base-uncased`), une version allégée de BERT (66 millions de paramètres, environ 2 fois plus rapide). Même jeu de données, même règle du OU et même découpage (train, dev, test) que le LSTM, donc les scores sont comparables. Réglages : lots de 8, 96 mots maximum par commentaire, 2 epochs ; le meilleur epoch (le 1er) est choisi sur le dev.

**Résultats sur le test.**

| Précision | Rappel | F1    | Temps d'inférence     |
| ---------- | ------ | ----- | ---------------------- |
| 0,941      | 0,919  | 0,930 | 6,5 ms par commentaire |

**Interprétation.**

- **Précision 0,94** : quand le modèle dit « toxique », il a raison 19 fois sur 20 (37 faux positifs sur 1 308).
- **Rappel 0,92** : il retrouve 92 % des commentaires réellement toxiques (52 oubliés).
- **Fiable** : le F1 du dev (0,932) et celui du test (0,930) sont presque identiques, le modèle ne sur-apprend pas.
- **Meilleur que le LSTM** : +2,8 points de F1 (0,930 contre 0,902), surtout grâce à la précision (0,941 contre 0,894), car il comprend mieux le contexte des phrases.
- **Plus lent** : 6,5 ms par commentaire, soit environ 16 fois plus que le LSTM (0,4 ms), mais cela reste plus de 150 commentaires par seconde.
- **Limites** : il est plus lourd à entraîner et à déployer que le LSTM (268 Mo de poids contre 16 Mo, environ 23 minutes d'entraînement pour 2 epochs) et rate encore environ 8 % des commentaires toxiques.

#### LAYA (modèle décisionnaire)

Script : `python -m src.models.laya_binary --train-rows 10000`. Résultats détaillés : `reports/results_laya.json`.

**Modèle.** LAYA (`convaiinnovations/laya`), un modèle « décisionnaire » construit sur l'encodeur ModernBERT-large (environ 400 millions de paramètres), entraîné par renforcement (RLCD) pour donner des probabilités bien calibrées. On lui pose une seule question binaire : « ce commentaire est-il toxique ? ». Même jeu de données, même règle du OU (colonne `label`) et même dev et test que le LSTM et BERT.

**Réglages.** 10 000 commentaires tirés au hasard dans le train (pour limiter le temps de calcul), 1 epoch, 128 mots maximum. L'encodeur est gelé, seule la tête de décision est entraînée : le modèle complet ne tient pas dans les 8 Go de la carte graphique. Entraînement en 5 minutes 20 sur une RTX 5050.

**Résultats sur le test.**

| Précision | Rappel | F1    | Temps d'inférence      |
| ---------- | ------ | ----- | ----------------------- |
| 0,908      | 0,906  | 0,907 | 10,3 ms par commentaire |

**Interprétation.**

- **Précision 0,91** : quand le modèle dit « toxique », il a raison 9 fois sur 10 (59 faux positifs sur 1 308).
- **Rappel 0,91** : il retrouve 91 % des commentaires réellement toxiques (60 oubliés).
- **Équilibré et fiable** : précision et rappel sont presque égaux, et le F1 du dev (0,917) est proche de celui du test (0,907).
- **Bien calibré** : l'erreur de calibration (ECE) passe de 0,070 à 0,023 après entraînement. Une probabilité de 0,9 veut vraiment dire « toxique 9 fois sur 10 », ce qui est utile pour un score.
- **Comparaison** : légèrement meilleur que le LSTM (F1 0,907 contre 0,902), en dessous de BERT (0,930). Il n'a pourtant vu que 10 000 commentaires (contre 23 364 pour les deux autres) et son encodeur n'a pas été entraîné.
- **Limites** : c'est le plus lent des trois (10,3 ms par commentaire, environ 100 par seconde) et le plus lourd (840 Mo de poids). Entraîner aussi l'encodeur demanderait une carte avec plus de mémoire.

### 3. Comparer précision, rappel et temps de traitement

Résultats sur le même test (1 308 commentaires jamais vus à l'entraînement).

| Modèle | Précision      | Rappel          | F1              | AUC             | Faux positifs | Oubliés     | Inférence       | Poids           | Entraînement                        |
| ------- | --------------- | --------------- | --------------- | --------------- | ------------- | ------------ | ---------------- | --------------- | ------------------------------------ |
| LSTM    | 0,894           | 0,911           | 0,902           | –              | 69            | 57           | **0,4 ms** | **16 Mo** | rapide, 23 364 lignes                |
| BERT    | **0,941** | **0,919** | **0,930** | **0,983** | **37**  | **52** | 6,5 ms           | 268 Mo          | 23 min, 23 364 lignes                |
| LAYA    | 0,908           | 0,906           | 0,907           | 0,973           | 59            | 60           | 10,3 ms          | 840 Mo          | 5 min, 10 000 lignes, encodeur gelé |

Les temps d'inférence ont été mesurés sur GPU, par lots.

**Conclusion : nous retenons le LSTM.**

BERT a la meilleure précision (0,941 contre 0,894) et le meilleur F1. Nous choisissons pourtant le LSTM, pour trois raisons :

- **Temps de réponse très bon** : 0,4 ms par commentaire, contre 6,5 ms pour BERT et 10,3 ms pour LAYA.
- **Précision satisfaisante** : 0,89, soit 9 commentaires jugés inappropriés sur 10 qui le sont vraiment, avec un rappel de 0,91. L'écart avec BERT est d'environ 5 points.
- **Léger, donc peu coûteux** : 16 Mo de poids contre 268 Mo (BERT) et 840 Mo (LAYA). Il tourne sur un simple CPU, sans GPU. Sur GCP, l'image est petite, le service demande peu de mémoire et chaque requête consomme très peu de temps de calcul : la facture est plus basse qu'avec BERT ou LAYA.

L'API utilise donc le LSTM par défaut (LAYA reste disponible en option, en local uniquement). Si la qualité de détection comptait plus que le coût, BERT serait le meilleur choix.

## Étape 3 : déploiement du modèle en API Cloud

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* transformer le modèle IA en service accessible ; utiliser FastAPI, Flask ou un service Cloud (Vertex AI, Scaleway, AWS…).
>
> *Exercice :*
>
> 1. Exporter le modèle et le déployer dans le Cloud.
> 2. Créer une API recevant un texte et renvoyant un score.
> 3. Tester plusieurs exemples de requêtes.

| Question du TP                                         | Partie                        |
| ------------------------------------------------------ | ----------------------------- |
| Créer une API recevant un texte et renvoyant un score | 1 (sécurité résumée en 2) |
| Exporter le modèle                                    | 3                             |
| Le déployer dans le Cloud                             | 4 (GKE)                       |
| Tester plusieurs exemples de requêtes                 | 5                             |

Code : `src/api/main.py` (routes) et `src/api/predictor.py` (chargement du modèle). Tests : `tests/test_api.py`.

**Choix des outils.** L'API est écrite avec **FastAPI** plutôt que Flask : validation automatique des entrées (Pydantic), documentation interactive générée (`/docs`), et c'est l'outil cité en premier par le TP. Elle est déployée sur **GKE** (Google Kubernetes Engine) plutôt que sur un service géré comme Vertex AI : Kubernetes donne la main sur le nombre de pods, les sondes de santé, l'autoscaling et les mises à jour progressives, nécessaires aux étapes 5 et 6.

### 1. Créer une API recevant un texte et renvoyant un score

**Processus.**

1. L'API est écrite avec **FastAPI** (`src/api/main.py`) et expose la route `POST /score`.
2. Le client envoie un texte en JSON (`{"text": "..."}`, de 1 à 5 000 caractères), avec sa clé API dans l'en-tête `X-API-Key`.
3. L'API vérifie la clé et la taille du texte. Sinon elle répond 401 ou 422.
4. Le texte suit le même chemin que les données d'entraînement : **anonymisation** (regex + spaCy), puis **nettoyage**, puis **LSTM**. Le modèle (`src/api/predictor.py`) est chargé une seule fois, au démarrage.
5. Le LSTM renvoie la probabilité que le texte soit toxique. L'API la multiplie par 100 : c'est le **score**, de 0 (respectueux) à 100 (très toxique).
6. L'API répond avec le score et la décision `toxic` (`true` si le score atteint 50).
7. Dans les logs, seul un identifiant pseudonyme du texte est écrit, jamais le texte.

Le modèle chargé est le LSTM à un seul label (dossier `artifacts/`).

| Route            | Rôle                                                         | Clé API                                         |
| ---------------- | ------------------------------------------------------------- | ------------------------------------------------ |
| `POST /score`  | texte → score de 0 à 100 et décision toxique / non toxique | oui                                              |
| `GET /health`  | état du service                                              | non                                              |
| `GET /metrics` | métriques Prometheus (requêtes, latence, scores)            | oui (`X-API-Key` ou `Authorization: Bearer`) |
| `GET /docs`    | documentation interactive (Swagger)                           | non                                              |

Exemple :

```bash
curl -X POST http://localhost:8000/score \
  -H "X-API-Key: test" -H "Content-Type: application/json" \
  -d '{"text": "Thank you for your help, have a nice day"}'
```

```json
{"score": 2.0, "toxic": false, "model": "lstm", "latency_ms": 12.58}
```

**Limite connue.** Le LSTM réagit aux mots plutôt qu'au sens. Les phrases qui commencent par « you are » sont jugées toxiques même quand elles sont gentilles : « You are great » obtient 63,0 et « You are a wonderful person » 98,7. Dans les données d'entraînement, cette tournure précède très souvent une insulte. De plus, le mot `person` y sert d'étiquette d'anonymisation et apparaît dans beaucoup de commentaires toxiques. BERT, qui tient compte du contexte, devrait moins se tromper sur ces cas.

**Tester LAYA.** Le champ `model` de `/score` permet de choisir le modèle : `"lstm"` (par défaut) ou `"laya"`. LAYA est chargé à sa première utilisation (quelques secondes) et nécessite le dossier `models/laya_binary/`. Il renvoie une seule probabilité, `toxic`. Il corrige l'erreur du LSTM : « You are a wonderful person » obtient 2,6 avec LAYA contre 98,7 avec le LSTM. Il est en revanche plus lent (environ 40 ms par requête sur GPU). L'image Docker ne contient pas LAYA : l'API y répond 503 si on le demande.

```bash
curl -X POST http://localhost:8000/score \
  -H "X-API-Key: test" -H "Content-Type: application/json" \
  -d '{"text": "You are a wonderful person", "model": "laya"}'
```

### 2. Sécurité et RGPD (résumé ; détail à l'étape 4)

- **Clé API obligatoire** dans l'en-tête `X-API-Key`, comparée en temps constant pour éviter les attaques par mesure du temps de réponse. Sans clé valide, l'API répond 401.
- **Validation des entrées** : texte de 1 à 5 000 caractères, sinon l'API répond 422.
- **Aucun texte stocké ni journalisé** : les logs ne contiennent qu'un identifiant pseudonyme du texte (HMAC-SHA256 avec une clé secrète), le modèle utilisé, le score et la latence. Ni le texte ni sa longueur ne sont écrits.
- **Métriques protégées** : `/metrics` demande aussi la clé API, pour ne pas exposer l'activité du service.
- **Anonymisation avant le modèle** : les données personnelles sont retirées du texte avant même d'arriver au modèle.
- **Secrets hors du code** : la clé API et la clé des logs sont stockées dans Secret Manager, puis dans un secret Kubernetes.
- **HTTPS uniquement** : sur GKE, le seul accès public est un Ingress en HTTPS (TLS 1.2 minimum, aucun port 80).
- **Conteneur non root** : l'image Docker et les pods tournent avec un utilisateur sans privilèges.

Le détail des mesures de sécurité et de conformité est dans l'**étape 4**.

### 3. Exporter le modèle

**Processus.**

1. Après l'entraînement, le LSTM est exporté en **3 fichiers**, dans le dossier `artifacts/` (16,5 Mo au total) :
   - `lstm.pt` : les poids du modèle ;
   - `vocab.json` : le vocabulaire de 30 000 mots, indispensable (sans lui, les scores sont faux) ;
   - `config.json` : tailles du réseau, 128 mots maximum par texte, seuil de décision 0,5.
2. Le `Dockerfile` construit une **image Docker** légère : Python 3.12 slim, PyTorch version CPU, le code de l'API et ces 3 fichiers (`artifacts/`). Les fichiers `.dockerignore` et `.gcloudignore` excluent les données, les rapports et le dossier `models/` (LAYA, anciens modèles).
3. L'image est stockée dans **Artifact Registry**, le registre privé de Google Cloud (partie 4, étapes 5 et 6), puis lancée dans des pods sur GKE.

**Mettre à jour le modèle.** Remplacer les 3 fichiers de `artifacts/` en local, les pousser sur GitHub, faire `git pull` dans Cloud Shell, puis suivre l'étape 13 de la partie 4.

Variables d'environnement de l'API :

| Variable                  | Rôle                                                                                                                                                  | Défaut                         |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------- |
| `API_KEY`               | clé attendue dans `X-API-Key` (**obligatoire**)                                                                                               | aucun                           |
| `LOG_SALT`              | clé secrète du HMAC écrit dans les logs                                                                                                             | aléatoire à chaque démarrage |
| `USE_NER`               | `1` = anonymisation spaCy active, `0` = regex seules                                                                                               | `1`                           |
| `ARTIFACTS_DIR`         | dossier des fichiers du LSTM                                                                                                                           | `artifacts/`                  |
| `API_KEYS`              | plusieurs clients : `nom:clé,nom:clé` (voir étape 4)                                                                                              | aucun                           |
| `RATE_LIMIT_PER_MINUTE` | requêtes par client et par minute, sur chaque pod (`0` = sans limite)                                                                               | `600`                         |
| `DEMO_ENABLED`          | `1` = page web `/` active, `0` = désactivée (404)                                                                                              | `1`                           |
| `TRUSTED_PROXY_HOPS`    | entrées ajoutées à `X-Forwarded-For` par les proxys de confiance (2 derrière l'Ingress GKE) ; les entrées écrites par le client sont ignorées | `0`                           |
| `PORT`                  | port d'écoute du conteneur                                                                                                                            | `8080`                        |

### 4. Déployer le modèle dans le Cloud : GKE

Le modèle est déployé sur **Kubernetes** (GKE, mode **Autopilot**), qui donne du contrôle sur le nombre de pods, les sondes de santé, l'autoscaling et les mises à jour progressives. Tout se fait dans **Cloud Shell**, le terminal intégré à la console Google Cloud, où `gcloud`, `kubectl`, `git` et Docker sont déjà installés. La région choisie est `europe-west9` (Paris) : les données restent en France, ce qui est conforme au RGPD.

Les fichiers Kubernetes sont dans `k8s/` :

```
Client ──HTTPS (TLS 1.2+, IP fixe)──▶ Load balancer Google (Ingress)
       ──réseau privé Google──▶ Service interne (ClusterIP) ──▶ pods dss-api :8080
```

| Fichier                     | Rôle                                                                                                          |
| --------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `k8s/deployment.yaml`     | pods de l'API, ressources CPU/mémoire, sondes `/health`, secrets, conteneur non root, répartition par zone |
| `k8s/service.yaml`        | service **interne** au cluster (pas d'IP publique), qui répartit les requêtes entre les pods           |
| `k8s/ingress.yaml`        | **seul point d'entrée public** : load balancer HTTPS, IP fixe, aucun accès HTTP                        |
| `k8s/frontendconfig.yaml` | politique TLS du load balancer : TLS 1.2 minimum                                                               |
| `k8s/backendconfig.yaml`  | health check du load balancer sur `/health`, logs du load balancer                                           |
| `k8s/hpa.yaml`            | autoscaling : de 3 à 15 pods selon l'usage CPU (seuil 50 %) ; 2 à 10 pods à 70 % avant l'étape 5           |
| `k8s/pdb.yaml`            | au moins 2 pods prêts pendant les maintenances de Google (ajouté après l'étape 5)                          |
| `k8s/managed-cert.yaml`   | certificat HTTPS géré par Google pour `34-54-213-190.sslip.io` (étape 4, partie 8)                        |
| `k8s/monitoring/`         | collecte des métriques et Grafana (étape 6)                                                                  |

> **En une commande :** `make bootstrap cluster build deploy smoke` (scripts de `deploy/`). Les étapes ci-dessous détaillent et expliquent ce que font ces scripts.

**1. Préparer le projet.** Sur [console.cloud.google.com](https://console.cloud.google.com), créer un projet, noter son ID et activer la facturation. Ouvrir Cloud Shell avec l'icône `>_` en haut à droite.

**2. Récupérer le code.** Le dépôt contient le code, le `Dockerfile`, les fichiers `k8s/` et les 3 fichiers du modèle (`artifacts/`).

```bash
git clone https://github.com/Baunell-ctrl/Digital-Social-Score.git
cd Digital-Social-Score
ls artifacts    # lstm.pt, vocab.json, config.json
```

**3. Variables et services.**

```bash
PROJECT=<ID_DU_PROJET>
REGION=europe-west9
TAG=v1
IMAGE=$REGION-docker.pkg.dev/$PROJECT/dss/api:$TAG
gcloud config set project $PROJECT
gcloud services enable artifactregistry.googleapis.com cloudbuild.googleapis.com container.googleapis.com \
  secretmanager.googleapis.com compute.googleapis.com cloudkms.googleapis.com
```

**4. Créer les secrets.** La clé API et la clé des logs sont stockées dans Secret Manager, jamais dans le code. Donner aussi au compte de service de Cloud Build les droits nécessaires pour construire et pousser l'image (principe du moindre privilège).

```bash
API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Clé API : $API_KEY"     # à conserver
printf "%s" "$API_KEY" | gcloud secrets create dss-api-key --data-file=-
python3 -c "import secrets; print(secrets.token_hex(32), end='')" | gcloud secrets create dss-log-salt --data-file=-

NUM=$(gcloud projects describe $PROJECT --format="value(projectNumber)")
for ROLE in roles/artifactregistry.writer roles/storage.objectViewer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding $PROJECT     --member="serviceAccount:$NUM-compute@developer.gserviceaccount.com" --role=$ROLE --condition=None -q > /dev/null
done
```

**5. Créer le dépôt Artifact Registry** (une seule fois). C'est le registre privé qui stocke les images Docker versionnées.

```bash
gcloud artifacts repositories create dss --repository-format=docker --location=$REGION \
  --description="Images de l'API Digital Social Score"
```

**6. Construire et pousser l'image.** Cloud Build lit le `Dockerfile`, construit l'image et la pousse dans le dépôt (5 à 10 minutes).

```bash
gcloud builds submit --tag $IMAGE .
gcloud artifacts docker images list $REGION-docker.pkg.dev/$PROJECT/dss   # l'image doit apparaître
```

Variante avec Docker (sur un poste où Docker tourne) :

```bash
gcloud auth configure-docker $REGION-docker.pkg.dev
docker build -t $IMAGE .
docker push $IMAGE
```

**7. Créer le cluster GKE.** Le mode **Autopilot** laisse Google gérer les machines : on ne paie que les ressources demandées par les pods. La création prend 5 à 10 minutes.

```bash
gcloud container clusters create-auto dss-cluster --region $REGION
gcloud container clusters get-credentials dss-cluster --region $REGION   # configure kubectl
kubectl get nodes
```

Le cluster doit pouvoir lire les images du dépôt :

```bash
gcloud artifacts repositories add-iam-policy-binding dss --location=$REGION \
  --member="serviceAccount:$NUM-compute@developer.gserviceaccount.com" --role=roles/artifactregistry.reader
```

**8. Chiffrer les Secrets du cluster avec une clé Cloud KMS.** Par défaut, Google chiffre les données du cluster avec ses propres clés. Ici, les Secrets Kubernetes (clé API, clé des logs, clé privée TLS) sont **en plus** chiffrés avec une clé qui appartient au projet (*application-layer secrets encryption*) : on contrôle qui peut l'utiliser, on peut la désactiver, et chaque usage est tracé dans les logs d'audit. La clé change automatiquement tous les 90 jours.

```bash
gcloud kms keyrings create dss-keyring --location $REGION
gcloud kms keys create dss-secrets-key --keyring dss-keyring --location $REGION --purpose encryption \
  --rotation-period 90d --next-rotation-time $(date -u -d "+90 days" +%Y-%m-%dT%H:%M:%SZ)

# seul le service GKE du projet peut chiffrer / déchiffrer avec cette clé
gcloud kms keys add-iam-policy-binding dss-secrets-key --keyring dss-keyring --location $REGION \
  --member="serviceAccount:service-$NUM@container-engine-robot.iam.gserviceaccount.com" \
  --role=roles/cloudkms.cryptoKeyEncrypterDecrypter

gcloud container clusters update dss-cluster --region $REGION \
  --database-encryption-key projects/$PROJECT/locations/$REGION/keyRings/dss-keyring/cryptoKeys/dss-secrets-key
gcloud container clusters describe dss-cluster --region $REGION --format="value(databaseEncryption)"   # state=ENCRYPTED
```

La clé doit être dans la même région que le cluster. La mise à jour du cluster prend plusieurs minutes.

**9. Créer le secret Kubernetes.** On reprend les valeurs stockées dans Secret Manager (étape 4), sans les écrire dans un fichier.

```bash
kubectl create secret generic dss-secrets \
  --from-literal=API_KEY="$(gcloud secrets versions access latest --secret=dss-api-key)" \
  --from-literal=LOG_SALT="$(gcloud secrets versions access latest --secret=dss-log-salt)"
```

**10. Préparer le HTTPS : IP fixe, politique TLS et certificat.** Le projet n'a pas de nom de domaine : on utilise un **certificat auto-signé** valable pour l'IP fixe. (Ce certificat sert de secours : le service présente en priorité le certificat Google du nom `34-54-213-190.sslip.io`, voir étape 4, partie 8.) Le trafic est chiffré exactement comme avec un certificat public ; la différence est que le navigateur ne le connaît pas et affiche un avertissement (voir étape 4, partie 8).

```bash
gcloud compute addresses create dss-ip --global
IP=$(gcloud compute addresses describe dss-ip --global --format="value(address)")
gcloud compute ssl-policies create dss-tls12 --profile MODERN --min-tls-version 1.2

openssl req -x509 -newkey rsa:2048 -nodes -days 365 -keyout tls.key -out tls.crt \
  -subj "/CN=dss-api" -addext "subjectAltName=IP:$IP"
kubectl create secret tls dss-tls --cert=tls.crt --key=tls.key
rm tls.key        # la clé privée ne reste que dans le Secret, chiffré par KMS
```

`tls.crt` (le certificat public) est gardé : les clients l'utilisent pour vérifier qu'ils parlent bien au serveur. Les fichiers `*.key` et `tls.crt` sont dans le `.gitignore`.

**11. Déployer.**

```bash
sed "s|IMAGE|$IMAGE|" k8s/deployment.yaml | kubectl apply -f -
kubectl apply -f k8s/backendconfig.yaml -f k8s/frontendconfig.yaml -f k8s/service.yaml \
  -f k8s/ingress.yaml -f k8s/hpa.yaml -f k8s/pdb.yaml
kubectl rollout status deployment/dss-api          # attend que les pods soient prêts
kubectl get ingress dss-api --watch                # 5 à 10 minutes : l'adresse doit être $IP
kubectl describe ingress dss-api | grep -A2 backends   # prêt quand les backends sont HEALTHY
```

**Cluster déjà déployé en HTTP ?** L'ancien Service était de type `LoadBalancer`, avec une IP publique en HTTP. Faire les étapes 8 et 10, puis :

```bash
kubectl delete svc dss-api                          # supprime l'ancienne IP publique HTTP
kubectl get secrets -o json | kubectl replace -f -  # rechiffre les Secrets existants avec la clé KMS
```

et appliquer les fichiers de l'étape 11. Pendant la création du load balancer (5 à 10 minutes), l'API n'est pas joignable depuis l'extérieur.

**12. Tester.** `--cacert tls.crt` demande à `curl` de vérifier le certificat du serveur (plutôt que `-k`, qui désactive toute vérification).

```bash
API_KEY=$(gcloud secrets versions access latest --secret=dss-api-key)
curl --cacert tls.crt https://$IP/health
curl --cacert tls.crt -X POST https://$IP/score -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "You are a stupid idiot"}'
curl --cacert tls.crt -X POST https://$IP/score -H "Content-Type: application/json" -d '{"text": "hello"}'   # sans clé : 401
curl -v --cacert tls.crt https://$IP/health 2>&1 | grep -E "SSL connection|subject|issuer"   # détails TLS
curl -m 5 http://$IP/health                                   # doit échouer : aucun accès HTTP
openssl s_client -connect $IP:443 -tls1_1 </dev/null           # doit échouer : TLS 1.1 refusé
```

La documentation interactive est disponible sur `https://$IP/docs` (bouton **Authorize** pour saisir la clé). Par le nom `https://34-54-213-190.sslip.io/docs`, le navigateur n'affiche aucun avertissement (certificat Google) ; par l'adresse IP seule, il affiche « connexion non privée », attendue avec le certificat auto-signé.

**13. Mettre à jour sans coupure.** Construire une nouvelle image avec un nouveau tag, puis remplacer l'image du déploiement. Kubernetes démarre un nouveau pod, attend qu'il soit prêt, puis arrête l'ancien (*rolling update*).

```bash
TAG=v2; IMAGE=$REGION-docker.pkg.dev/$PROJECT/dss/api:$TAG
gcloud builds submit --tag $IMAGE .
kubectl set image deployment/dss-api api=$IMAGE
kubectl rollout status deployment/dss-api
kubectl rollout undo deployment/dss-api            # retour à la version précédente si besoin
```

**14. Supervision et tests de panne** (étapes 5 et 6 du TP).

```bash
kubectl logs -l app=dss-api --tail=50              # logs pseudonymes des pods
kubectl top pods                                   # CPU et mémoire par pod
kubectl get hpa dss-api --watch                    # nombre de pods pendant un test de charge
kubectl delete pod -l app=dss-api --wait=false     # simule une panne : Kubernetes recrée les pods
```

Les logs et métriques sont aussi visibles dans la console : **Kubernetes Engine → Charges de travail → dss-api**.

**15. Supprimer** (le cluster, l'IP fixe et le load balancer sont facturés tant qu'ils existent).

```bash
kubectl delete -f k8s/ingress.yaml -f k8s/hpa.yaml -f k8s/pdb.yaml -f k8s/service.yaml -f k8s/deployment.yaml \
  -f k8s/frontendconfig.yaml -f k8s/backendconfig.yaml
gcloud container clusters delete dss-cluster --region $REGION
gcloud compute addresses delete dss-ip --global
gcloud compute ssl-policies delete dss-tls12
# une clé KMS ne peut pas être supprimée d'un coup : on détruit ses versions (effectif après 30 jours)
gcloud kms keys versions list --key dss-secrets-key --keyring dss-keyring --location $REGION
gcloud kms keys versions destroy 1 --key dss-secrets-key --keyring dss-keyring --location $REGION
```

### 5. Tester plusieurs exemples de requêtes : résultats sur le cloud (GKE)

Tests faits depuis Cloud Shell avec `curl`, sur l'API déployée dans le cluster GKE (2 pods, modèle LSTM), avant le passage en HTTPS : les requêtes de la capture sont donc en `http://`.

![Requêtes de test sur l'API déployée dans GKE](reports/images/gke_tests_requetes.png)

**Commentaires toxiques** (tous détectés) :

| Texte (début)                                                                      | Score | Toxique |
| ----------------------------------------------------------------------------------- | ----- | ------- |
| « Honestly you are the dumbest person I have ever had the misfortune… »          | 100,0 | oui     |
| « I swear if I ever see you at that bar again I am going to beat… »              | 100,0 | oui     |
| « Look at this clown thinking his opinion matters… »                             | 100,0 | oui     |
| « Nice profile picture, sweetheart. Send me something a little more revealing… » | 91,3  | oui     |
| « You are a complete waste of oxygen… »                                          | 99,6  | oui     |

**Commentaires non toxiques** (tous acceptés) : 5 messages polis ou neutres (remerciement, désaccord argumenté, avis sur une mise à jour…), avec des scores de **0,3 · 0,7 · 11,9 · 16,9 · 0,2**.

**Tests de sécurité** :

| Requête                    | Réponse attendue | Réponse obtenue                     |
| --------------------------- | ----------------- | ------------------------------------ |
| sans en-tête `X-API-Key` | 401               | 401 `Clé API invalide ou absente` |
| avec une mauvaise clé      | 401               | 401 (rapport d'attaque, 12/11)       |
| texte vide                  | 422               | 422 (rapport d'attaque, 12/11)       |

**Analyse.**

- **10 sur 10** : les 5 textes toxiques dépassent 90 et les 5 textes non toxiques restent sous 17. Le harcèlement sexuel (« Send me something a little more revealing »), qui ne contient aucune insulte, est aussi détecté (91,3).
- **Latence** : de 19 à 27 ms par requête côté API, anonymisation spaCy comprise, sur CPU.
- **Limite observée** : sur des phrases courtes testées à part, le LSTM se trompe encore. « I am happy to be alive » obtient **76,9** (faux positif) : le modèle réagit aux mots isolés (« alive » apparaît souvent dans des menaces) et pas au sens de la phrase. C'est la limite déjà décrite plus haut (« Limite connue »).

## Étape 4 : sécurisation et conformité RGPD

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* mettre en place authentification et chiffrement ; garantir la conformité RGPD (validation, logs pseudonymes).
>
> *Exercice :*
>
> 1. Configurer JWT ou clé API.
> 2. Mettre en place HTTPS, IAM et registre RGPD.

Code : `src/api/main.py` et `k8s/`. Tests automatiques : `tests/test_api.py` (21 tests, dont 3 pour les métriques de l'étape 6).

| Exigence du TP             | Mise en œuvre                                                                                                                                | Partie |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| JWT ou clé API            | clé API par client, stockée dans Secret Manager puis dans un Secret Kubernetes                                                              | 1      |
| HTTPS et chiffrement       | HTTPS uniquement (TLS 1.2+) via l'Ingress GKE ; Secrets chiffrés par une clé Cloud KMS (prévu, **pas encore actif** : voir partie 2) | 2      |
| Validation des entrées    | taille du texte, taille de la requête, valeurs autorisées                                                                                   | 3      |
| Logs pseudonymes           | HMAC-SHA256 du texte et de l'IP, jamais le texte, la clé ou l'IP en clair                                                                    | 4      |
| IAM                        | chaque identité n'a que les rôles nécessaires ; les pods n'ont aucun droit Google Cloud                                                    | 5      |
| Registre RGPD              | registre du traitement « scoring via l'API »                                                                                                | 6      |
| Grille de tests (livrable) | 14 tests, en local et sur le cluster                                                                                                          | 7      |

### 1. Configurer JWT ou clé API : une clé API par client

Chaque requête vers `/score` et `/metrics` doit porter une clé dans l'en-tête `X-API-Key` (ou `Authorization: Bearer` pour Prometheus). Sans clé valide, l'API répond **401**.

- **Comparaison en temps constant** (`secrets.compare_digest`) : le temps de réponse ne révèle pas combien de caractères de la clé sont justes.
- **Une clé par client** : la variable `API_KEYS` accepte plusieurs clés nommées, par exemple `moderation:<clé1>,mobile:<clé2>`. Le nom du client (jamais la clé) apparaît dans les logs, et on peut **révoquer un client** en retirant sa clé sans couper les autres. `API_KEY` (une seule clé, client `default`) reste accepté.
- **Clés fortes** : générées avec `secrets.token_urlsafe(32)` (256 bits d'aléa). L'API écrit un avertissement au démarrage si une clé fait moins de 16 caractères.
- **Stockage** : la source de vérité est **Secret Manager** ; les pods lisent les clés dans le Secret Kubernetes `dss-secrets`, chiffré par KMS (partie 2). Les clés n'apparaissent ni dans le code, ni dans Git, ni dans les fichiers `k8s/`.

**Pourquoi une clé API plutôt qu'un JWT ?** L'API est appelée par des applications (outil de modération, back-end d'un réseau social), pas par des personnes qui se connectent. Une clé API suffit pour ce cas « machine à machine » et ne demande pas de serveur d'identité. Un JWT (OAuth2) deviendrait utile s'il fallait des comptes utilisateurs, des droits différents selon les rôles ou des jetons à durée de vie courte.

**Plusieurs clients et changement de clé sans coupure.** On ajoute `API_KEYS` dans le Secret Kubernetes, puis on redémarre les pods un par un :

```bash
# 1. pendant la transition, l'ancienne et la nouvelle clé fonctionnent
kubectl create secret generic dss-secrets --dry-run=client -o yaml \
  --from-literal=API_KEYS="moderation:$ANCIENNE_CLE,moderation-v2:$NOUVELLE_CLE" \
  --from-literal=LOG_SALT="$(gcloud secrets versions access latest --secret=dss-log-salt)" | kubectl apply -f -
kubectl rollout restart deployment/dss-api
# 2. les clients passent à la nouvelle clé
# 3. on retire l'ancienne clé de API_KEYS et on redémarre de nouveau
```

### 2. Mettre en place HTTPS et le chiffrement

| Où                        | Comment                                                                                                                                                                                                                                                                                                                                                                                               |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| En transit (client → API) | **HTTPS uniquement** via l'Ingress GKE : aucun port 80, **TLS 1.2 minimum** (politique `dss-tls12`, profil MODERN), certificat **géré par Google** pour `34-54-213-190.sslip.io` (l'auto-signé reste en secours)                                                                                                                                                             |
| En transit (dans Google)   | le load balancer joint les pods par le réseau privé de Google ; le Service des pods est interne (`ClusterIP`, pas d'IP publique)                                                                                                                                                                                                                                                                  |
| Au repos : Secrets         | Secrets Kubernetes (clé API, clé des logs, clé privée TLS) chiffrés avec la clé Cloud KMS `dss-secrets-key` du projet, qui change tous les 90 jours **À activer** : vérifié le 12/11/2026, le cluster n'utilise pas encore cette clé (`databaseEncryption: DECRYPTED`, étape 8 de la partie 4 non appliquée). Les Secrets restent chiffrés par Google, avec ses propres clés. |
| Au repos : le reste        | images Docker, Secret Manager et logs chiffrés par défaut par Google Cloud (AES-256)                                                                                                                                                                                                                                                                                                                |
| Données des utilisateurs  | **aucune donnée stockée** : le texte reste en mémoire le temps de la requête (quelques ms)                                                                                                                                                                                                                                                                                                  |

L'API renvoie aussi l'en-tête `Strict-Transport-Security`, qui demande au navigateur de n'utiliser que HTTPS.

### 3. Validation des entrées et protection de l'API

| Protection            | Règle                                                                                                                    | Réponse si non respectée |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| Texte                 | de 1 à 5 000 caractères                                                                                                 | 422                        |
| Champ `model`       | `lstm` ou `laya` uniquement                                                                                           | 422                        |
| Taille de la requête | 32 Ko maximum, comptés sur les octets réellement reçus (même sans `Content-Length`)                                 | 413                        |
| Nombre de requêtes   | 600 par minute et par client, sur chaque pod (`RATE_LIMIT_PER_MINUTE`)                                                  | 429 + `Retry-After`      |
| En-têtes HTTP        | `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, `Cache-Control: no-store`, HSTS                                | –                         |
| Pods                  | utilisateur non root, pas d'élévation de privilèges, aucune capacité Linux, pas de jeton d'accès à l'API Kubernetes | –                         |

**Pourquoi limiter le nombre de requêtes ?** Le cours cite le **vol de modèle** (*model stealing*) : un attaquant envoie des milliers de textes et utilise les scores pour entraîner une copie du modèle. La limite par client rend cette attaque beaucoup plus lente et la rend visible dans les logs (`rate_limited`). Elle protège aussi contre l'épuisement des ressources.

Pendant les **tests de charge** (étape 5), une seule clé envoie beaucoup de requêtes : il faut lever la limite pour mesurer la capacité réelle du service, puis la remettre :

```bash
kubectl set env deployment/dss-api RATE_LIMIT_PER_MINUTE=0
# ... test de charge ...
kubectl set env deployment/dss-api RATE_LIMIT_PER_MINUTE=600
```

### 4. Logs pseudonymes et journal d'audit

L'API écrit une ligne JSON par événement, collectée par Cloud Logging. Aucun log de l'API ne contient le texte, la clé API ou l'adresse IP en clair.

```json
{"event": "score", "client": "default", "model": "lstm", "text_id": "3f9a1c0b7e2d4a65", "score": 100.0, "latency_ms": 24.6}
{"event": "auth_failed", "path": "/score", "reason": "invalide", "ip_id": "8c41e0d2a9b3f710"}
{"event": "rate_limited", "client": "mobile"}
```

- `ip_id` vient de l'IP ajoutée par le load balancer Google (`TRUSTED_PROXY_HOPS=2`), pas de ce que le client écrit dans `X-Forwarded-For` : un attaquant ne peut pas falsifier le journal d'audit.
- `text_id` et `ip_id` sont des **HMAC-SHA256** calculés avec la clé secrète `LOG_SALT`. Ils permettent de repérer un même texte ou une même source répétés (attaque, spam), mais on ne peut pas retrouver le texte ou l'IP sans la clé. C'est une **pseudonymisation** au sens du RGPD.
- `auth_failed` et `rate_limited` forment le **journal d'audit** : ils servent à détecter une attaque (étape 6 du TP).
- Changer `LOG_SALT` rend les anciens identifiants impossibles à relier aux nouveaux.

```bash
kubectl logs -l app=dss-api --tail=100 | grep auth_failed
gcloud logging read 'jsonPayload.event="auth_failed"' --limit 5 --format=json
```

**Logs du load balancer.** En plus des logs de l'API, le load balancer de l'Ingress enregistre chaque requête HTTP **avec l'adresse IP du client** (`k8s/backendconfig.yaml`, section `logging`). Ce sont des données personnelles, gardées 30 jours et déclarées dans le registre (partie 6). Ils servent à enquêter sur une attaque. Pour aller plus loin dans la minimisation, mettre `logging.enable: false` dans `k8s/backendconfig.yaml` : les métriques de Cloud Monitoring (requêtes, latence, erreurs) restent disponibles.

Durée de conservation des logs, fixée explicitement à 30 jours :

```bash
gcloud logging buckets update _Default --location=global --retention-days=30
```

### 5. Mettre en place IAM : moindre privilège

| Identité                                                         | Rôles                                                                  | Pourquoi                                                                   |
| ----------------------------------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Pods de l'API                                                     | **aucun** rôle Google Cloud, pas de jeton Kubernetes             | ils lisent leurs secrets dans les variables d'environnement ; rien d'autre |
| Nœuds GKE (`<NUM>-compute@…`)                                 | lecture d'Artifact Registry, écriture de logs                          | télécharger l'image, envoyer les logs                                    |
| Cloud Build (`<NUM>-compute@…`)                                | écriture dans Artifact Registry, lecture du stockage, logs             | construire et pousser l'image (étape 4 de la partie 4)                    |
| Agent de service GKE (`service-<NUM>@container-engine-robot…`) | chiffrer / déchiffrer avec **la seule** clé `dss-secrets-key` | chiffrement des Secrets (étape 8)                                         |
| Membres de l'équipe (conseillé)                                 | `roles/container.developer`, `roles/logging.viewer`                 | déployer et lire les logs                                                 |
| Responsable du projet                                             | `roles/owner`                                                         | projet, facturation, IAM                                                   |
| Clients de l'API                                                  | aucun rôle Google Cloud                                                | ils n'ont qu'une clé API                                                  |

```bash
gcloud iam service-accounts create dss-nodes --display-name="Noeuds GKE Digital Social Score"
SA=dss-nodes@$PROJECT.iam.gserviceaccount.com
for ROLE in roles/artifactregistry.reader roles/logging.logWriter roles/monitoring.metricWriter roles/monitoring.viewer; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$SA" --role=$ROLE --condition=None
done
gcloud container clusters create-auto dss-cluster --region $REGION --service-account=$SA
```

### 6. Mettre en place le registre RGPD du traitement « scoring de toxicité »

Ce registre complète celui de l'étape 1 (préparation du jeu de données). Modèle inspiré de celui de la CNIL.

| Rubrique                          | Contenu                                                                                                                                                                                                                                                                                |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Traitement**              | Calcul d'un score de toxicité (0 à 100) pour des textes envoyés à l'API Digital Social Score                                                                                                                                                                                       |
| **Responsable / rôles**    | Équipe projet Digital Social Score (TP). Pour un client qui modère sa plateforme avec l'API, ce client est responsable du traitement et l'équipe est **sous-traitante** ; Google Cloud est sous-traitant ultérieur (hébergement)                                            |
| **Finalité**               | Aider à la modération des contenus en ligne : repérer les messages injurieux, haineux ou harcelants. Le score est une **aide à la décision** : il ne doit pas entraîner seul une sanction contre une personne                                                              |
| **Base légale**            | Intérêt légitime (lutte contre le cyberharcèlement et les contenus haineux)                                                                                                                                                                                                        |
| **Personnes concernées**   | Auteurs des textes analysés et personnes citées dans ces textes                                                                                                                                                                                                                      |
| **Données traitées**      | Texte envoyé (peut contenir des noms, des opinions ou d'autres données personnelles). Données techniques : adresse IP (logs du load balancer)                                                                                                                                       |
| **Données sensibles**      | Un texte peut révéler une origine, une religion, une orientation sexuelle. Elles ne sont ni extraites ni stockées : le texte n'est utilisé que pour calculer le score                                                                                                              |
| **Minimisation**            | Anonymisation du texte (regex + spaCy) avant le modèle ; texte jamais stocké ni journalisé ; logs de l'API limités au nom du client, à des identifiants HMAC, au score et à la latence                                                                                           |
| **Durée de conservation**  | Texte : durée de la requête (quelques millisecondes, en mémoire). Logs : 30 jours. Secrets : jusqu'à leur rotation                                                                                                                                                                 |
| **Destinataires**           | Équipe projet (accès aux logs par IAM). Le client reçoit uniquement le score de son texte                                                                                                                                                                                           |
| **Transferts hors UE**      | Aucun transfert organisé : cluster, clé KMS et secrets en région `europe-west9` (Paris). Google Cloud reste une société américaine, couverte par ses clauses contractuelles (DPA)                                                                                              |
| **Mesures de sécurité**   | HTTPS uniquement (TLS 1.2+), clé API par client, Secrets chiffrés par Cloud KMS, IAM au moindre privilège, limitation des requêtes, validation des entrées, pods non root, journal d'audit pseudonymisé                                                                          |
| **Droits des personnes**    | Accès, effacement, opposition : aucun texte n'étant conservé, il n'y a rien à communiquer ni à effacer. Les identifiants HMAC ne permettent pas de retrouver un texte                                                                                                             |
| **Analyse d'impact (AIPD)** | À évaluer avant une mise en production : l'évaluation automatisée de contenus fait partie des critères de la CNIL. Ici, le score porte sur un texte et non sur une personne, et ne déclenche aucune décision automatique                                                        |
| **Page web**                | La page `/` n'ajoute aucun traitement : elle appelle `/score` avec la clé que l'utilisateur saisit (gardée dans son onglet, jamais écrite dans la page ni dans les logs). Les polices sont servies par l'API : aucune adresse IP de visiteur n'est envoyée à un service tiers |
| **Limites connues**         | Le modèle peut se tromper (faux positifs, voir étape 3) ; l'anonymisation peut laisser passer des noms rares                                                                                                                                                                         |

### 7. Grille de tests de sécurité (livrable)

Les tests « auto » sont dans `tests/test_api.py` (`python -m pytest tests/test_api.py`). Les tests « cloud » se font avec `curl` sur le cluster GKE, depuis le dossier où se trouve `tls.crt`.

| Test                                  | Commande / méthode                                                             | Attendu       | Auto | Cloud                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------- | ------------- | ---- | ----------------------------------------------------------------------------- |
| Sans clé API                         | requête sans `X-API-Key`                                                     | 401           | ✅   | ✅ (en HTTP, avant l'Ingress)                                                 |
| Mauvaise clé                         | en-tête `X-API-Key: mauvaise-cle`                                            | 401           | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| `/metrics` sans clé                | `curl https://$IP/metrics`                                                    | 401           | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| Texte vide                            | `-d '{"text":""}'`                                                            | 422           | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| Texte de plus de 5 000 caractères    | texte généré                                                                 | 422           | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| Modèle inconnu                       | `-d '{"text":"hello","model":"gpt"}'`                                         | 422           | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| Requête de plus de 32 Ko             | corps de 40 Ko                                                                  | 413           | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| Trop de requêtes                     | plus de `RATE_LIMIT_PER_MINUTE` requêtes en une minute                       | 429           | ✅   | ✅ 5 566 réponses 429 (étape 5)                                             |
| En-têtes de sécurité               | `curl -I https://$IP/health`                                                  | présents     | ✅   | ✅ (rapport d'attaque, 12/11)                                                 |
| Pas de texte ni de clé dans les logs | lecture des logs après un appel                                                | absents       | ✅   | à compléter                                                                 |
| HTTPS avec certificat vérifié       | `curl --cacert tls.crt https://$IP/health`                                    | 200           | –   | ✅ certificat Google vérifié sur `https://34-54-213-190.sslip.io` (12/11) |
| HTTP refusé                          | `curl -m 5 http://$IP/health`                                                 | échec        | –   | ✅ (rapport d'attaque, 12/11)                                                 |
| TLS 1.1 refusé                       | `openssl s_client -connect $IP:443 -tls1_1`                                   | échec        | –   | ✅ refusé (12/11)                                                            |
| Secrets chiffrés par KMS             | `gcloud container clusters describe ... --format="value(databaseEncryption)"` | `ENCRYPTED` | –   | ❌ `DECRYPTED` (12/11) : étape 8 de la partie 4 à appliquer               |

La phase `grille` de `loadtest/attack.py` (étape 6) remplit la colonne « Cloud » en une commande : `python3 loadtest/attack.py --host https://$IP --cacert tls.crt --phases grille`. Commandes équivalentes avec `curl` (afficher seulement le code HTTP) :

```bash
httpcode() { curl -s -o /dev/null -w "%{http_code}\n" --cacert tls.crt "$@"; }
URL=https://$IP
httpcode -X POST $URL/score -H "Content-Type: application/json" -d '{"text":"hello"}'                                  # 401
httpcode -X POST $URL/score -H "X-API-Key: mauvaise-cle" -H "Content-Type: application/json" -d '{"text":"hello"}'     # 401
httpcode $URL/metrics                                                                                                  # 401
httpcode -X POST $URL/score -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{"text":""}'               # 422
httpcode -X POST $URL/score -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{"text":"hello","model":"gpt"}'  # 422
python3 -c "print('{\"text\":\"' + 'a'*40000 + '\"}')" > big.json
httpcode -X POST $URL/score -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" --data-binary @big.json      # 413
curl -sI --cacert tls.crt $URL/health | grep -iE "x-content-type|x-frame|strict-transport|cache-control"
gcloud logging read 'jsonPayload.event="auth_failed"' --limit 5 --format=json                                      # journal d'audit
```

### 8. Page web et accès à la supervision (lien à partager)

L'API sert une page web sur **https://34-54-213-190.sslip.io/** : champ de texte, bouton « Analyser », score de 0 à 100 avec une jauge, thèmes clair et sombre. Une personne qui a **le lien et la clé API** peut l'utiliser sans rien installer.

- **Authentification** : la page demande la clé API et l'envoie en `X-API-Key` à `POST /score`, comme n'importe quel client. La clé reste dans l'onglet du navigateur (`sessionStorage`) et n'est écrite ni dans la page, ni dans le code, ni dans les logs. Sans clé valide, l'API répond 401 et la page affiche « Clé API invalide ». La limite de 600 requêtes par minute s'applique à cette clé.
- **Pas d'accès libre** : une première version de la page appelait une route sans clé (`/demo/score`). Elle a été supprimée : tout passe par `/score`, protégé par la clé.
- **Sécurité de la page** : HTTPS uniquement, `Content-Security-Policy` stricte (`default-src 'none'`, aucune ressource externe), en-têtes de sécurité. Les polices (Literata, Instrument Sans, licence OFL) sont **hébergées par l'API** (`/fonts/`) : charger Google Fonts aurait envoyé l'adresse IP de chaque visiteur à Google.
- **Bouton « Monitoring »** : ouvre Grafana sur `/grafana/`. Grafana est exposé par le même Ingress, avec une **page de connexion**. On y entre avec le compte **`lecteur`** (rôle Viewer : voir les tableaux de bord, rien modifier), dont le mot de passe est donné avec la clé API : `gcloud secrets versions access latest --secret=grafana-viewer-password`. Le compte `admin` n'est jamais partagé. Le tableau de bord montre le trafic, la latence, les erreurs, les pods qui répondent et le CPU.
- **Certificat HTTPS** : certificat **géré par Google** (`k8s/managed-cert.yaml`) pour le nom `34-54-213-190.sslip.io`, qui pointe vers l'IP fixe : plus d'avertissement du navigateur. Le certificat auto-signé reste présenté en secours tant que celui de Google n'est pas actif.
- **Éteindre la page** sans toucher au code : `kubectl set env deployment/dss-api DEMO_ENABLED=0`. Couper l'accès à Grafana : `kubectl scale deployment/grafana --replicas=0`.
- **Limites** : Grafana est joignable depuis internet : seuls la page de connexion, un mot de passe aléatoire et le blocage après échecs de connexion de Grafana le protègent (pas de limite globale de requêtes : Cloud Armor, voir partie 9). La base de Grafana est perdue à chaque redémarrage de son pod : relancer `deploy/monitoring.sh`, qui recrée la source de données et le compte lecteur. Le compteur de requêtes est par pod.

Mise en ligne : reconstruire l'image (`gcloud builds submit`), mettre à jour le déploiement (étape 13 de la partie 4), puis `kubectl apply -f k8s/managed-cert.yaml -f k8s/ingress.yaml -f k8s/monitoring/grafana.yaml` et `deploy/monitoring.sh`.

### 9. Limites et améliorations

- **Nom de domaine de démonstration** : le certificat Google est émis pour `34-54-213-190.sslip.io`, un nom public gratuit qui pointe vers l'IP fixe. Pour un service durable, il faut un nom de domaine propre, car ce nom dépend du service sslip.io. Le certificat auto-signé du Secret `dss-tls` n'est plus qu'un secours (il expire après 1 an).
- **Limite de requêtes par pod** : le compteur est en mémoire. Avec 10 pods, un client peut faire jusqu'à 10 × 600 requêtes par minute (mesuré à l'étape 5 : environ 6 300 requêtes acceptées par minute avec une seule clé). Pour une limite globale : **Cloud Armor** (WAF), branché sur l'Ingress par le `BackendConfig` (`securityPolicy`), qui ajoute aussi le filtrage d'IP et la protection contre le déni de service.
- **Clés API sans expiration** : une rotation régulière (partie 1) est nécessaire. Pour des jetons à durée de vie courte, passer à OAuth2 / JWT.
- **Attaques adverses** : un texte volontairement déformé (« id1ot », espaces entre les lettres) peut tromper le LSTM. Ce point sera traité avec les attaques sur l'IA (cours n°6).

## Étape 5 : simulation de montée en charge

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* expérimenter la différence entre un prototype et un service scalable ; utiliser Apache Bench, Locust ou k6 pour tester la charge.
>
> *Exercice :*
>
> 1. Simuler croissance progressive, stress test et failover.
> 2. Mesurer latence moyenne, P95/P99, taux d'erreur.
> 3. Proposer des améliorations (cache, load balancer, redondance).

| Question du TP                                                 | Réponse courte                                                                                                           | Partie |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------ |
| Prototype ou service scalable ?                                | le même code passe de 2 req/s (1 utilisateur) à **195 req/s** (10 pods) avec 0,03 % d'erreurs                     | 1 et 5 |
| Quel outil ?                                                   | **Locust**, scénarios écrits en Python                                                                            | 2      |
| Simuler croissance progressive, stress test et failover        | 3 scénarios joués sur GKE le 12/11/2026, plus un test de la limite de requêtes                                         | 3      |
| Mesurer latence moyenne, P95/P99, taux d'erreur                | 106 / 210 / 390 ms à vide ; 673 ms / 2,6 s / 4,7 s en stress ; 0,01 à 0,03 % d'erreurs, 4 % quand tous les pods tombent | 4      |
| Proposer des améliorations (cache, load balancer, redondance) | load balancer déjà en place ; redondance renforcée ; cache proposé                                                    | 6      |
| Modifications appliquées                                      | HPA 3 à 15 pods à 50 % de CPU, sondes plus tolérantes, pods répartis par zone, `PodDisruptionBudget`                | 7      |

Code : `loadtest/` (scénarios Locust, script qui enchaîne les tests sur le cluster, calcul des tableaux). Résultats bruts : `reports/loadtest/` (CSV et rapports HTML de Locust, relevé de l'autoscaling, événements, métriques Cloud Monitoring). Tests faits le 12 novembre 2026, de 11 h 35 à 12 h 00, sur l'API déployée dans GKE.

### 1. Prototype ou service scalable : point de départ

Avant cette étape, l'API est déployée et sécurisée (étapes 3 et 4), mais elle n'a jamais reçu plus de quelques requêtes à la fois. On ne sait pas combien d'utilisateurs elle supporte, comment sa latence évolue, si l'autoscaling réagit à temps, ni si elle survit à une panne.

Le **code** de l'API est le même dans les deux cas ; c'est l'**infrastructure** qui fait la différence :

|                   | Prototype (`uvicorn` sur un poste)                 | Service sur GKE                                                              |
| ----------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------- |
| Instances         | 1 processus (`--workers 1` dans le `Dockerfile`) | 2 à 10 pods au moment du test (3 à 15 depuis la partie 7)                  |
| Répartition      | aucune                                               | load balancer Google (Ingress) + Service interne                             |
| Montée en charge | aucune : la latence augmente jusqu'à saturation     | HPA : ajoute des pods quand le CPU dépasse le seuil                         |
| Panne             | le service s'arrête                                 | les sondes de santé retirent et redémarrent le pod ; les autres répondent |
| Mise à jour      | coupure                                              | progressive (`RollingUpdate`, `maxUnavailable: 0`)                       |

**Architecture testée.**

```
Locust (Mac) ──HTTPS──▶ Load balancer Google (Ingress, IP fixe, TLS 1.2+)
                              │ répartit les requêtes entre les pods prêts
                              ▼
               Service interne (ClusterIP) ──▶ pods dss-api (2 à 10, ajoutés par le HPA)
                                               sur des nœuds Autopilot ek-standard-8
```

Configuration **au moment du test** (les valeurs modifiées depuis sont dans la partie 7) :

| Niveau                       | Configuration (relevée sur le cluster)                                                                                                                                                              | Pourquoi ce choix                                                                                                                |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Cluster**            | GKE **Autopilot** `dss-cluster`, Kubernetes 1.36, canal de mises à jour « Regular », région `europe-west9` (Paris), nœuds possibles dans les 3 zones a, b et c                        | Google crée et gère les machines ; on ne paie que les ressources demandées par les pods ; données en France                  |
| **Nœuds**             | machines **`ek-standard-8`** : 8 vCPU et environ 28 Go utilisables chacune, créées et supprimées automatiquement par Autopilot selon les pods à placer. 2 nœuds au repos (zones a et b) | aucun choix de notre part : Autopilot choisit la machine                                                                         |
| **Pods**               | **2 au minimum, 10 au maximum** ; 1 conteneur, 1 processus uvicorn                                                                                                                             | 2 pods : le service survit à la perte d'un pod ; 10 : plafond de coût                                                          |
| **Ressources par pod** | demande : **500 m de CPU** (un demi-cœur) et **1,5 Go** de mémoire ; limite : 2 Go de mémoire, **pas de limite de CPU**                                                         | sans limite de CPU, un pod peut dépasser sa demande s'il reste du CPU libre sur le nœud : d'où des mesures au-dessus de 100 % |
| **Autoscaling (HPA)**  | ajoute des pods quand le CPU moyen dépasse **70 %** de la demande                                                                                                                             | marge de 30 % pour absorber la charge pendant l'arrivée des nouveaux pods                                                       |
| **Sondes de santé**   | `/health` toutes les 5 s au démarrage (jusqu'à 2 min), toutes les 10 s pour la disponibilité, toutes les 20 s pour la vie ; **1 s** pour répondre ; 3 échecs consécutifs               | un pod n'a du trafic que s'il répond ; un pod qui ne répond plus est redémarré                                               |
| **Mises à jour**      | une par une (`maxSurge: 1`, `maxUnavailable: 0`)                                                                                                                                                 | jamais moins de pods prêts pendant une mise à jour                                                                             |
| **Protection**         | 600 requêtes par minute et par clé, **comptées sur chaque pod** ; levée pendant les tests de capacité, remise à la fin                                                                   | étape 4                                                                                                                         |

**Requêtes de test** : `POST /score` en HTTPS avec la clé API, 10 textes toxiques ou neutres tirés au hasard ; chaque utilisateur simulé attend 0,2 à 0,6 s entre deux requêtes. Elles partent d'un Mac, par internet : les latences comprennent ce trajet, en plus des 19 à 27 ms de calcul de l'API (anonymisation spaCy puis LSTM).

### 2. Utiliser Apache Bench, Locust ou k6 : choix de Locust

| Critère                         | **Locust**                           | Apache Bench (`ab`)                  | k6                        |
| -------------------------------- | ------------------------------------------ | -------------------------------------- | ------------------------- |
| Écriture du scénario           | **Python**, comme le reste du projet | aucune : une seule requête répétée | JavaScript                |
| Montée progressive par paliers  | **oui**                              | non, charge fixe                       | oui                       |
| P95 / P99 seconde par seconde    | **oui** (CSV)                        | seulement un total en fin de test      | oui, export à configurer |
| Suivre une panne pendant le test | oui                                        | non                                    | oui                       |
| Rapport                          | **HTML + CSV**                       | texte                                  | texte ou JSON             |

`ab` ne permet ni paliers ni suivi d'une panne dans le temps. k6 ferait aussi bien que Locust, mais dans un autre langage. Locust suffit pour nos volumes (quelques centaines de requêtes par seconde) et s'écrit en Python, à côté du code de l'API.

**Comment nos tests sont écrits.**

- `loadtest/locustfile.py` : **un seul fichier pour tous les scénarios**, choisi par la variable `SCENARIO`. Le dictionnaire `STAGES` décrit chaque scénario en paliers `(fin du palier en s, utilisateurs, utilisateurs ajoutés par seconde)`, et la classe `Shape(LoadTestShape)` les applique seconde par seconde : pas besoin de `--users` ni de `--run-time`.
- La classe `ApiUser` simule un utilisateur : `wait_time = between(0.2, 0.6)` (il lit le résultat avant d'envoyer le texte suivant), sauf pour `ratelimit` où `constant(0.05)` enchaîne les requêtes sans pause.
- Chaque texte reçoit un **suffixe aléatoire** (`pick()`) : le même texte n'est jamais rejoué, ce qui évite qu'un éventuel cache fausse la mesure.
- `catch_response=True` : **toute réponse autre que 200** (401, 429, 502, délai dépassé) est comptée comme une erreur.
- `loadtest/run_gke.sh` enchaîne les scénarios contre le cluster. Pendant tout le test, une boucle en arrière-plan lit le HPA toutes les 5 s (`kubectl get hpa`) et écrit le nombre de pods et le CPU dans `gke_hpa.csv` ; chaque action (début d'un scénario, suppression d'un pod) est horodatée dans `gke_events.csv`. La fonction `set_limit` change `RATE_LIMIT_PER_MINUTE` et attend que les pods redémarrés soient prêts.
- `loadtest/plot.py` lit les CSV de Locust et calcule les tableaux de la partie 4 (médiane par palier, hors 15 premières secondes de montée).

### 3. Simuler croissance progressive, stress test et failover

| Étape                              | Heure              | Charge envoyée                                                                                      | Question posée                            |
| ----------------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| 0. Préparation                     | 11 h 35            | limite de requêtes levée (`RATE_LIMIT_PER_MINUTE=0`)                                             | mesurer la capacité, pas la protection    |
| 1. Référence                      | 11 h 36            | 1 utilisateur, 1 min                                                                                 | quelle est la latence à vide ?            |
| 2. **Croissance progressive** | 11 h 37 → 11 h 44 | 5 → 20 → 50 → 100 utilisateurs                                                                    | l'autoscaling suit-il une hausse normale ? |
| 3. **Stress test**            | 11 h 44 → 11 h 50 | 20 → 60 → 120 → 200 → 300 utilisateurs                                                           | où est le point de rupture ?              |
| 4. **Failover**               | 11 h 50 → 11 h 54 | 40 utilisateurs constants ; **1 pod supprimé** à 11 h 51, **tous les pods** à 11 h 52 | le service survit-il à une panne ?        |
| 5. Limite de requêtes              | 11 h 56 → 12 h 00 | limite remise à 600 ; une seule clé, 5 puis 20 utilisateurs sans pause                             | la protection anti-abus répond-elle 429 ? |

Relancer le test (environ 30 minutes, jusqu'à 15 pods facturés) :

```bash
pip install -r requirements-loadtest.txt
gcloud container clusters get-credentials dss-cluster --region europe-west9
make loadtest                                      # = loadtest/run_gke.sh ; remet la limite à 600 à la fin
python loadtest/plot.py                             # tableaux de résultats (et graphes Locust)
```

**Ce que montre Cloud Monitoring.**

![Requêtes reçues par les pods, vues par Cloud Monitoring](reports/images/cloud_monitoring_backend_request_count.png)

*Source : **Google Cloud Monitoring**, Metrics Explorer, métrique `backend_request_count` (ressource « Cloud Load Balancing Backend Service ») : requêtes transmises par le load balancer aux pods `dss-api`, en requêtes par seconde, moyenne par minute, heure de Paris.*

Ce graphique vient de Google et non de Locust : il confirme nos mesures avec une source indépendante. On y lit **trois phases de charge** :

| Phase                                | Heure              | Ce qu'on voit                                                                                                | Comportement des pods                                                                                     |
| ------------------------------------ | ------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| **1. Croissance progressive**  | 11 h 36 → 11 h 44 | le débit monte par étapes jusqu'à environ **130 req/s**                                             | le HPA passe de 2 à 7 pods ; il en demande 10, mais 3 attendent qu'Autopilot crée de nouvelles machines |
| **2. Stress**                  | 11 h 45 → 11 h 50 | pic à environ **185 req/s**, puis **plateau** alors que Locust passe de 120 à 300 utilisateurs | les 10 pods sont là, mais tous saturés : le débit ne peut plus monter, les requêtes attendent         |
| **3. Rafale d'un seul client** | 11 h 57 → 12 h 00 | remontée brutale à environ **155 req/s** avec une seule clé API                                     | les 10 pods suffisent largement, mais chacun refuse (429) ce qui dépasse 600 requêtes par minute        |

Le creux de 11 h 45 est le passage au stress, qui recommence à 20 utilisateurs. La descente entre les phases 2 et 3 correspond au failover (environ 80 req/s) puis au redémarrage des pods pour remettre la limite. La coupure de 13 secondes du failover ne se voit pas : Cloud Monitoring fait la moyenne par minute. Pour la même raison, ses valeurs sont un peu plus basses que celles de Locust (185 contre 195 req/s au plateau).

Ces trois phases correspondent aux trois façons dont la charge arrive en production : une hausse normale (heures de pointe), une surcharge exceptionnelle et une rafale d'un seul client (robot, abus, tentative de copie du modèle).

**Failover, en détail.** Pendant 4 minutes, 40 utilisateurs envoient des requêtes en continu, et `run_gke.sh` provoque deux pannes avec `kubectl delete pod` :

| Panne                                        | Ce qui se passe                                                                                             | Effet mesuré par Locust                                                         |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **1 pod supprimé** (11 h 51)          | le load balancer cesse d'envoyer du trafic à ce pod ; Kubernetes en recrée un, prêt en 16 s              | **0 erreur** : les autres pods absorbent la charge                         |
| **Tous les pods supprimés** (11 h 52) | plus aucun pod prêt : le load balancer répond 502 ; les pods recréés doivent recharger spaCy et le LSTM | **13 s de coupure** (751 erreurs 502), puis les 10 pods reviennent en 40 s |

### 4. Mesurer latence moyenne, P95/P99, taux d'erreur

Format des diapos 17 et 18 du cours n°3. Latences mesurées par Locust côté client. CPU relevé par le HPA, en % du CPU **demandé** : au-dessus de 100 %, le pod utilise du CPU libre du nœud. Mémoire lue dans Cloud Monitoring, en % de la mémoire demandée. Le « débit max » est le meilleur débit sur une fenêtre de quelques secondes.

| Scénario               | Utilisateurs simultanés | Requêtes | Débit max (req/s) | Latence moyenne | P95    | P99    | Erreurs                                 | CPU moyen (max) | Mémoire | Pods prêts           | Observations                                           |
| ----------------------- | ------------------------ | --------- | ------------------ | --------------- | ------ | ------ | --------------------------------------- | --------------- | -------- | --------------------- | ------------------------------------------------------ |
| Référence             | 1                        | 116       | 2                  | 106 ms          | 210 ms | 390 ms | 0 %                                     | 5 %             | 11 %     | 2                     | latence de base, trajet internet compris               |
| Croissance progressive  | 5 → 100                 | 35 279    | 153                | 269 ms          | 990 ms | 1,6 s  | **0,01 %** (4 × 502)             | 76 % (113 %)    | 12 %     | 2 → 7 (10 demandés) | 3 pods attendent 4 min leurs machines                  |
| Stress                  | 20 → 300                | 54 508    | **201**      | 673 ms          | 2,6 s  | 4,7 s  | **0,03 %** (15 × 502)            | 96 % (123 %)    | 11 %     | 7 → 10               | plateau à environ 195 req/s, CPU saturé              |
| Failover                | 40                       | 18 678    | 87                 | 105 ms          | 200 ms | 660 ms | **4,0 %** (751 × 502, 1 coupure) | 59 %            | 12 %     | 10 → 1 → 10         | 1 pod perdu : 0 erreur ; tous perdus : 13 s de coupure |
| Limite, 5 utilisateurs  | 5                        | 4 158     | 47                 | 56 ms           | 120 ms | 140 ms | 0 %                                     | faible          | 9 %      | 10                    | aucun 429 : chaque pod ne voit que 280 req/min         |
| Limite, 20 utilisateurs | 20                       | 15 057    | 167                | 69 ms           | 150 ms | 210 ms | 37 % de **429 attendus** (5 566)  | faible          | 9 %      | 10                    | environ 6 300 req/min acceptées = 10 pods × 600      |

Stress test palier par palier (médiane de chaque palier, hors 15 premières secondes de montée) :

| Utilisateurs | Débit              | Médiane | P95    | P99    | Pods prêts | CPU moyen |
| ------------ | ------------------- | -------- | ------ | ------ | ----------- | --------- |
| 20           | 43 req/s            | 45 ms    | 130 ms | 160 ms | 7           | 63 %      |
| 60           | 113 req/s           | 72 ms    | 200 ms | 460 ms | 7 → 10     | 63 %      |
| 120          | **193 req/s** | 170 ms   | 550 ms | 840 ms | 10          | 99 %      |
| 200          | 192 req/s           | 490 ms   | 1,7 s  | 2,2 s  | 10          | 116 %     |
| 300          | 188 req/s           | 600 ms   | 3,6 s  | 5,6 s  | 8 → 10     | 119 %     |

**D'où vient le plafond d'environ 195 req/s ?** Ce n'est pas un réglage : c'est la conséquence de deux paramètres. Le HPA ne dépassait pas **10 pods** (`maxReplicas`), et un pod traite environ **20 requêtes par seconde** : chaque requête coûte quelques dizaines de millisecondes de calcul (spaCy puis LSTM) et un pod n'a guère plus d'un demi-cœur. 10 × 20 ≈ 195. Changer l'un des deux paramètres déplace le plafond (voir la partie 7).

### 5. Analyse : ce que les mesures disent du service

| Question du TP                      | Réponse                                                                                                                                                                                                                                                               |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Croissance progressive**    | Le service suit la charge : le HPA demande un pod environ 50 s après le dépassement du seuil, et il y a 4 erreurs sur 35 279 requêtes. Mais le P95 passe de 150 ms à 1,1 s : les pods arrivent toujours après la charge, et les 3 derniers ont attendu 4 minutes. |
| **Stress test**               | Capacité maximale d'environ **195 req/s avec 10 pods**. Au-delà de 120 utilisateurs, le débit plafonne et la latence est multipliée par 6. Le service ralentit mais ne s'effondre pas : 99,97 % des requêtes réussissent à 300 utilisateurs.              |
| **Failover**                  | Perdre **un** pod n'a aucun effet visible (0 erreur, remplacé en 16 s). Perdre **tous** les pods coupe le service **13 secondes**, puis les 10 pods reviennent en 40 s.                                                                             |
| **Latence moyenne, P95, P99** | 106 / 210 / 390 ms à vide ; 269 ms / 990 ms / 1,6 s en montée ; 673 ms / 2,6 s / 4,7 s en stress.                                                                                                                                                                    |
| **Taux d'erreur**             | 0,01 % en montée, 0,03 % en stress, 4 % quand tous les pods tombent. Toutes ces erreurs sont des 502 dues à des pods arrêtés.                                                                                                                                      |
| **Protection**                | La limite fonctionne (5 566 refus 429), mais elle compte **par pod** : avec 10 pods, une seule clé obtient environ 6 300 requêtes par minute au lieu de 600.                                                                                                   |
| **Ressources**                | Le **CPU** est le seul goulot. La mémoire reste à 12 % : environ 200 Mo utilisés sur 1,5 Go réservés.                                                                                                                                                       |

**Prototype ou service scalable ?** Un prototype (un seul processus) aurait plafonné à environ 20 req/s et se serait arrêté à la première panne. Avec plusieurs pods derrière un load balancer, des sondes de santé et l'autoscaling, le même code passe à 195 requêtes par seconde avec très peu d'erreurs, et la perte d'un pod passe inaperçue. Le point faible est la **réactivité** : quand il faut de nouvelles machines, la capacité arrive plusieurs minutes après la charge. Ce sont les réglages qui limitent le service, pas l'architecture.

**Limites du système constatées.**

| Limite                                | Preuve mesurée                                                                                                                                            | Conséquence                                                                                                                                                                                                     |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Attente des machines**        | à 11 h 41, le HPA demande 10 pods ; 3 restent en attente jusqu'à 11 h 45. Kubernetes : `Pod didn't trigger scale-up: in backoff after failed scale-up` | Autopilot doit créer un nœud, et sa première tentative a échoué (cause non identifiée). Pendant 4 minutes, 7 pods saturés font le travail de 10                                                           |
| **Capacité plafonnée**        | débit bloqué à environ 195 req/s, HPA déjà au maximum                                                                                                 | au-delà de 120 utilisateurs, P95 de 3,6 s à 300 utilisateurs                                                                                                                                                   |
| **Sonde de vie trop stricte**   | `Liveness probe failed: context deadline exceeded` pendant le stress                                                                                     | un pod saturé met plus d'1 s à répondre à `/health` ; après 3 échecs, Kubernetes le croit bloqué et le **redémarre**, ce qui coupe ses requêtes en cours : ce sont les 15 erreurs 502 du stress |
| **Pods regroupés**             | au repos, les 2 pods tournent sur **le même nœud** (avant comme après le test)                                                                    | une panne de cette machine coupe tout le service, comme la suppression de tous les pods (13 s d'erreurs)                                                                                                         |
| **Limite de requêtes par pod** | 0 refus avec 5 utilisateurs ; environ 6 300 req/min acceptées avec 20                                                                                     | la protection anti-abus s'élargit quand le cluster grandit                                                                                                                                                      |
| **Mémoire surdimensionnée**   | 9 à 13 % de la mémoire demandée                                                                                                                         | Autopilot facture la mémoire **demandée** : environ 1,3 Go payés pour rien par pod                                                                                                                      |

**Limites de la mesure.** Les requêtes partaient d'un Mac, par internet : les latences absolues sont un peu surestimées. Chaque test a duré 4 à 7 minutes : la stabilité sur 30 minutes (charge soutenue du cours) n'est pas vérifiée. Seule la suppression de pods a été testée, pas la panne d'une machine ou d'une zone.

### 6. Proposer des améliorations (cache, load balancer, redondance)

**Les trois pistes citées par le TP.**

| Piste du TP             | Où en est le projet                                                                                                                                                                                                                                                                                                                                | Ce que nous proposons                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Load balancer** | **en place depuis l'étape 3** : le load balancer HTTPS de Google (Ingress) répartit les requêtes entre les pods par le Service interne. Il n'envoie du trafic qu'aux pods qui répondent à `/health` (sonde de disponibilité, et health check du `BackendConfig`) : c'est grâce à lui que la perte d'un pod n'a fait aucune erreur | ajouter **Cloud Armor** devant (limite de requêtes globale, filtrage d'IP), branché par le `BackendConfig`                                                                                                                                                                                                                                                                                                                                                                              |
| **Redondance**    | au moment du test : 2 pods, **sur le même nœud**                                                                                                                                                                                                                                                                                            | au moins 3 pods, répartis sur plusieurs zones, et protégés pendant les maintenances : **appliqué** (partie 7)                                                                                                                                                                                                                                                                                                                                                                           |
| **Cache**         | aucun                                                                                                                                                                                                                                                                                                                                               | garder le score des textes déjà vus, indexé par l'identifiant HMAC du texte (déjà calculé pour les logs par `pseudonym()` dans `src/api/main.py`), **jamais le texte lui-même** ; durée courte. Utile contre le spam (même texte envoyé des milliers de fois). Un cache en mémoire suffit pour un pod ; Memorystore (Redis) pour un cache partagé entre pods. **Non mis en place** : nos tests utilisent des textes tous différents, le gain ne serait pas mesurable |

**Toutes les pistes, classées de la plus utile à la moins utile.**

| Limite                            | Solution                                                                                                                                               | Effet attendu                                                                                | Statut                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- | --------------------------------------- |
| Sonde de vie trop stricte         | `timeoutSeconds: 5` et `failureThreshold: 5` sur la `livenessProbe` : elle doit détecter un pod **bloqué**, pas un pod **occupé** | plus de redémarrages en pleine charge, donc plus d'erreurs 502 au stress                    | **appliqué**                     |
| Attente des machines              | **capacité d'avance**, voir ci-dessous                                                                                                          | les pods démarrent tout de suite pendant un pic                                             | en partie (seuil plus bas)              |
| Réaction tardive du HPA          | seuil à **50 %** au lieu de 70 %, `minReplicas: 3`                                                                                            | le cluster grandit avant d'être saturé                                                     | **appliqué**                     |
| Capacité plafonnée              | `maxReplicas: 15` dans `k8s/hpa.yaml`                                                                                                              | environ 290 req/s (15 pods × 20 req/s), à confirmer par un nouveau test                    | **appliqué**                     |
| Pods regroupés                   | `topologySpreadConstraints` par zone, et un `PodDisruptionBudget`                                                                                  | une panne de machine ou de zone, ou une maintenance de Google, ne coupe plus tout le service | **appliqué**                     |
| Limite de requêtes par pod       | **Cloud Armor** devant l'Ingress : limite globale par IP ou par clé                                                                             | une vraie limite de 600 requêtes par minute, quel que soit le nombre de pods                | non retenu (pour l'instant)             |
| Mémoire surdimensionnée         | demander **768 Mo** au lieu de 1,5 Go (marge pour le chargement de spaCy)                                                                        | facture Autopilot plus basse                                                                 | non retenu (économie, pas résilience) |
| Textes répétés (spam, attaque) | **cache** du score par identifiant HMAC du texte                                                                                                 | réponse immédiate pour un texte déjà vu, sans donnée personnelle stockée               | non retenu (gain non mesurable ici)     |

**Éviter l'attente des machines.** Le HPA réagit en moins d'une minute ; ce qui coûte du temps, c'est la création d'un nœud quand il n'y a plus de place. Trois moyens d'avoir la capacité **avant** le pic :

| Moyen                                         | Principe                                                                                                                                                                                                                                                                                                                     | Coût                                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| **Pods de réserve** (*balloon pods*) | un petit déploiement de pods fictifs, de priorité basse (`PriorityClass`), qui occupent de la place sur des nœuds déjà créés. Quand l'API a besoin de pods, Kubernetes expulse les pods fictifs et place l'API **immédiatement** ; les pods fictifs attendent ensuite un nouveau nœud, sans gêner personne | la réserve est payée en permanence                              |
| **Minimum planifié**                   | une tâche planifiée (`CronJob`) monte `minReplicas` (par exemple de 3 à 6) avant les heures chargées connues, puis le redescend                                                                                                                                                                                      | payé seulement pendant ces heures ; inutile pour un pic imprévu |
| **Seuil plus bas**                      | HPA à 50 % : le cluster grandit plus tôt, ce qui laisse aux nœuds le temps d'arriver                                                                                                                                                                                                                                      | quelques pods de plus en moyenne                                  |

Nous avons retenu le seuil plus bas (partie 7) : il ne coûte que quelques pods de plus, sans réserve payée en permanence.

### 7. Modifications retenues pour améliorer la résilience de l'API

Parmi les pistes de la partie 6, nous avons appliqué celles qui rendent le service plus **résilient** (il continue de répondre malgré la charge ou une panne) **sans toucher au code Python** : ce sont des réglages Kubernetes, dans `k8s/hpa.yaml`, `k8s/deployment.yaml` et un nouveau fichier `k8s/pdb.yaml`. Elles sont **appliquées au cluster depuis le 12/11/2026** (image `v6`), mais **pas encore re-mesurées** par un nouveau test de charge.

| Constat de la partie 5                                                                          | Modification (avant → après)                                                                                        | Fichier                    | Pourquoi                                                                          | Effet attendu                                                                             |
| ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | -------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| P95 de 1,1 s pendant la croissance : les pods (et parfois les nœuds) arrivent après la charge | seuil CPU du HPA **70 % → 50 %**                                                                               | `k8s/hpa.yaml`           | le HPA demande des pods plus tôt, il reste de la marge pendant qu'ils démarrent | moins de saturation pendant une hausse                                                    |
| Peu de marge au repos ; 1 pod perdu = 1 seul restant                                            | `minReplicas` **2 → 3**                                                                                      | `k8s/hpa.yaml`           | après une panne, il reste 2 pods pour absorber la charge                         | perte d'un pod toujours invisible, même sous charge ; un pod de plus payé en permanence |
| Plafond à environ 195 req/s avec 10 pods                                                       | `maxReplicas` **10 → 15**                                                                                    | `k8s/hpa.yaml`           | le plafond est le produit « pods × 20 req/s »                                  | environ 290 req/s, à confirmer par un nouveau stress test                                |
| Pods saturés redémarrés par la sonde de vie (15 erreurs 502)                                 | sonde de vie : délai **1 s → 5 s**, **3 → 5 échecs** (environ 100 s sans réponse avant redémarrage) | `k8s/deployment.yaml`    | un pod occupé n'est plus pris pour un pod bloqué                                | plus de 502 dus aux redémarrages pendant un pic                                          |
| (même cause)                                                                                   | sonde de disponibilité : délai **1 s → 3 s**                                                                 | `k8s/deployment.yaml`    | un pod chargé n'est plus retiré du load balancer pour une réponse un peu lente | moins de va-et-vient des pods dans le load balancer                                       |
| Les 2 pods sur le même nœud                                                                   | répartition sur les zones a, b et c (`topologySpreadConstraints`, règle souple)                                   | `k8s/deployment.yaml`    | une machine ou une zone en panne n'emporte plus tous les pods                     | constaté après application : 3 pods sur **2 zones** (a et b)                      |
| Maintenance de Google : rien n'empêche d'arrêter tous les pods                                | `PodDisruptionBudget` : au moins **2 pods prêts**                                                            | `k8s/pdb.yaml` (nouveau) | Kubernetes doit attendre qu'un pod soit remplacé avant d'en arrêter un autre    | pas de coupure pendant une mise à jour des nœuds                                        |

**Ce qui a changé dans les fichiers.**

```yaml
# k8s/hpa.yaml
  minReplicas: 3            # avant : 2
  maxReplicas: 15           # avant : 10
  ...
          averageUtilization: 50   # avant : 70
```

```yaml
# k8s/deployment.yaml (extraits)
      topologySpreadConstraints:          # nouveau : un pod par zone autant que possible
        - maxSkew: 1                      # au plus 1 pod d'écart entre deux zones
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: ScheduleAnyway   # règle souple : ne bloque jamais le démarrage d'un pod
          labelSelector:
            matchLabels:
              app: dss-api
      ...
          readinessProbe:
            timeoutSeconds: 3      # avant : 1 (valeur par défaut)
          livenessProbe:
            timeoutSeconds: 5      # avant : 1
            failureThreshold: 5    # avant : 3
```

```yaml
# k8s/pdb.yaml (nouveau)
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: dss-api
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: dss-api
```

**Pourquoi une règle de répartition souple (`ScheduleAnyway`) ?** Avec une règle stricte (`DoNotSchedule`), un pod qui ne trouve pas de place dans la bonne zone reste en attente, le temps qu'Autopilot crée un nœud dans cette zone : on retrouverait l'attente de 4 minutes de la partie 5, en plein pic. La règle souple préfère répartir, mais place quand même le pod s'il n'y a pas d'autre choix. C'est pourquoi les 3 pods sont aujourd'hui sur 2 zones : il n'y a pas de nœud en zone c.

**Limites du `PodDisruptionBudget`.** Il protège des arrêts **prévus** (mise à jour ou réparation d'un nœud par Google), pas d'une panne brutale : contre celle-ci, c'est la répartition par zone qui joue.

**Commandes utilisées (sans coupure) et vérification :**

```bash
sed "s|IMAGE|$REGION-docker.pkg.dev/$PROJECT/dss/api:v6|" k8s/deployment.yaml | kubectl apply -f -
kubectl apply -f k8s/hpa.yaml -f k8s/pdb.yaml
kubectl rollout status deployment/dss-api

kubectl get hpa dss-api                      # MINPODS 3, MAXPODS 15, cible 50 %
kubectl get pdb dss-api                      # MIN AVAILABLE 2
kubectl get pods -l app=dss-api -o wide      # nœud de chaque pod
kubectl get nodes -L topology.kubernetes.io/zone   # zone de chaque nœud
```

**Reste à faire.**

- **Re-mesurer** : relancer `loadtest/run_gke.sh` depuis une machine Google Cloud de la même région (Cloud Shell ou une VM), pour comparer avant et après sans le trajet internet. Ajouter un test de panne de nœud (`kubectl drain` d'un nœud) pour vérifier la répartition par zone et le `PodDisruptionBudget`.
- **`replicas: 2` dans `k8s/deployment.yaml`** : le HPA fixe désormais le nombre de pods (3 au minimum). Réappliquer ce fichier remet un instant le déploiement à 2 pods, même si le HPA en avait lancé 15 pendant un pic. Retirer cette ligne éviterait ce creux.
- **Non retenu pour l'instant** : les pods de réserve (coût permanent), Cloud Armor (limite globale, voir l'étape 4, partie 9), le cache (gain non mesurable avec nos tests) et la réduction de la mémoire demandée (économie, pas résilience).

## Étape 6 : sécurité et supervision

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* observer la stabilité du système en cas de stress ; tester la supervision Prometheus/Grafana.
>
> *Exercice :*
>
> 1. Analyser les logs et les comportements anormaux.
> 2. Simuler panne ou attaque et évaluer la continuité du service.

| Question du TP                                      | Réponse courte                                                                                                                                                                 | Partie |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| Quelles technologies, pourquoi, comment ?           | métriques Prometheus exposées par l'API, collectées par Managed Service for Prometheus, affichées dans Grafana, alertes dans Cloud Monitoring, logs JSON dans Cloud Logging | 1      |
| Tester la supervision Prometheus/Grafana            | collecte de chaque pod toutes les 30 s, tableau de bord de 13 panneaux, 7 règles d'alerte                                                                                      | 2      |
| Observer la stabilité du système en cas de stress | le service ralentit mais ne tombe pas (99,97 % de réussite à 300 utilisateurs) ; chaque symptôme a son panneau et son alerte                                                 | 3      |
| Simuler panne ou attaque et évaluer la continuité | panne : 0 erreur pour 1 pod perdu, 13 s de coupure si tous tombent ; attaque : grille 10/10 et fuzzing sans plantage sur GKE                                                    | 4      |
| Analyser les logs et les comportements anormaux     | journal d'audit pseudonymisé (`auth_failed`, `rate_limited`), requêtes `gcloud logging read`                                                                            | 5      |

Code : `src/api/main.py` (métriques et logs), `k8s/monitoring/` (collecte et Grafana), `monitoring/` (tableau de bord, alertes), `deploy/monitoring.sh` (installation), `loadtest/attack.py` (simulation d'attaque). Tests : `tests/test_api.py` (3 tests de métriques).

### 1. Technologies employées : pourquoi et comment

Le cours n°3 distingue trois sources d'information (les trois piliers de l'observabilité). Chacune a son outil :

| Pilier               | Ce qu'il dit                                        | Outil                                                                                                                                                                                   | Dans le projet                                          |
| -------------------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| **Métriques** | des valeurs agrégées : combien, à quelle vitesse | **Prometheus** (format et langage PromQL), collecté par **Managed Service for Prometheus** (GMP), affiché dans **Grafana**, alertes dans **Cloud Monitoring** | `/metrics` de l'API, collecté toutes les 30 s        |
| **Logs**       | des événements horodatés : qui, quoi, quand      | **Cloud Logging**                                                                                                                                                                 | une ligne JSON par requête, pseudonymisée (étape 4)  |
| **Traces**     | le chemin d'une requête entre services             | Cloud Trace                                                                                                                                                                             | non mis en place : un seul service (voir la partie 1.7) |

**Qui fait quoi.** GMP n'est **pas** une fusion de Prometheus et Grafana. Il remplace le **serveur Prometheus** : Google collecte les métriques des pods et les stocke à notre place, dans le projet. GMP parle le langage de Prometheus (PromQL), donc Grafana et Cloud Monitoring peuvent tous les deux le lire.

```
src/api/main.py                    k8s/monitoring/podmonitoring.yaml         monitoring/alerts/*.yaml
pods dss-api ──/metrics (Bearer)──▶ collecteurs GMP (gérés par Google, namespace gke-gmp-system)
     │                                    │
     │ stdout (JSON)                      ▼
     ▼                     Monarch : base de métriques de Cloud Monitoring (dans le projet)
Cloud Logging               │                         │                        │
(gcloud logging read)  Grafana (PromQL)       Metrics Explorer (PromQL)   règles d'alerte (PromQL)
                       k8s/monitoring/grafana.yaml   graphes rapides       ──▶ email
```

| Rôle                                     | Outil                                                         | Pourquoi ce choix                                                                        | Fichiers                                                                   |
| ----------------------------------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Exposer les métriques                    | `prometheus-fastapi-instrumentator` + `prometheus_client` | les deux méthodes du cours (diapos 20 et 21), en Python, dans l'API                     | `src/api/main.py`                                                        |
| Collecter et stocker                      | **GMP**                                                 | aucun serveur Prometheus à installer ni à sauvegarder ; déjà actif sur GKE Autopilot | `k8s/monitoring/podmonitoring.yaml`, `rbac.yaml`                       |
| Alerter                                   | **Cloud Monitoring**                                    | lit directement les métriques GMP et envoie des emails, sans Alertmanager à héberger  | `monitoring/alerts/*.yaml`                                               |
| Afficher                                  | **Grafana**                                             | demandé par le TP (« Prometheus/Grafana ») ; outil montré dans le cours (diapo 23)   | `k8s/monitoring/grafana.yaml`, `monitoring/grafana/dss-dashboard.json` |
| Donner à Grafana l'accès aux métriques | **datasource-syncer** + **Workload Identity**     | outil officiel de Google ; aucun mot de passe ni clé Google stockés                    | `k8s/monitoring/grafana.yaml`, `deploy/monitoring.sh`               |
| Journaliser                               | **Cloud Logging**                                       | GKE y envoie déjà la sortie des pods, sans agent à installer                          | `src/api/main.py`                                                        |
| Installer le tout                         | script **bash** idempotent                              | une commande, relançable sans risque                                                    | `deploy/monitoring.sh`, `deploy/create_viewer.sh`               |
| Simuler une attaque                       | **Python + `requests`**                               | même langage que le reste ; rapport JSON horodaté                                      | `loadtest/attack.py`                                                     |

#### 1.1 Exposer les métriques : `prometheus-fastapi-instrumentator` et `prometheus_client`

**Pourquoi.** Prometheus est le standard cité par le TP. L'application publie elle-même ses compteurs sous forme de texte sur `/metrics`, et n'importe quel outil compatible (GMP, Grafana, un Prometheus classique) peut les lire. Le cours présente deux méthodes ; nous utilisons les deux, car elles ne mesurent pas la même chose.

**Méthode 1 : middleware prêt à l'emploi** (`prometheus-fastapi-instrumentator`). Trois lignes dans `src/api/main.py` mesurent toutes les routes HTTP :

```python
Instrumentator(excluded_handlers=["/health", "/metrics", "/fonts/.*"]).add(
    instr_metrics.default(latency_lowr_buckets=LATENCY_BUCKETS)).instrument(app)
```

- Il produit `http_requests_total{handler, status}` (requêtes par route et par code `2xx`, `4xx`, `5xx`) et `http_request_duration_seconds{handler}` (latence par route).
- `/health`, `/metrics` et les polices sont **exclus** : les sondes Kubernetes (toutes les 10 à 20 s) et la collecte (toutes les 30 s) fausseraient les taux.
- `LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2.5, 5, 10)` : un histogramme Prometheus ne donne une réponse exacte qu'aux bornes de ses intervalles. Nous avons placé des bornes à **0,3 s** (SLO du cours) et **0,5 s** (alerte de latence), pour que « part des requêtes sous 300 ms » soit un compte exact (`le="0.3"`) et non une estimation.

**Méthode 2 : instrumentation manuelle** (`prometheus_client`), pour ce que le middleware ne peut pas voir :

| Métrique                                | Type                                         | Où elle est mise à jour dans `main.py`                              | À quoi elle sert                                                                                                                                                                                |
| ---------------------------------------- | -------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `dss_inference_latency_seconds{model}` | histogramme                                  | `score()` : `time.perf_counter()` autour de `predictor.predict()` | durée de l'**inférence seule** (anonymisation, nettoyage, LSTM). Comparée à la latence HTTP, elle dit si le temps est perdu dans le modèle ou dans la file d'attente d'un pod saturé |
| `dss_toxicity_score`                   | histogramme (bornes 10, 25, 50, 75, 90, 100) | `score()` : `SCORE_HIST.observe(value)`                             | distribution des scores ; la part au-dessus de 50 sert d'indicateur de **dérive**                                                                                                         |
| `dss_auth_failures_total{reason}`      | compteur                                     | `authenticate()`, avant de répondre 401                              | essais de clés (`absente` ou `invalide`) : brute force                                                                                                                                      |
| `dss_rate_limited_total{client}`       | compteur                                     | `rate_limit()`, avant de répondre 429                                | client qui dépasse la limite : abus ou extraction du modèle                                                                                                                                    |

Deux détails du code :

- **Séries créées à 0 au démarrage** (`lifespan`) : `AUTH_FAILURES.labels(reason=...)`, `RATE_LIMITED.labels(client=...)` pour chaque client connu, `INFERENCE_LATENCY.labels(model="lstm")`. Sans cela, une métrique n'existe qu'après le premier 401 ou le premier 429, et Cloud Monitoring **refuse de créer** une règle d'alerte qui cite une métrique encore inconnue.
- **Aucune donnée personnelle dans les étiquettes** : ni texte, ni IP, ni clé ; le seul libellé client est le **nom** du client (`default`, `prometheus`…). Cela limite aussi le nombre de séries : une étiquette par IP créerait une série par visiteur.

**`/metrics` reste protégé.** La route appelle la même fonction `authenticate()` que `/score`, en acceptant la clé dans `X-API-Key` **ou** dans `Authorization: Bearer` (le format qu'envoie un collecteur Prometheus). Sans clé, elle répond 401 : l'activité du service n'est pas publique.

#### 1.2 Collecter : Managed Service for Prometheus (GMP)

**Pourquoi GMP plutôt qu'un Prometheus installé dans le cluster ?** Un Prometheus auto-hébergé demande un disque persistant, de la mémoire, des mises à jour et une sauvegarde. Il perd ses données si son pod tombe, et il faut le dimensionner quand le nombre de pods augmente. Avec GMP, la collecte est **déjà active** sur GKE Autopilot, Google stocke les données **24 mois**, et les requêtes restent en PromQL standard : on pourrait revenir à un Prometheus classique sans réécrire le tableau de bord ni les alertes.

**Comment.** Un seul objet Kubernetes, `k8s/monitoring/podmonitoring.yaml`, dit à GMP quoi collecter :

```yaml
kind: PodMonitoring
spec:
  selector:
    matchLabels:
      app: dss-api          # tous les pods de l'API, y compris ceux ajoutés par le HPA
  endpoints:
    - port: 8080
      path: /metrics
      interval: 30s
      timeout: 10s          # un pod saturé qui ne répond pas en 10 s est compté comme « down »
      authorization:
        type: Bearer
        credentials:
          secret:
            name: dss-prometheus-scrape
            key: token
```

- **Découverte automatique** : le sélecteur `app: dss-api` vise les pods par leur étiquette. Quand le HPA passe de 3 à 15 pods, les nouveaux sont collectés sans rien changer.
- **Clé dédiée au collecteur** : `deploy/monitoring.sh` (étape 1) crée la clé `dss-prometheus-key` dans Secret Manager, la copie dans le Secret Kubernetes `dss-prometheus-scrape`, et ajoute `prometheus:<clé>` à `API_KEYS`. Le collecteur apparaît donc sous le nom `prometheus` dans les logs, et on peut révoquer sa clé sans toucher aux clients.
- **Moindre privilège** : `k8s/monitoring/rbac.yaml` donne au compte `collector` de GMP le droit de lire **un seul Secret** (`resourceNames: ["dss-prometheus-scrape"]`). Il ne voit ni les clés des clients, ni `LOG_SALT`, ni la clé privée TLS.
- `scheme: http` : la collecte se fait à l'intérieur du cluster, de pod à pod ; le HTTPS s'arrête au load balancer.

#### 1.3 Alerter : Cloud Monitoring

**Pourquoi.** Les métriques collectées par GMP sont stockées dans Monarch, la base de **Cloud Monitoring**. Ses règles d'alerte savent lire du PromQL et envoyer un email : il n'y a pas d'Alertmanager à héberger, et les alertes continuent de fonctionner même si Grafana est arrêté.

**Comment.** Une règle par fichier dans `monitoring/alerts/`, au format de Cloud Monitoring :

```yaml
# monitoring/alerts/06-brute-force.yaml (extrait)
displayName: "DSS - Essais de cles API (brute force)"
severity: CRITICAL
documentation:
  content: |
    A faire : gcloud logging read 'jsonPayload.event="auth_failed"' --freshness=15m ; regrouper par ip_id ...
conditions:
  - conditionPrometheusQueryLanguage:
      duration: 60s            # la condition doit rester vraie 1 minute
      evaluationInterval: 60s  # évaluée chaque minute
      query: |
        sum(rate(dss_auth_failures_total{job="dss-api"}[5m])) * 60 > 20
notificationChannels: []       # rempli par deploy/monitoring.sh
```

- `deploy/monitoring.sh` (étape 4) crée le canal email, remplace `notificationChannels: []` par ce canal (`sed`), puis crée chaque règle avec `gcloud alpha monitoring policies create`. Une règle déjà créée est sautée : le script peut être relancé.
- Chaque règle contient dans sa `documentation` **la commande à lancer pour enquêter** : elle arrive dans l'email.
- L'alerte « Aucun pod ne répond » utilise `(sum(up{job="dss-api"}) or vector(0)) < 1`. Quand tous les pods ont disparu, la série `up` n'existe plus du tout et `sum(...)` ne renvoie rien ; sans `or vector(0)`, l'alerte ne se déclencherait jamais au moment où elle est la plus utile.
- Le CPU vient des métriques système de Cloud Monitoring (`kubernetes_io:container_cpu_request_utilization`), lisibles en PromQL : pas besoin de collecter cAdvisor ou node-exporter nous-mêmes.
- **Deuxième source pour les échecs d'authentification** : `deploy/monitoring.sh` crée aussi la métrique basée sur les logs `dss_auth_failed` (filtre `jsonPayload.event="auth_failed"`). Elle compte les mêmes événements à partir des logs, indépendamment de `/metrics`, et reste visible dans Metrics Explorer même si la collecte tombe.

#### 1.4 Afficher : Grafana, datasource-syncer et Workload Identity

**Pourquoi Grafana.** Le TP demande « Prometheus/Grafana ». GMP n'offre que Metrics Explorer, pratique pour un graphe rapide mais pas pour un tableau de bord permanent. Grafana tourne dans le cluster et passe par le même load balancer que l'API.

**Comment, dans `k8s/monitoring/grafana.yaml`.**

| Réglage                                                                       | Pourquoi                                                                                                                 |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| image `grafana/grafana:11.3.0` (version fixée), 1 pod, 250 m de CPU         | version reproductible ; Grafana n'a pas besoin de redondance pour un TP                                                  |
| `GF_AUTH_ANONYMOUS_ENABLED=false`, `GF_USERS_ALLOW_SIGN_UP=false`          | connexion obligatoire, personne ne peut créer de compte                                                                 |
| `GF_SERVER_ROOT_URL` et `GF_SERVER_SERVE_FROM_SUB_PATH=true`               | Grafana est servi sous `/grafana/`, derrière l'Ingress de l'API (règle `path: /grafana` dans `k8s/ingress.yaml`) |
| `GF_SECURITY_COOKIE_SECURE=true`                                             | le cookie de session ne circule qu'en HTTPS                                                                              |
| `GF_SECURITY_DISABLE_GRAVATAR`, `GF_ANALYTICS_*=false`                     | aucun appel à un service tiers, donc aucune IP de visiteur envoyée ailleurs (RGPD)                                     |
| `runAsUser: 472`, `drop: ["ALL"]`, `automountServiceAccountToken: false` | mêmes règles que les pods de l'API : non root, aucun droit Kubernetes                                                  |
| mot de passe `admin` lu dans le Secret `grafana-admin`                     | copié depuis Secret Manager (`grafana-admin-password`) par `deploy/monitoring.sh`                                           |
| `BackendConfig` avec health check sur `/grafana/api/health`                | le load balancer sait si Grafana est prêt                                                                               |

- **Tableau de bord versionné dans Git** : `monitoring/grafana/dss-dashboard.json` est chargé dans la ConfigMap `grafana-dashboards`, que Grafana lit au démarrage (*provisioning*, `disableDeletion: true`). Modifier le tableau de bord, c'est modifier ce fichier et relancer `deploy/monitoring.sh`.
- **Source de données** : `deploy/monitoring.sh` crée la source `gmp` par l'API HTTP de Grafana, avec l'adresse `https://monitoring.googleapis.com/v1/projects/$PROJECT/location/global/prometheus` (l'API compatible Prometheus de Cloud Monitoring).
- **Compte en lecture seule** : `deploy/create_viewer.sh` crée le compte `lecteur` (rôle Viewer, page d'accueil = le tableau de bord du projet). C'est lui qu'on partage, jamais `admin`.

**Pourquoi le datasource-syncer.** L'API de Cloud Monitoring exige un jeton Google, qui expire après 1 heure. Grafana ne sait pas en obtenir un seul.

**Comment.** Le CronJob `datasource-syncer` (image officielle de Google, `v0.17.2-gke.2`) tourne toutes les 10 minutes (`schedule: "*/10 * * * *"`) et écrit un jeton neuf dans la source `gmp` de Grafana.

- Il obtient ce jeton par **Workload Identity** : `deploy/monitoring.sh` donne au compte Kubernetes `datasource-syncer` le seul rôle `roles/monitoring.viewer`. Il lit les métriques et ne peut rien modifier. Aucune clé de compte de service Google n'est créée ni stockée.
- Pour écrire dans Grafana, il utilise un jeton de compte de service Grafana, rangé dans le Secret `grafana-syncer`. L'exemple de Google écrit ce jeton en clair dans le manifeste ; nous le lisons dans un Secret.
- Grafana lui-même n'a **aucun droit Google Cloud**.

#### 1.5 Journaliser : Cloud Logging

**Pourquoi.** GKE envoie automatiquement la sortie standard de chaque pod à Cloud Logging : pas d'agent à installer, et les logs sont consultables par `gcloud logging read` ou dans la console.

**Comment.** L'API écrit **une ligne JSON par événement** :

```python
logging.basicConfig(level=logging.INFO, format="%(message)s")
...
log.info(json.dumps({"event": "score", "client": client, "model": req.model,
                     "text_id": pseudonym(req.text), "score": value, "latency_ms": round(latency, 2)}))
```

- Comme chaque ligne est un objet JSON, Cloud Logging la range dans `jsonPayload` : on peut filtrer sur un champ (`jsonPayload.event="auth_failed"`) au lieu de chercher du texte.
- Événements écrits : `score` (chaque requête), `auth_failed` (dans `authenticate()`), `rate_limited` (dans `rate_limit()`), `weak_api_key` (au démarrage, si une clé fait moins de 16 caractères).
- **Pseudonymisation** : `pseudonym()` calcule un HMAC-SHA256 avec la clé secrète `LOG_SALT`. Le texte et l'IP n'apparaissent jamais, seulement `text_id` et `ip_id` (étape 4, partie 4).
- `ip_id` vient de `client_ip()`, qui prend l'IP ajoutée par le load balancer (`TRUSTED_PROXY_HOPS=2`) et ignore ce que le client écrit lui-même dans `X-Forwarded-For` : un attaquant ne peut pas brouiller le journal d'audit.

#### 1.6 Simuler : `loadtest/attack.py` et Locust

**Pourquoi un script à nous.** Il faut des attaques **reproductibles** et **horodatées**, pour retrouver chaque phase dans les logs et sur le tableau de bord. Python et `requests` suffisent ; Locust (étape 5) sert déjà pour la charge et la panne.

**Comment.**

- Une `requests.Session` qui vérifie le certificat du serveur (`--cacert tls.crt`), sauf si l'on demande explicitement `--insecure`.
- Quatre phases, choisies par `--phases` : `phase_grille` (la grille de l'étape 4, une requête par cas), `phase_fuzzing` (11 entrées malformées), `phase_bruteforce` (clés aléatoires `secrets.token_urlsafe(24)` à débit régulier, 2 par seconde pendant 3 min), `phase_extraction` (`ThreadPoolExecutor` de 20 threads, une seule clé valide, 90 s sans pause).
- L'extraction est jouée en dernier : après elle, la clé reste bloquée une minute par la limite.
- Le rapport `reports/attack/attack_<date>.json` contient l'heure de début et de fin de chaque phase et les codes HTTP obtenus. Ni la clé API ni les clés essayées n'y sont écrites.

#### 1.7 Ce que nous n'avons pas utilisé, et pourquoi

| Outil                       | Pourquoi pas                                                                                                                                                           |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Prometheus auto-hébergé   | GMP fait le même travail sans serveur à maintenir (partie 1.2)                                                                                                       |
| Alertmanager                | Cloud Monitoring envoie déjà les alertes                                                                                                                             |
| Cloud Trace / OpenTelemetry | un seul service : la métrique `dss_inference_latency_seconds` suffit à savoir où passe le temps. Utile si l'API appelait une base de données ou un autre modèle |
| PagerDuty, Slack            | email suffisant pour un TP (voir les limites, partie 6)                                                                                                                |

### 2. Tester la supervision Prometheus/Grafana

**Mise en place.** Dans Cloud Shell, à la racine du dépôt, une fois l'image reconstruite avec les métriques :

```bash
TAG=v4; IMAGE=$REGION-docker.pkg.dev/$PROJECT/dss/api:$TAG
gcloud builds submit --tag $IMAGE .
kubectl set image deployment/dss-api api=$IMAGE && kubectl rollout status deployment/dss-api

EMAIL=<adresse des alertes> make monitoring     # sans make : EMAIL=... bash deploy/monitoring.sh
```

| Étape du script    | Ce qui est créé                                                                                                                                                                                        |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Clé de collecte | secret `dss-prometheus-key` (Secret Manager) → Secret Kubernetes `dss-prometheus-scrape` ; ajout de `prometheus:<clé>` à `API_KEYS`, puis redémarrage progressif des pods                    |
| 2. Collecte GMP     | `PodMonitoring` et droit de lecture sur ce seul Secret                                                                                                                                                 |
| 3. Grafana          | pod Grafana, mot de passe dans Secret Manager (`grafana-admin-password`), tableau de bord, source de données `gmp`, compte `lecteur`, **datasource-syncer** et sa première synchronisation |
| 4. Alertes          | métrique basée sur les logs `dss_auth_failed`, canal email, 7 règles d'alerte                                                                                                                       |

Si le pod Grafana redémarre (sa base est perdue) : `make grafana-reset` (ou `ALERTS=0 bash deploy/monitoring.sh`) refait seulement les étapes 1 à 3.

**Accès à Grafana :** en public sur https://34-54-213-190.sslip.io/grafana/ (bouton « Monitoring » de la page web), avec le compte `lecteur` ; en administrateur par un tunnel :

```bash
kubectl port-forward svc/grafana 3000:3000                         # puis http://localhost:3000/grafana/
gcloud secrets versions access latest --secret=grafana-admin-password   # utilisateur : admin
```

**Vérifier la collecte** dans la console, sous **Monitoring → Metrics Explorer → PromQL** : `up{job="dss-api"}` doit valoir 1 pour chaque pod, environ une minute après l'installation.

**Tableau de bord Grafana.** Les panneaux reprennent le « dashboard typique » du cours (diapo 22), auxquels s'ajoutent des panneaux propres au modèle et à la sécurité. Rafraîchi toutes les 30 s, sur la dernière heure.

| Ligne                 | Panneaux                                                                                                                         | Requête PromQL principale                                                                                   |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Vue d'ensemble        | requêtes/s, latence P95, part d'erreurs 5xx, pods qui répondent                                                                | `count(up{job="dss-api"} == 1)` pour les pods                                                              |
| Trafic et latence     | requêtes/s par code ; latence HTTP P50/P95/P99 ; **SLO** (part des requêtes sous 300 ms) ; latence de l'inférence seule | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{handler="/score"}[2m])))` |
| Ressources            | CPU et mémoire de chaque pod, rapportés à la demande                                                                          | `kubernetes_io:container_cpu_request_utilization{container_name="api"}`                                    |
| Modèle et sécurité | part des textes jugés toxiques ; échecs d'authentification par minute ; réponses 429 par minute et par client                 | `sum by (reason) (rate(dss_auth_failures_total[2m])) * 60`                                                 |

La part des textes toxiques sert d'indicateur de **dérive** (cours n°1) : un changement brutal peut venir d'une campagne de spam ou de textes d'un nouveau type que le modèle connaît mal.

<!-- ![Tableau de bord Grafana pendant un test de charge](reports/images/grafana_dashboard.png) -->

*À compléter : capture `reports/images/grafana_dashboard.png`, prise pendant `SCENARIO=ramp` puis pendant la simulation d'attaque.*

**Alertes.** Les règles reprennent les exemples du cours (diapos 24 et 25), évaluées par Cloud Monitoring chaque minute. Une notification part par email.

| Alerte                | Condition                                          | Durée | Gravité | Origine                            |
| --------------------- | -------------------------------------------------- | ------ | -------- | ---------------------------------- |
| SLO latence           | moins de 95 % des requêtes `/score` sous 300 ms | 10 min | warning  | règle SLO du cours (diapo 25)     |
| Latence P95           | P95 de `/score` > 500 ms                         | 5 min  | warning  | « latence > 500 ms » (diapo 24)  |
| Erreurs 5xx           | plus de 1 % de réponses 5xx                       | 5 min  | critical | « erreurs 5xx > 1 % » (diapo 24) |
| CPU                   | un pod au-dessus de 90 % du CPU demandé           | 5 min  | warning  | « CPU > 90 % » (diapo 24)        |
| Service indisponible  | aucun pod ne répond à la collecte                | 2 min  | critical | test de failover (étape 5)        |
| Brute force           | plus de 20 échecs d'authentification par minute   | 1 min  | critical | journal d'audit (étape 4)         |
| Extraction du modèle | au moins une réponse 429 sur 5 min                | 1 min  | warning  | vol de modèle (cours n°1)        |

**Seuils choisis d'après les mesures de l'étape 5.** À vide, l'API répond en 19 à 27 ms (étape 3). Le P95 ne dépasse 500 ms que lorsque le CPU sature : à partir de 50 utilisateurs en croissance progressive, quand les pods ajoutés n'étaient pas encore prêts, et de 120 utilisateurs au stress test. L'alerte signale donc une vraie saturation, pas un ralentissement passager. Le seuil CPU de 90 % est au-dessus de celui du HPA (50 %, 70 % au moment des tests) : l'alerte ne se déclenche que si l'autoscaling ne suit plus, par exemple au maximum de pods ou en attente de nœuds.

### 3. Observer la stabilité du système en cas de stress

Pendant le stress test de l'étape 5, le service est resté **stable** : il a ralenti (P95 de 3,6 s à 300 utilisateurs) mais n'est pas tombé (99,97 % de réussite). Ces symptômes ont été mesurés par Locust, le relevé du HPA et Cloud Monitoring. Le tableau ci-dessous indique le panneau et l'alerte qui les montrent désormais :

| Symptôme observé à l'étape 5                                             | Mesuré par              | Panneau Grafana                                                     | Alerte                                                                                                                                                   |
| ---------------------------------------------------------------------------- | ------------------------ | ------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| CPU saturé (99 à 123 % de la demande) au-delà de 120 utilisateurs         | relevé du HPA           | « CPU utilisé / CPU demandé, par pod »                          | CPU > 90 %                                                                                                                                               |
| P95 au-dessus de 500 ms, SLO de 300 ms non tenu                              | Locust                   | « Latence HTTP de /score », « SLO »                             | Latence P95, SLO latence                                                                                                                                 |
| Latence HTTP multipliée par 6, alors que le calcul ne prend que 19 à 27 ms | Locust                   | « Latence de l'inférence seule » comparée à « Latence HTTP » | – : si l'inférence reste rapide pendant que la latence HTTP monte, le temps est perdu en **file d'attente** (pods saturés), pas dans le modèle |
| Pods redémarrés par la sonde de vie (15 erreurs 502)                       | événements Kubernetes  | « Pods qui répondent »                                           | – (voir ci-dessous)                                                                                                                                     |
| Plateau du débit à environ 195 req/s                                       | Locust, Cloud Monitoring | « Requêtes / seconde »                                           | –                                                                                                                                                       |
| Rafale d'un seul client (5 566 réponses 429)                                | Locust                   | « Requêtes refusées par la limite »                             | Extraction du modèle                                                                                                                                    |

**Ce que la supervision ne voit pas bien.**

- Les **502 renvoyés par le load balancer** (pod redémarré, aucun pod prêt) ne passent par aucun pod : `http_requests_total` ne les compte pas, et l'alerte « Erreurs 5xx » reste muette. Seuls Locust et les métriques du load balancer (`backend_request_count`, partie 3 de l'étape 5) les voient.
- La **coupure de 13 s** du failover est plus courte que l'intervalle de collecte (30 s) et que la durée de l'alerte « Aucun pod ne répond » (2 min) : elle ne déclencherait pas d'alerte. C'est un choix : alerter pour chaque coupure de quelques secondes ferait trop de fausses alertes.

### 4. Simuler panne ou attaque et évaluer la continuité du service

**Panne.** Testée à l'étape 5 (partie 3), sous 40 utilisateurs constants :

| Panne simulée           | Continuité du service                                                             |
| ------------------------ | ---------------------------------------------------------------------------------- |
| 1 pod supprimé          | **continu** : 0 erreur, pod remplacé en 16 s                                |
| tous les pods supprimés | **coupure de 13 s** (4 % d'erreurs sur le test), service complet après 40 s |

Depuis, la redondance a été renforcée (étape 5, partie 7) : 3 pods au minimum, répartis sur 2 zones, et un `PodDisruptionBudget`. La panne d'un nœud entier n'a pas encore été testée.

**Attaque.** Script `loadtest/attack.py` :

```bash
API_KEY=$(gcloud secrets versions access latest --secret=dss-api-key) \
  python3 loadtest/attack.py --host https://$IP --cacert tls.crt
```

| Phase       | Ce que fait l'attaquant                                                            | Réponse attendue                                | Ce qui doit le détecter                                                  |
| ----------- | ---------------------------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------- |
| Grille      | les tests de la grille de l'étape 4, une requête chacun                          | 401, 422, 413, en-têtes présents, HTTP refusé | –                                                                        |
| Fuzzing     | JSON invalide, mauvais types, octets non UTF-8, injection de fausses lignes de log | 4xx, **jamais de plantage**                | –                                                                        |
| Brute force | 3 minutes d'essais de clés au hasard, 2 par seconde                               | 401                                              | alerte **Brute force**, panneau « Échecs d'authentification »    |
| Extraction  | 90 s de rafale, 20 requêtes en parallèle avec une seule clé valide              | 200 puis 429                                     | alerte **Extraction du modèle**, panneau « Requêtes refusées » |

**Résultats sur GKE** (12/11/2026, 15 h 05, rapport `reports/attack/attack_20261112_1505.json`, phases `grille` et `fuzzing`) :

| Phase   | Résultat                                                                                                                                                                                                                                                                                                                                          |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Grille  | **10 tests sur 10 conformes** : 401 sans clé, avec une mauvaise clé et sur `/metrics` ; 422 pour un texte vide, trop long ou un modèle inconnu ; 413 au-delà de 32 Ko ; 200 pour une requête valide ; en-têtes de sécurité présents ; HTTP refusé                                                                                |
| Fuzzing | **11 cas, aucun plantage** : JSON invalide, mauvais type, champ manquant, liste au lieu d'un objet, `Content-Type` texte → 422 ; octets non UTF-8 → 400 ; caractères de contrôle, injection de logs, emojis seuls, texte de 5 000 caractères → 200 (traités normalement) ; modèle LAYA → 503, voulu (LAYA n'est pas dans l'image) |

**Résultats en local** (API sur le poste, limite à 100 requêtes par minute) : la rafale d'extraction obtient **100 réponses 200, puis 4 019 réponses 429**.

*À compléter : phases `bruteforce` et `extraction` sur GKE, avec les captures de l'alerte déclenchée et de l'email reçu. Avec 3 pods, la limite compte 600 requêtes par minute **sur chaque pod** : il faut plus de 1 800 requêtes par minute pour obtenir des 429.*

**Continuité pendant une attaque.** La limite de requêtes est tenue **par client** (`_hits[client]` dans `rate_limit()`) : un client bloqué ne bloque pas les autres. Le brute force est refusé dès la vérification de la clé, avant le modèle, et coûte très peu de CPU. Cette continuité pour les autres clients découle du code mais n'a pas été mesurée pendant une attaque.

### 5. Analyser les logs et les comportements anormaux

**Requêtes d'analyse après une attaque :**

```bash
# combien d'échecs, et depuis combien de sources différentes (ip_id = HMAC de l'IP, pas l'IP)
gcloud logging read 'resource.labels.container_name="api" AND jsonPayload.event="auth_failed"' \
  --freshness=30m --format="value(jsonPayload.ip_id)" | sort | uniq -c | sort -rn
# quel client a été bloqué par la limite
gcloud logging read 'jsonPayload.event="rate_limited"' --freshness=30m \
  --format="value(jsonPayload.client)" | sort | uniq -c
# l'IP réelle, si une enquête l'exige : logs du load balancer (gardés 30 jours, voir étape 4)
gcloud logging read 'resource.type="http_load_balancer" AND httpRequest.status=401' --freshness=30m --limit=5
# pods redémarrés pendant un pic (sonde de vie)
kubectl get events --sort-by=.lastTimestamp | grep -iE "probe|killing|backoff"
```

**Comportements anormaux et leur signature :**

| Comportement anormal                                                                               | Signature dans les logs et les métriques                                                                         | Réaction                                                                                                     |
| -------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Brute force** (essais de clés)                                                            | des centaines de lignes `auth_failed` avec le **même `ip_id`** ; `dss_auth_failures_total` qui monte | alerte Brute force ; bloquer la source avec une règle **Cloud Armor**                                  |
| **Extraction du modèle** (copie par requêtes massives)                                     | lignes `rate_limited` pour un même `client` ; réponses 429                                                  | alerte Extraction ; **révoquer la clé** du client en la retirant de `API_KEYS` (étape 4, partie 1) |
| **Injection de logs** (fausse ligne `{"event": "score", "client": "admin"}` dans le texte) | **absente** des logs : le texte n'est jamais journalisé, seul son HMAC l'est                               | rien à faire : l'attaque échoue par construction                                                            |
| **Saturation**                                                                               | `Liveness probe failed` dans les événements ; P95 et CPU en hausse                                            | vérifier le HPA (`kubectl get hpa dss-api`) ; relever `maxReplicas` si le maximum est atteint            |
| **Dérive des données**                                                                     | part des textes toxiques qui change brutalement                                                                   | regarder si c'est une campagne de spam ou un nouveau type de texte ; réentraîner si besoin                  |
| **Spam** (même texte répété)                                                             | même `text_id` sur de nombreuses lignes `score`                                                              | limiter le client ; un cache (étape 5, partie 6) éviterait de recalculer                                    |

Le même `ip_id` sur des centaines de lignes `auth_failed` signe un brute force depuis une seule source. Le texte et la clé essayée n'apparaissent jamais dans les logs.

### 6. Limites et améliorations

- **5xx du load balancer** : quand tous les pods tombent, le load balancer répond 502 sans qu'aucun pod ne compte ces erreurs. L'alerte « Service indisponible » couvre une coupure longue. Pour une mesure complète, ajouter une alerte sur la métrique `loadbalancing.googleapis.com/https/request_count` (code de réponse 5xx), qui voit les erreurs côté load balancer.
- **Attaques lentes** : un brute force à 10 essais par minute, ou une copie du modèle juste sous la limite, ne déclenchent aucune alerte (seuils par minute, pas de métrique de requêtes par client). Tests et pistes détaillés dans `docs/pistes-tests-et-etape7.md`.
- **Grafana sans disque** : si son pod redémarre, la source de données, le compte lecteur et le jeton du synchroniseur sont perdus. Il suffit de relancer `make grafana-reset` ; le tableau de bord, lui, est rechargé automatiquement. En production : un disque persistant ou **Grafana Cloud** / Cloud Monitoring seul. Grafana lui-même n'est surveillé par aucune alerte.
- **Documentation des alertes** : le texte de l'alerte CPU (`monitoring/alerts/04-cpu.yaml`) parle encore de 10 pods et 195 req/s ; le maximum est désormais de 15 pods.
- **Coût** : GMP facture les échantillons ingérés. Avec 3 à 15 pods collectés toutes les 30 s, le volume reste très faible. Le pod Grafana (250 m de CPU) est facturé par Autopilot tant qu'il tourne : `kubectl scale deployment/grafana --replicas=0` en dehors des séances.
- **Alertes par email seulement** : en production, ajouter un canal d'astreinte (PagerDuty, Slack) pour les alertes `critical`.

## Étape 7 : modélisation de l'infrastructure Cloud

> **Énoncé du TP**
>
> *Objectifs pédagogiques :* schématiser et justifier chaque composant ; penser sécurité et conformité (approche zero trust).
>
> *Exercice :*
>
> 1. Lister les composants (API, IA, stockage, supervision).
> 2. Dessiner les flux, authentifications, et chiffrement.
> 3. Rédiger un texte explicatif et une légende du schéma.

*En cours.* La base est la section « Vue d'ensemble : architecture et connexions » en tête de ce document (schéma, et tableau des connexions avec authentification et chiffrement). Le plan du schéma final et de l'analyse zero trust est dans `docs/pistes-tests-et-etape7.md`.

## Lancer le projet

En local (entraînement et tests) :

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (Linux/Mac : source .venv/bin/activate)
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -m src.anonymization.build_datasets
python -m src.preprocessing.build_clean_datasets
python -m src.models.lstm_binary
python -m src.models.bert_binary
python -m src.models.laya_binary --train-rows 10000
python -m src.models.export_lstm   # copie le LSTM dans artifacts/
make test                          # ou : python -m pytest tests
```

API en local (nécessite `artifacts/lstm.pt`, `artifacts/vocab.json` et `artifacts/config.json`) :

```bash
pip install -r requirements-api.txt
make api                           # http://localhost:8000/docs, clé "test"
make docker                        # même chose dans l'image Docker, sur http://localhost:8080
```

Sur Google Cloud (Cloud Shell, à la racine du dépôt) : voir « Arborescence du dépôt et commandes » en haut du README.

```bash
gcloud config set project <ID_DU_PROJET>
make bootstrap cluster             # une seule fois
make build deploy TAG=v7           # à chaque nouvelle version
make monitoring EMAIL=<adresse>    # supervision et alertes
make status smoke                  # vérifier
```
