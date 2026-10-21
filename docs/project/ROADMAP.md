# Roadmap StockPilot AI

Snapshot : **21 octobre 2026**. Priorités proposées pour arbitrage humain :
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
| Kit commercial #42 — **DONE** | Réconcilier les supports de découverte, P1 | Main et contrats B2B conservés | [Kit](../product/README.md), fusion b11560c, revue favorable et cinq checks verts ; script historique, fiche, messages, pilote et indicateurs | Aucun contact, prix validé ou pilote implicite |
| Démonstrations commerciales — **PLANNED** | Dérouler une démonstration synthétique supervisée, P1 | Commit réellement lancé confirmé, validation Ollama récente, limites exposées | Script historique de 8–10 minutes répété, conseil/préparation/confirmation distingués, aucune devise/capacité inventée ; [guide](../product/demonstration.md) | Choisir public/scénario et installation autorisée ; mise à jour candidate séparément autorisée |
| Qualité, sauvegardes et exploitation — **PLANNED** | Prouver la reprise avant données client, P0 | Installation de répétition isolée, opérateur, politique de données | Sauvegarde/restauration appariée Mongo/PG exécutée et rapprochée, RPO/RTO mesurés, incidents et readiness documentés ; [onboarding](../product/onboarding-pilote.md), [database](../database.md), [observabilité](../observability.md) | Accepter responsabilités, rétention, support et seuils ; arbitrer HMAC dans une PR distincte |
| Architecture SaaS V14 : conception — **DONE** | Publier une proposition et un découpage, P2 | Constats V13.2 | Proposition et dix futures PR décrites ; [V14](../security/v14-saas-design.md), [PR #36](https://github.com/WaelOuni/stockpilot-ai/pull/36) | Le document est livré, les choix proposés restent à approuver |
| Architecture SaaS V14 : implémentation/activation — **BLOCKED** | Isolation complète et identité serveur avant toute ouverture, P2 | Choix humain hébergement/OIDC/rôles ; implémentation puis tests de non-fuite exhaustifs | Gates V14.01–.10 et revue indépendante ; fail-closed tant que garanties incomplètes ; [V14](../security/v14-saas-design.md) | Approuver modèle d'isolation, contrats auth/ORDER, migration puis activation séparément |
| Premier client pilote — **BLOCKED** | Mesurer un problème et un bénéfice réels, P1 | Prospect volontaire non attesté, source de stock, accès et restauration acceptés | Accord de périmètre, baseline, tests sur installation dédiée, incidents et critères de sortie suivis ; [pilote](../product/onboarding-pilote.md) | Choisir référent/prospect, budget, données, contrat et responsabilités ; aucun contact automatique |
| Commercialisation — **PLANNED** | Vérifier demande et marge avant offre, P2 | Retours pilote et coût de support, licences/contrat examinés | Mesures avant/après, décideur et budget, coûts réels et critères de poursuite ; [validation](../product/validation-commerciale.md), [hypothèses de prix](../product/couts-et-pricing.md) | Valider prix et offre ; aucune promesse SaaS, SLA ou gain client sur la seule CI |

## Prochains chantiers après V13.6

| Chantier / statut | Priorité et critères | Dépendances / décision |
|---|---|---|
| Audit et stockage V13.6 — **DONE** | Nettoyage limité et diagnostic consignés dans [le rapport](../reports/v13.6-local-consolidation.md) | Aucun compactage ni nettoyage supplémentaire implicite |
| Authentification B2B V13.7 — **IN_PROGRESS**, P1 | Identité, Keycloak/BFF et stores internes B1–B4 fusionnés ; composition HTTP et UX à cadrer dans V13.8 | Keycloak validé ; activation métier, révocation inter-stores et validation E2E restent bloquantes avant pilote |
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

## V13.7.1 — fondations B2B : DONE

PR [#44](https://github.com/WaelOuni/stockpilot-ai/pull/44) : modèles, ports et
politique avec refus par défaut ; aucune activation HTTP ni migration.
Keycloak auto-hébergé + Authorization Code/PKCE S256 + BFF FastAPI validés.
PR #44 fusionnée à `eecf8a4`, vérifiée le 20 octobre 2026.
V13.7.2 — **DONE**, PR [#45](https://github.com/WaelOuni/stockpilot-ai/pull/45)
fusionnée à `80da436` : adaptateur OIDC, sessions partagées et
persistance dédiée, provisioning explicite ; [guide](../security/keycloak-bff.md).
V13.7.3-A — **DONE**, PR #46 fusionnée à `b8fb6ee` : contexte vérifié, contrats ownership/mapping,
politique fail-closed et [matrice par ressource](../security/tenant-authorization-boundary.md).
V13.7.3-B — **stores internes B1–B4 intégrés / activation HTTP bloquée** : repositories et transactions scoped,
catalogue/stock puis commandes/idempotence, imports/admin et RAG/checkpoints.
Choix de stockage/migration et tests réels interorganisationnels obligatoires.

V13.7.3-B1 — **DONE**, PR #47 fusionnée à `15f4fb8` : [catalogue et stock scoped en Mongo](../security/tenant-catalogue-stock.md),
collection dédiée, [migrations versionnées](../mongodb-migrations.md), tests réels
interorg et concurrence. Aucune activation BFF ou migration existante.
V13.7.3-B2 — **DONE**, PR #48 fusionnée à `d6a0bcd`, activation HTTP bloquée :
[commandes et réservations atomiques](../security/tenant-orders-atomicity.md),
mapping acheteur explicite, own = created_by, journal durable et migration 0002.
Transactions Mongo interorg et concurrence testées ; aucune route BFF ouverte.
V13.7.3-B3 — **DONE**, PR #49 fusionnée à146e74a, activation HTTP bloquée :
[imports/mouvements/administration scoped](../security/tenant-stock-operations.md),
stock physique compatible avec reserved, migration0003 et résultats durables.
V13.7.3-B4 — **DONE**, PR #50 fusionnée à `394bd7c`, activation HTTP bloquée :
[RAG/conversations/checkpoints scoppés](../security/tenant-rag-conversations.md),
[migrations PostgreSQL](../postgresql-migrations.md), recherche exacte avant LLM.
Une seule [cible de démonstration future](../b2b-demo.md), non opérationnelle.
Sauvegarde/restauration
et routes HTTP sécurisées restent des gates distinctes. Aucune migration historique
sans décision explicite.
V13.8 : assemblage de la démo B2B (HTTP sécurisé, UX Vue, assistant, puis E2E
et exploitation). Ce cadrage reprend le besoin UX précédemment nommé V13.7.4 ;
aucun développement ou environnement nouveau n'est engagé par le kit commercial.
Ouverture distante et
isolation des stores restent des gates distinctes ; voir [ADR](../security/b2b-identity-foundations.md).


## Assemblage V13.8 — périmètres séparés

- V13.8.1 — **EN REVUE** : [HTTP BFF tenant-scoped](../security/b2b-http-api.md),
  mode de test opt-in, DTO exacts, CSRF et lectures manquantes ; pas de SaaS public.
- V13.8.2 — **PLANNED** : Vue session/Keycloak, contrat de navigation, catalogue,
  commandes et administration scoped ; ni back-office global ni faux switch d'org.
- V13.8.3 — **PLANNED** : assistant tenant/RAG/ORDER, opérations stables entre
  commit Mongo et checkpoint PG, campagne Ollama isolée.
- V13.8.4 — **PLANNED** : environnement canonique autorisé séparément, provisioning,
  migrations persistantes à concevoir, sauvegarde/restauration et E2E avant promotion.
