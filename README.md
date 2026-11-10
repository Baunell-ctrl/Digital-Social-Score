# Digital Social Score

API de détection de toxicité dans des textes, conforme au RGPD.
Le dépôt couvre trois étapes :

1. **Données** : exploration, anonymisation et registre de traitement.
2. **Modèles** : nettoyage des textes, puis entraînement et comparaison de trois modèles (LSTM, BERT, LAYA).
3. **API** : service web sécurisé qui renvoie un score de toxicité de 0 à 100, packagé avec Docker.

## Étape 1

### 1. Jeu de données

[Toxic Comment Classification Dataset](https://www.kaggle.com/competitions/jigsaw-toxic-comment-classification-challenge) (commentaires Wikipédia, 6 étiquettes de toxicité).

| Fichier                    | Lignes |
| -------------------------- | ------ |
| `data/train_dataset.csv` | 25 960 |
| `data/val_dataset.csv`   | 6 490  |

Colonnes : `id`, `comment_text`, `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`.

### 2. Anonymisation avec un NER

Script : `python -m src.anonymization.build_datasets` (code dans `src/anonymization/anonymizer.py`, tests dans `tests/`).

- **Expressions régulières** pour les formats stricts : email, URL, adresse IP, téléphone, `@pseudo`.
- **spaCy** (`en_core_web_sm`) pour les noms de personnes et les lieux.
- Chaque donnée trouvée est remplacée par une étiquette : `[PERSON]`, `[LOCATION]`, `[EMAIL]`, `[URL]`, `[IP]`, `[PHONE]`, `[USERNAME]`.
- L'opération est **irréversible** : on ne peut pas retrouver la personne.

Sorties : `data/anonymized/{train,val}_dataset.csv`, `reports/anonymization_stats.json`, `reports/before_after_sample.csv` (exemples avant / après).

### 3. Comparaison avant / après et justification des choix

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

### 4. Registre de traitement des données

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

## Étape 2

### 1. Nettoyage des textes

Script : `python -m src.preprocessing.build_clean_datasets` (code dans `src/preprocessing/cleaner.py`).
Entrée : `data/anonymized/`. Sortie : `data/cleaned/{train,val}_dataset.csv` et `reports/cleaning_before_after_sample.csv`.

Le nettoyage fait 4 choses, dans cet ordre :

- **Minuscules** : « Hate » et « hate » deviennent le même mot, le vocabulaire est plus petit.
- **Emojis supprimés** : ils n'apportent rien aux modèles de texte simples (LSTM, LAYA).
- **Sauts de ligne supprimés** : remplacés par un espace.
- **Ponctuation supprimée** : les apostrophes sont gardées (`don't`).

Mêmes lignes, mêmes identifiants, mêmes étiquettes qu'avant. Les étiquettes d'anonymisation perdent leurs crochets (`[PERSON]` devient `person`) : accepté, c'est simple et sans effet sur l'entraînement.

### 2. Modèles : LSTM, BERT, LAYA

Les trois modèles utilisent le même jeu de données, la même règle du OU et le même découpage, donc leurs scores sont comparables.

| Jeu   | Commentaires | Rôle                                        |
| ----- | ------------ | -------------------------------------------- |
| Train | 23 364       | apprentissage                                |
| Dev   | 2 596        | choix du meilleur epoch                      |
| Test  | 1 308        | évaluation finale, jamais vu par le modèle |

### LSTM

Script : `python -m src.models.lstm_binary`. Résultats détaillés : `reports/results_lstm.json`.

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

### BERT

Script : `python -m src.models.bert_binary`. Résultats détaillés : `reports/results_bert.json`.

**Modèle.** DistilBERT (`distilbert-base-uncased`), une version allégée de BERT (66 millions de paramètres, environ 2 fois plus rapide). Même jeu de données, même règle du OU et même découpage (train, dev, test) que le LSTM, donc les scores sont comparables. Réglages : lots de 8, 96 mots maximum par commentaire, 2 epochs ; le meilleur epoch (le 1er) est choisi sur le dev.

**Résultats sur le test.**

| Précision | Rappel | F1    | Temps d'inférence      |
| --------- | ------ | ----- | ---------------------- |
| 0,941     | 0,919  | 0,930 | 6,5 ms par commentaire |

**Interprétation.**

- **Précision 0,94** : quand le modèle dit « toxique », il a raison 19 fois sur 20 (37 faux positifs sur 1 308).
- **Rappel 0,92** : il retrouve 92 % des commentaires réellement toxiques (52 oubliés).
- **Fiable** : le F1 du dev (0,932) et celui du test (0,930) sont presque identiques, le modèle ne sur-apprend pas.
- **Meilleur que le LSTM** : +2,8 points de F1 (0,930 contre 0,902), surtout grâce à la précision (0,941 contre 0,894), car il comprend mieux le contexte des phrases.
- **Plus lent** : 6,5 ms par commentaire, soit environ 16 fois plus que le LSTM (0,4 ms), mais cela reste plus de 150 commentaires par seconde.
- **Limites** : il est plus lourd à entraîner et à déployer que le LSTM (268 Mo de poids contre 16 Mo, environ 23 minutes d'entraînement pour 2 epochs) et rate encore environ 8 % des commentaires toxiques.

### LAYA

Script : `python -m src.models.laya_binary --train-rows 10000`. Résultats détaillés : `reports/results_laya.json`.

**Modèle.** LAYA (`convaiinnovations/laya`), un modèle « décisionnaire » construit sur l'encodeur ModernBERT-large (environ 400 millions de paramètres), entraîné par renforcement (RLCD) pour donner des probabilités bien calibrées. On lui pose une seule question binaire : « ce commentaire est-il toxique ? ». Même jeu de données, même règle du OU (colonne `label`) et même dev et test que le LSTM et BERT.

**Réglages.** 10 000 commentaires tirés au hasard dans le train (pour limiter le temps de calcul), 1 epoch, 128 mots maximum. L'encodeur est gelé, seule la tête de décision est entraînée : le modèle complet ne tient pas dans les 8 Go de la carte graphique. Entraînement en 5 minutes 20 sur une RTX 5050.

**Résultats sur le test.**

| Précision | Rappel | F1    | Temps d'inférence       |
| --------- | ------ | ----- | ----------------------- |
| 0,908     | 0,906  | 0,907 | 10,3 ms par commentaire |

**Interprétation.**

- **Précision 0,91** : quand le modèle dit « toxique », il a raison 9 fois sur 10 (59 faux positifs sur 1 308).
- **Rappel 0,91** : il retrouve 91 % des commentaires réellement toxiques (60 oubliés).
- **Équilibré et fiable** : précision et rappel sont presque égaux, et le F1 du dev (0,917) est proche de celui du test (0,907).
- **Bien calibré** : l'erreur de calibration (ECE) passe de 0,070 à 0,023 après entraînement. Une probabilité de 0,9 veut vraiment dire « toxique 9 fois sur 10 », ce qui est utile pour un score.
- **Comparaison** : légèrement meilleur que le LSTM (F1 0,907 contre 0,902), en dessous de BERT (0,930). Il n'a pourtant vu que 10 000 commentaires (contre 23 364 pour les deux autres) et son encodeur n'a pas été entraîné.
- **Limites** : c'est le plus lent des trois (10,3 ms par commentaire, environ 100 par seconde) et le plus lourd (840 Mo de poids). Entraîner aussi l'encodeur demanderait une carte avec plus de mémoire.

### 3. Comparaison des modèles

Résultats sur le même test (1 308 commentaires jamais vus à l'entraînement).

| Modèle | Précision | Rappel | F1        | AUC   | Faux positifs | Oubliés | Inférence | Poids  | Entraînement     |
| ------ | --------- | ------ | --------- | ----- | ------------- | ------- | --------- | ------ | ---------------- |
| LSTM   | 0,894     | 0,911  | 0,902     | –     | 69            | 57      | **0,4 ms** | **16 Mo** | rapide, 23 364 lignes |
| BERT   | **0,941** | **0,919** | **0,930** | **0,983** | **37**   | **52**  | 6,5 ms    | 268 Mo | 23 min, 23 364 lignes |
| LAYA   | 0,908     | 0,906  | 0,907     | 0,973 | 59            | 60      | 10,3 ms   | 840 Mo | 5 min, 10 000 lignes, encodeur gelé |

Les temps d'inférence ont été mesurés sur GPU, par lots.

**Test sur des phrases piégeuses** (score de 0 à 100, avec l'API) :

| Phrase                                     | LSTM     | LAYA    |
| ------------------------------------------ | -------- | ------- |
| « Thank you for your help, have a nice day » | 9,3    | 2,5     |
| « You are a wonderful person »             | **95,3** ❌ | **2,6** ✅ |
| « You are great »                          | 85,9 ❌  | –       |
| « you are a stupid fucking idiot »         | 100      | 99,2    |

**Bilan.**

- **BERT** a les meilleurs chiffres partout : meilleur F1, deux fois moins de faux positifs que le LSTM. Il comprend le contexte de la phrase. C'est le meilleur compromis qualité / coût parmi les modèles « lourds ».
- **LAYA** a des métriques proches du LSTM, mais il comprend mieux le sens : il ne tombe pas dans le piège « you are ». Ses probabilités sont les mieux calibrées (ECE 0,023) : un score de 90 veut vraiment dire « toxique 9 fois sur 10 ». C'est aussi le plus lent et le plus lourd. Ses chiffres sont sous-estimés, car il a été entraîné sur 10 000 lignes avec l'encodeur gelé.
- **LSTM** est imbattable en vitesse et en légèreté et tourne sur un simple CPU. Mais il juge sur les mots, pas sur le sens : il produit des faux positifs gênants sur des phrases gentilles.

**Quel modèle choisir ?**

| Besoin                                              | Modèle conseillé |
| --------------------------------------------------- | ---------------- |
| Petit budget cloud, CPU seul, gros volume de textes | **LSTM**         |
| Meilleure qualité de détection                      | **BERT**         |
| Un score de toxicité fiable, interprétable comme une probabilité | **LAYA** |

**Choix actuel pour l'API : le LSTM par défaut**, avec LAYA disponible via `"model": "laya"`. Le LSTM tourne sur un simple CPU, ce qui rend l'hébergement cloud peu coûteux, et l'image Docker reste légère. Pour un vrai service, il vaudrait mieux passer à **BERT** : 6,5 ms par commentaire reste rapide, et comme il tient compte du contexte, il devrait éviter les erreurs du LSTM. Ses poids ne sont pas disponibles sur ce poste : il n'a donc pas pu être testé sur ces phrases.

## Étape 3 : API et déploiement

Code : `src/api/main.py` (routes) et `src/api/predictor.py` (chargement du modèle). Tests : `tests/test_api.py`.

### 1. Fonctionnement

À chaque requête, le texte suit le même chemin que les données d'entraînement : **anonymisation** (regex + spaCy), puis **nettoyage**, puis **LSTM**. Le modèle chargé par l'API a 6 sorties, une par type de toxicité. Le score principal correspond à la probabilité `toxic`, multipliée par 100.

| Route          | Rôle                                         | Clé API |
| -------------- | -------------------------------------------- | ------- |
| `POST /score`  | texte → score de 0 à 100 et détail par type  | oui     |
| `GET /health`  | état du service                              | non     |
| `GET /metrics` | métriques Prometheus (requêtes, latence, scores) | non |
| `GET /docs`    | documentation interactive (Swagger)          | non     |

Exemple :

```bash
curl -X POST http://localhost:8000/score \
  -H "X-API-Key: test" -H "Content-Type: application/json" \
  -d '{"text": "Thank you for your help, have a nice day"}'
```

```json
{"score": 9.3, "toxic": false,
 "labels": {"toxic": 9.3, "severe_toxic": 1.5, "obscene": 9.8, "threat": 15.6, "insult": 28.9, "identity_hate": 11.5},
 "latency_ms": 12.85}
```

**Limite connue.** Le LSTM réagit aux mots plutôt qu'au sens. Les phrases qui commencent par « you are » sont jugées toxiques même quand elles sont gentilles : « You are great » obtient 85,9 et « You are a wonderful person » 95,3. Dans les données d'entraînement, cette tournure précède très souvent une insulte. De plus, le mot `person` y sert d'étiquette d'anonymisation et apparaît dans beaucoup de commentaires toxiques. BERT, qui tient compte du contexte, devrait moins se tromper sur ces cas.

**Tester LAYA.** Le champ `model` de `/score` permet de choisir le modèle : `"lstm"` (par défaut) ou `"laya"`. LAYA est chargé à sa première utilisation (quelques secondes) et nécessite le dossier `models/laya_binary/`. Il renvoie une seule probabilité, `toxic`. Il corrige l'erreur du LSTM : « You are a wonderful person » obtient 2,6 avec LAYA contre 95,3 avec le LSTM. Il est en revanche plus lent (environ 40 ms par requête sur GPU). L'image Docker ne contient pas LAYA : l'API y répond 503 si on le demande.

```bash
curl -X POST http://localhost:8000/score \
  -H "X-API-Key: test" -H "Content-Type: application/json" \
  -d '{"text": "You are a wonderful person", "model": "laya"}'
```

### 2. Sécurité et RGPD

- **Clé API obligatoire** dans l'en-tête `X-API-Key`, comparée en temps constant pour éviter les attaques par mesure du temps de réponse. Sans clé valide, l'API répond 401.
- **Validation des entrées** : texte de 1 à 5 000 caractères, sinon l'API répond 422.
- **Aucun texte stocké ni journalisé** : les logs ne contiennent qu'un hash salé du texte (pseudonyme), sa longueur, le score et la latence.
- **Anonymisation avant le modèle** : les données personnelles sont retirées du texte avant même d'arriver au modèle.
- **HTTPS** fourni par la plateforme cloud.
- **Conteneur non root** : l'image Docker tourne avec un utilisateur sans privilèges.

### 3. Docker et cloud

Le `Dockerfile` construit une image légère : Python 3.12 slim, PyTorch version CPU, et seulement le code de l'API avec les poids du LSTM. Les fichiers `.dockerignore` et `.gcloudignore` excluent les données, les rapports et les autres modèles (LAYA, BERT).

Variables d'environnement :

| Variable     | Rôle                                                    | Défaut |
| ------------ | ------------------------------------------------------- | ------ |
| `API_KEY`    | clé attendue dans `X-API-Key` (**obligatoire**)        | aucun  |
| `LOG_SALT`   | sel du hash écrit dans les logs                         | `dss`  |
| `USE_NER`    | `1` = anonymisation spaCy active, `0` = regex seules    | `1`    |
| `PORT`       | port d'écoute du conteneur                              | `8080` |

## Lancer le projet

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
python -m pytest tests
```

API en local (nécessite `models/lstm.pt` et `models/lstm_vocab.json`) :

```bash
pip install -r requirements-api.txt
set API_KEY=test                  # Windows (Linux/Mac : export API_KEY=test)
uvicorn src.api.main:app --reload # puis http://localhost:8000/docs
```

API avec Docker :

```bash
docker build -t digital-social-score .
docker run -p 8080:8080 -e API_KEY=test digital-social-score
```

Déploiement sur Google Cloud Run (l'image est construite par Cloud Build) :

```bash
gcloud builds submit --tag <REGION>-docker.pkg.dev/<PROJET>/<DEPOT>/digital-social-score
gcloud run deploy digital-social-score --image <REGION>-docker.pkg.dev/<PROJET>/<DEPOT>/digital-social-score \
  --region <REGION> --memory 2Gi --set-env-vars API_KEY=<CLE_SECRETE>
```
