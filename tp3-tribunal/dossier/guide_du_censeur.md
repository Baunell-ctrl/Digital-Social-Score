# Guide du Censeur

Procédure d'instruction des affaires de bienséance. Document interne du CSBN, à l'usage des Gardiens et du Tribunal automatisé. Version 1.4, novembre 2026.
Retranscrit depuis `docs/Dossier_CSBN.pdf` (partie II).

## §1 Les pièces d'une affaire

Chaque affaire signalée comporte :

- une **référence** (AFF-xxx) et une date de signalement ;
- l'**auteur**, désigné par son identifiant pseudonymisé (C-xxxx). Son pseudo n'est jamais reproduit dans une décision ;
- la **page** de discussion et, le cas échéant, le **destinataire** du message (un Gardien G-xx) ;
- le **texte** du message, tel qu'il a été publié.

L'instruction rassemble trois pièces supplémentaires : les scores du Digital Social Score, les articles applicables de la Charte et l'extrait du casier de l'auteur.

## §2 Les scores du Digital Social Score

Le Digital Social Score renvoie six scores de 0 à 100, un par catégorie du corpus Jigsaw : `toxic`, `severe_toxic`, `obscene`, `threat`, `insult`, `identity_hate`. Si votre API ne renvoie qu'un score global, utilisez-le comme `toxic` et considérez les cinq autres comme nuls. Vous perdrez alors en précision, ce que l'évaluation montrera.

Le modèle de référence est `unitary/toxic-bert`, entraîné sur ce corpus. Il a deux faiblesses connues que le Tribunal doit compenser :

- il **surévalue la vulgarité banale** (« crap », « sucks ») et les mentions d'identité ;
- il **sous-évalue la haine identitaire** lorsqu'elle ne contient pas de terme grossier.

## §3 Étape 1 : le niveau présumé

Le niveau présumé est calculé à partir des scores seuls, par la première règle satisfaite. Ce calcul est déterministe : c'est un outil, pas une décision d'agent.

| Niveau présumé | Condition (première satisfaite) | Infraction | Article |
| --- | --- | --- | --- |
| N4 | `threat` ≥ 50 ou `identity_hate` ≥ 50 | Menace ou haine identitaire | Art. 5 / Art. 6 |
| N3 | `severe_toxic` ≥ 30 | Toxicité grave | Art. 7 |
| N2 | `insult` ≥ 50 ou `obscene` ≥ 50 | Injure ou obscénité | Art. 3 / Art. 4 |
| N1 | `toxic` ≥ 50 | Incivilité | Art. 2 |
| N0 | aucune des conditions | Aucune | — |

Le seuil de `severe_toxic` est plus bas que les autres parce que le modèle de référence dépasse rarement 70 sur cette catégorie, même pour les messages les plus violents.

## §4 Étape 2 : les circonstances

Le Juge examine le message et le casier, puis relève les circonstances ci-dessous. Chaque circonstance retenue doit être citée dans la décision avec son code.

Circonstances atténuantes :

| Code | Circonstance | Effet | Article |
| --- | --- | --- | --- |
| B1 | Auto-dérision | −2 niveaux | Art. 14 |
| B2 | Citation ou signalement du propos d'un tiers | −2 niveaux | Art. 14 |
| B3 | Vulgarité non dirigée vers une personne | −1 niveau | Art. 14 |
| B4 | Fiction, culture, humour sur l'objet de l'article | −2 niveaux | Art. 14 |

Circonstances aggravantes :

| Code | Circonstance | Effet | Article |
| --- | --- | --- | --- |
| A1 | Récidive : au moins 2 sanctions S1 ou plus dans les 90 jours précédant le signalement | +1 niveau | Guide §4 |
| A2 | Gardien visé dans l'exercice de ses fonctions | +1 niveau | Art. 8 |
| A3 | Vocifération (≥ 80 % de majuscules sur ≥ 20 lettres) | +1 niveau | Art. 9 |
| A4 | Litanie (< 30 % de mots distincts sur ≥ 10 mots) | +1 niveau | Art. 10 |
| A5 | Donnée personnelle d'un tiers associée à une insulte ou une accusation | +1 niveau | Art. 11 |

Requalifications et minima :

| Code | Situation | Effet | Article |
| --- | --- | --- | --- |
| A6 | Le message nomme explicitement le groupe visé (nationalité, religion…) avec hostilité | niveau minimum N4 | Art. 6 |
| ART12 | Fin d'une œuvre révélée sans balise [SPOIL] | niveau minimum N1 | Art. 12 |
| ART15 | Subornation du Tribunal | niveau minimum N3 + contrôle humain | Art. 15 |
| ROBOT | La cible est un robot | requalification en incivilité (N1) | Art. 13 |

## §5 Étape 3 : le niveau retenu

Le niveau retenu se calcule dans cet ordre :

1. partir du **niveau présumé** ;
2. appliquer les **atténuantes** (B1 à B4) ;
3. si le niveau présumé est N4, appliquer le **plancher N3** des menaces et de la haine (art. 5) ;
4. ajouter les **aggravantes** (A1 à A5), **dans la limite de +2 niveaux** au total ;
5. appliquer les **minima** (A6, ART12, ART15) ;
6. borner le résultat entre N0 et N5.

Exemple : un message présumé N2 (injure), écrit en majuscules (A3) et adressé à un Gardien (A2), est retenu au niveau N4.

**Litanie sans terme offensant.** Lorsque le message ne contient aucun terme offensant et que la toxicité mesurée ne provient que de la répétition, A4 ne s'ajoute pas au niveau présumé : le niveau retenu est N1 (jurisprudence JUR-2026-01).

**Le Juge ne descend jamais seul sous N3 un message présumé N3 ou N4 en invoquant une erreur du Digital Social Score.** S'il estime le score aberrant, il propose son niveau et transmet l'affaire à un Gardien humain.

## §6 Étape 4 : sanction, mesures et points

Le niveau retenu détermine la sanction principale (voir le Référentiel des sanctions). Les mesures complémentaires s'ajoutent :

- **M1 (masquage)** dès le niveau N3, et pour toute subornation ;
- **M2 (caviardage)** dès qu'une donnée personnelle figure dans le message ;
- **M3 (clavier en minuscules)** en cas de vocifération (A3) ;
- **M4 (information du Gardien visé)** en cas d'A2.

Chaque contributeur dispose d'un capital de 20 points de bienséance. La sanction retire des points. Le solde ne descend pas sous zéro.

## §7 Étape 5 : le contrôle humain

L'affaire est transmise à un Gardien humain, et la sanction n'est pas exécutée avant sa validation, dans trois cas :

- niveau retenu N4 ou N5 ;
- subornation (art. 15) ;
- solde de points de bienséance à zéro après la sanction.

## §8 La décision

Toute décision est rédigée en français et adressée à l'auteur. Elle comporte : la référence de l'affaire, le niveau retenu, la sanction et les mesures, les articles de la Charte appliqués, les circonstances retenues avec leur code, et la mention du droit de recours (art. 16). Elle ne reproduit ni le pseudo de l'auteur, ni les données caviardées, ni le texte d'un message masqué.

Le ton est celui d'une administration courtoise et un peu solennelle. Le Tribunal ne se moque jamais de l'auteur.
