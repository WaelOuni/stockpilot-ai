# Roadmap StockPilot AI

Snapshot : **20 octobre 2026**. Priorités proposées pour arbitrage humain :
**P0** sûreté avant données réelles, **P1** prochaine validation utile,
**P2** évolution après preuves. Pas de date de livraison engagée.
DONE = périmètre documenté terminé ; IN_PROGRESS = chantier actif vérifié ;
PLANNED = prochaine étape proposée ; BLOCKED = prérequis explicite non satisfait.
Une proposition documentaire terminée ne rend pas sa fonctionnalité opérationnelle.

| Chantier / statut | Objectif et priorité | Dépendances | Acceptation et preuve / PR | Décision humaine nécessaire |
|---|---|---|---|---|
| Recherche naturelle bornée — **DONE** | Corriger les formulations couvertes et les plafonds stricts, P1 | Catalogue vérifié, gardes stock/ORDER | Matrice graphe/Mongo/navigateur et CI #39 réussies ; [V13.4.2](../validation/v13.4.2-natural-search.md), [PR #39](https://github.com/WaelOuni/stockpilot-ai/pull/39) | Toute extension linguistique doit être cadrée ; DONE ne signifie pas français universel |
| Validation fonctionnelle Ollama réel post-#41 — **DONE sur le périmètre V13.4.3** | Vérifier compréhension, latence, ambiguïtés et suites conversationnelles, P1 | Commit et modèles identifiés, services/données jetables explicitement vérifiés | 85 assertions réussies sur catalogue synthétique isolé, conseil puis ORDER confirmé/annulé ; [V13.4.3 et limites](../validation/v13.4.3-real-user-journey.md). Campagne du 20 octobre, non rejouée par V13.6.4 | Toute extension ou répétition réelle exige un environnement isolé et une charge Ollama autorisés ; aucun accès DEMO implicite |
| Mémoire et pilotage V13.5 — **DONE** | Quatre points d'entrée maintenables, P1 | Documentation et GitHub recoupés | Liens valides, décisions/propositions séparées, revue indépendante, PR vers main et CI ; [état](PROJECT_STATUS.md), PR de la branche `docs/v135-project-memory` | PR #40 fusionnée, commit `01f1566` vérifié ; conserver les snapshots datés |
| Démonstrations commerciales — **PLANNED** | Dérouler une démonstration synthétique supervisée, P1 | Commit réellement lancé confirmé, validation Ollama récente, limites exposées | Script de 8 minutes répété, conseil/préparation/confirmation distingués, aucune devise/capacité inventée ; [guide](../product/demonstration.md) | Choisir public/scénario et installation autorisée ; mise à jour candidate séparément autorisée |
| Qualité, sauvegardes et exploitation — **PLANNED** | Prouver la reprise avant données client, P0 | Installation de répétition isolée, opérateur, politique de données | Sauvegarde/restauration appariée Mongo/PG exécutée et rapprochée, RPO/RTO mesurés, incidents et readiness documentés ; [onboarding](../product/onboarding-pilote.md), [database](../database.md), [observabilité](../observability.md) | Accepter responsabilités, rétention, support et seuils ; arbitrer HMAC dans une PR distincte |
| Architecture SaaS V14 : conception — **DONE** | Publier une proposition et un découpage, P2 | Constats V13.2 | Proposition et dix futures PR décrites ; [V14](../security/v14-saas-design.md), [PR #36](https://github.com/WaelOuni/stockpilot-ai/pull/36) | Le document est livré, les choix proposés restent à approuver |
| Architecture SaaS V14 : implémentation/activation — **BLOCKED** | Isolation complète et identité serveur avant toute ouverture, P2 | Choix humain hébergement/OIDC/rôles ; implémentation puis tests de non-fuite exhaustifs | Gates V14.01–.10 et revue indépendante ; fail-closed tant que garanties incomplètes ; [V14](../security/v14-saas-design.md) | Approuver modèle d'isolation, contrats auth/ORDER, migration puis activation séparément |
| Premier client pilote — **BLOCKED** | Mesurer un problème et un bénéfice réels, P1 | Prospect volontaire non attesté, source de stock, accès et restauration acceptés | Accord de périmètre, baseline, tests sur installation dédiée, incidents et critères de sortie suivis ; [pilote](../product/onboarding-pilote.md) | Choisir référent/prospect, budget, données, contrat et responsabilités ; aucun contact automatique |
| Commercialisation — **PLANNED** | Vérifier demande et marge avant offre, P2 | Retours pilote et coût de support, licences/contrat examinés | Mesures avant/après, décideur et budget, coûts réels et critères de poursuite ; [validation](../product/validation-commerciale.md), [hypothèses de prix](../product/couts-et-pricing.md) | Valider prix et offre ; aucune promesse SaaS, SLA ou gain client sur la seule CI |

## Prochains chantiers après V13.6

| Chantier / statut | Priorité et critères | Dépendances / décision |
|---|---|---|
| Audit et stockage V13.6 — **DONE** | Nettoyage limité et diagnostic consignés dans [le rapport](../reports/v13.6-local-consolidation.md) | Aucun compactage ni nettoyage supplémentaire implicite |
| Authentification B2B V13.7 — **PLANNED**, P1 | Auditer identité serveur, sessions, autorisations, distinction client/organisation et tests de non-fuite ; livrer constats et proposition | Choix humain du fournisseur d'identité et du périmètre avant implémentation ; aucun SaaS activé |
| Environnement de test distant — **PLANNED**, P1 | Définir isolation, données synthétiques, accès, sauvegarde/restauration, observabilité et coût | Serveur, budget et politique d'accès à choisir ; ne pas déplacer les démos existantes |
| Kubernetes/K3s — **ENVISAGÉ**, P2 | Comparer utilité et coût opérationnel avec une implantation Compose dédiée | Aucune implémentation ; décision après besoins et contraintes du serveur distant |

## Synchronisation légère

Mettre à jour uniquement les lignes concernées, dans la PR du chantier si possible
ou dans un bref suivi après constat de fusion ; pas de réécriture générale ni de CI
supplémentaire. Le responsable du chantier ajoute date, preuve et limite :

- Fusion constatée : statut DONE et PR/rapport, puis état produit si son comportement change.
- Décision humaine validée : entrée motivée dans [les décisions](ARCHITECTURE_DECISIONS.md),
  avec lien vers la validation ; une proposition reste marquée comme telle.
- Candidate/release : rapport daté avec **commit application effectivement déployé**,
  version des scripts si différente, vérification de provenance et tests. Une PR
  fusionnée seule ne constitue pas une preuve de déploiement.
- Anomalie importante : symptôme, impact, cause, résolution ou limite ouverte dans
  le rapport de validation du chantier ; lier le risque ici ou dans l'état projet.
- Priorités modifiées : actualiser cette table et noter l'arbitrage humain associé.

Les contrats détaillés restent dans leurs guides et les preuves dans
`docs/validation/`. Ne jamais recopier secrets, JWT ou données privées dans le suivi.
