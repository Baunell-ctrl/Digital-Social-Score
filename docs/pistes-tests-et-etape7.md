# Pistes : tests d'attaque complémentaires et étape 7

Document de préparation. Rien de ce qui suit n'est encore mis en place.

## 1. Batterie de tests : attaques « discrètes »

Le script d'Aurel (`loadtest/attack.py`) couvre déjà les attaques **bruyantes** : grille de l'étape 4, fuzzing, force brute rapide (2 essais/s), extraction en rafale (429). Ces quatre tests visent l'inverse : des attaques qui **ne font pas monter le CPU** et restent **sous les seuils d'alerte**. La question n'est plus « le système résiste-t-il ? » mais « **le voit-on ?** ».

| # | Test | Sans clé API ? | Ce que fait l'attaquant | Ce qu'on vérifie |
| - | ---- | -------------- | ----------------------- | ---------------- |
| 1 | Force brute lente | oui | 10 essais de clés par minute pendant 30 min (300 essais), d'une seule source | l'alerte « Brute force » (> 20 échecs/min) se déclenche-t-elle ? |
| 2 | Client abusif sous la limite | non (clé valide) | une seule clé à environ 80 % de la limite réelle (600/min **par pod**) pendant 10 min | un seul client qui fait l'essentiel du trafic est-il repéré, sans aucun 429 ? |
| 3 | Attaque sur Grafana | oui | 30 mots de passe essayés sur `/grafana/login` (comptes `admin` et `lecteur`) | Grafana bloque-t-il ? Quelqu'un le voit-il ? |
| 4 | Reconnaissance et connexions lentes | oui | requêtes vers des chemins courants (`/.env`, `/admin`, `/wp-login.php`, `/grafana/api/admin/...`), puis 50 connexions qui envoient leur requête très lentement | aucune fuite d'information ; le service reste rapide pour les vrais utilisateurs |

### Détail

**Test 1 : force brute lente.**
- *Mise en place* : nouvelle phase `bruteforce-lente` dans `attack.py`, avec un débit réglable.
- *Mesures* : panneau « Échecs d'authentification », logs `auth_failed` regroupés par `ip_id`.
- *Résultat probable* : **aucune alerte**, car le seuil est par minute. L'attaque reste visible dans les logs (300 lignes, même `ip_id`).
- *Piste* : alerte sur un cumul (plus de 100 échecs en 1 heure), ou métrique basée sur les logs par `ip_id` ; blocage automatique d'IP avec Cloud Armor.

**Test 2 : client abusif sous la limite.** C'est le scénario « copie du modèle », fait patiemment.
- *Mise en place* : phase `extraction-lente` dans `attack.py`.
- *Mesures* : débit total, réponses 429 (il ne doit y en avoir aucune), CPU.
- *Résultat probable* : **invisible**. L'alerte « Extraction » n'existe que s'il y a des 429, et les métriques HTTP n'ont pas d'étiquette par client.
- *Piste* : compteur `dss_requests_total{client}` dans l'API, alerte « un client fait plus de 50 % du trafic » ou quota journalier par clé, limite globale avec Cloud Armor.

**Test 3 : attaque sur Grafana.** Grafana est la nouvelle surface publique, et elle n'est pas protégée par la clé API.
- *Mise en place* : quelques requêtes `POST /grafana/login` avec de mauvais mots de passe.
- *Mesures* : réponse de Grafana après plusieurs échecs (blocage temporaire du compte ?), logs du load balancer (codes 401 sur `/grafana/login`).
- *Résultat probable* : Grafana bloque le compte après quelques échecs, mais **aucune de nos alertes ne surveille Grafana**.
- *Piste* : alerte sur les 401 de `/grafana` (logs du load balancer), Cloud Armor ou IAP (Identity-Aware Proxy, qui demande un nom de domaine propre), ou limiter l'accès à des adresses IP connues.

**Test 4 : reconnaissance et connexions lentes.**
- *Mise en place* : liste de chemins, puis un petit script qui ouvre des connexions et envoie leur corps octet par octet.
- *Mesures* : codes reçus (404 ou 401 attendus, jamais 500 ni de fichier servi), latence d'une requête légitime pendant les connexions lentes.
- *Résultat probable* : rien ne fuit (`/.env` est exclu de l'image, les polices sont le seul dossier servi). Les connexions lentes devraient être absorbées par le load balancer de Google, qui reçoit la requête complète avant de la transmettre. À vérifier.
- *Piste* : alerte sur un pic de 404 venant d'une même source (logs du load balancer), règles de détection de scanners de Cloud Armor, couper `/docs` en production.

**Pour chaque test** : noter l'heure de début et de fin (comme `attack.py`), faire une capture de Grafana et des logs, et remplir une grille « attendu / obtenu / détecté (oui/non, par quoi) / temps de détection ».

## 2. Étape 7 : pistes pour la modélisation de l'infrastructure

Ce que le TP demande : lister les composants, dessiner les flux, les authentifications et le chiffrement, justifier chaque composant (approche **zero trust**), écrire un texte explicatif et une légende.

**Base déjà disponible** : la section « Vue d'ensemble : architecture et connexions » du README, avec un schéma texte et un tableau des connexions (comment, qui s'authentifie, chiffrement).

### Plan proposé

1. **Inventaire des composants.** Une ligne par composant : rôle, service Google Cloud, pourquoi ce choix, alternative écartée.
   - Load balancer et certificat, pods de l'API et HPA, Grafana, Prometheus géré (GMP), alertes, Cloud Logging.
   - Secret Manager, Artifact Registry, Cloud Build, IAM et Workload Identity, KMS (prévu).
   - Point fort à souligner : **aucun stockage de données des utilisateurs**. Le modèle est dans l'image Docker, les textes ne sont jamais écrits.

2. **Schéma.** Dessiné dans draw.io (diagrams.net), avec les icônes officielles Google Cloud.
   - Trois zones emboîtées : Internet, le projet Google Cloud (région Paris), le cluster GKE.
   - Flux numérotés, avec trois couleurs : données des utilisateurs, supervision, déploiement.
   - Un cadenas sur chaque flux chiffré, une clé sur chaque flux authentifié.

3. **Zero trust : « ne jamais faire confiance, toujours vérifier ».** Pour chaque flux, ce qui est vérifié.
   - Ce qui est en place : clé API, compte Grafana, Workload Identity, rôles IAM minimaux, pods sans adresse publique.
   - Les écarts, à dire honnêtement :
     - load balancer → pods en HTTP sur le réseau de Google ;
     - **clé KMS non activée** ;
     - Grafana protégé par un simple mot de passe ;
     - pas de `NetworkPolicy` (tout pod du cluster peut joindre l'API) ;
     - clés API sans expiration ;
     - pas de Cloud Armor.
   - Pistes pour chaque écart : activer KMS, `NetworkPolicy` (Dataplane V2 est déjà actif sur le cluster), IAP devant Grafana, rotation des clés, Cloud Armor.

4. **Texte explicatif et légende.**
   - Un paragraphe qui suit une requête de bout en bout : le navigateur, le load balancer, un pod, l'anonymisation, le LSTM, la réponse, puis la ligne de log et la métrique.
   - Un paragraphe sur la supervision, un sur la sécurité et le RGPD.
   - La légende explique les couleurs, les icônes et les numéros.

5. **Questions probables à l'oral**, avec une réponse courte à préparer pour chacune :
   - Pourquoi GKE Autopilot plutôt que Cloud Run ?
   - Pourquoi une clé API et pas un JWT ?
   - Où sont les données personnelles ?
   - Que se passe-t-il si un pod ou une zone tombe ?
   - Comment détecte-t-on une attaque ?
   - Combien ça coûte ?
   - Pourquoi la région de Paris ?
