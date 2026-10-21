# StockPilot AI — état du projet

État vérifié le **21 octobre 2026** : main distant à
`b6a170974c275d2f9602f6c9bc08aca5af8d7df0`, fusion réelle de la PR #53.
La branche par défaut GitHub reste dev ; les chantiers stables ciblent main.
Ce SHA est une preuve datée, jamais une invitation à synchroniser une démo.

## Produit actuel

MVP avancé local : Vue/FastAPI, LangGraph/Ollama, MongoDB replica set et
PostgreSQL/pgvector. Conseil, catalogue, stock, préparation/confirmation ORDER,
idempotence et annulation existent dans le parcours historique.
StockPilot n'est pas un SaaS prêt pour Internet ni une production commerciale.

| Lot intégré | Périmètre prouvé / limite | Référence |
|---|---|---|
| V13.4.3 | 85 assertions sur parcours synthétique avec Ollama réel, zéro erreur Vue ; preuve datée, pas un SLA ou français universel | [Validation](../validation/v13.4.3-real-user-journey.md) |
| V13.6 / #43 | Audit, nettoyage local limité et consolidation ; aucun déploiement implicite | [Rapport](../reports/v13.6-local-consolidation.md) |
| V13.7.1 / #44 | User/Organization/Membership/Principal et permissions distinctes de customer_id | [Identité](../security/b2b-identity-foundations.md) |
| V13.7.2 / #45 | Keycloak OIDC/PKCE et sessions BFF Mongo, mode b2b_auth_test uniquement | [Guide et limites](../security/keycloak-bff.md) |
| V13.7.3-A / #46 | Contexte serveur, permissions/ownership, associations acheteurs explicites | [Frontière](../security/tenant-authorization-boundary.md) |
| V13.7.3-B1 / #47 | Catalogue/stock B2B isolés, migration Mongo0001 | [ADR B1](../security/tenant-catalogue-stock.md) |
| V13.7.3-B2 / #48 | Commandes B2B, own=created_by, réservations/annulations atomiques, idempotence ; Mongo0002 | [ADR B2](../security/tenant-orders-atomicity.md) |
| V13.7.3-B3 / #49 | Ajustements quantity>=reserved, mouvements et imports atomiques bornés ; Mongo0003 | [ADR B3](../security/tenant-stock-operations.md) |
| V13.7.3-B4 / #50 | RAG, conversations privées et checkpoints B2B PostgreSQL ; aucune route métier BFF | [ADR B4](../security/tenant-rag-conversations.md) |
| V13.8.1 / #51 | BFF métier de test opt-in, sessions/CSRF/permissions et API B1/B2/B3 | [Contrat HTTP](../security/b2b-http-api.md) |
| V13.8.2 / #52 | Vue B2B cookie/session, catalogue/ORDER/administration jetables | [Validation](../validation/v13.8.2-unified-b2b-frontend.md) |
| V13.8.3 / #53 | Assistant LangGraph tenant-scoped, RAG et réconciliation Mongo/PG | [Validation](../validation/v13.8.3-unified-b2b-assistant.md) |

La chaîne de fusions #44→#53 est vérifiable dans l'historique first-parent de main.
Fusionner un port métier ne le rend pas accessible par Vue ou le BFF.

## B4 intégré — activation durable toujours bloquée

[ADR RAG/conversations](../security/tenant-rag-conversations.md) :
documents/embeddings tenant-scoped dans SQL avant tri vectoriel exact et avant
LLM, ingestion versionnée à publication atomique, conversations privées du créateur
et checkpoints dédiés, sans toucher l'historique.
[Migration PostgreSQL explicite](../postgresql-migrations.md), aucun setup automatique
ni migration de données existantes. [Rapport B4](../validation/v13.7.3b4-rag-conversations.md)
et [PR #50](https://github.com/WaelOuni/stockpilot-ai/pull/50) font autorité pour les tests réellement exécutés et la CI finale.

À la livraison B4, BFF auth/session uniquement. V13.8.1 ci-dessous prépare une
composition métier de test séparée ; production_saas et
production restent fail-closed. Les ports internes B1–B4 ne constituent pas un
parcours SaaS complet. Fenêtres de révocation inter-stores, sauvegarde/restauration,
quotas, exploitation, composition HTTP et UX Vue restent des gates distinctes.

## Démonstrations et cible produit

Une seule cible future : [StockPilot AI B2B Demo](../b2b-demo.md), Keycloak + BFF +
Vue, **installation officielle absente et non promue**. Le guide canonique définit les scénarios
et conditions de promotion, sans créer de quatrième installation persistante.

Installations locales protégées :
- Stable v017 : /home/waelko/deployments/stockpilot-demo-v017/, HTTPS5191.
- Candidate V13.3 : /home/waelko/deployments/stockpilot-candidate-v133/, HTTPS5193 ;
  dépendance /home/waelko/projects/stockpilot-v133-validation/.
- Historique : /home/waelko/projects/stockpilot-ai/, ports5175/8003, checkout
  volontairement à6f0949676e33a21c640b2ccc2f5877368a4dd0cb.

Au début de B4 : 28 conteneurs, cinq candidate actifs, 30 volumes ; stable et
historique déjà arrêtées. Ne pas les restaurer ou promouvoir automatiquement.
Préserver worktree V13.4.3, preuves non suivies disponibles, secrets, certificats,
volumes et images de rollback. Le retrait ultérieur exige sauvegarde, nouvelle
démo validée et autorisation explicite.

## Prochaines étapes

1. Kit commercial #42 fusionné à b11560c, sans changement des contrats B2B.
2. V13.8.2/#52 intégrée ; revoir V13.8.3 puis préparer V13.8.4 et la restauration.
3. Environnement pilote distant dédié, restauration éprouvée et décision d'accès.
4. Kubernetes/K3s seulement si justifié ; Compose reste l'hypothèse du premier pilote.

[Roadmap](ROADMAP.md), [décisions durables](ARCHITECTURE_DECISIONS.md),
[handoff court](AGENT_HANDOFF.md). Les ADR, validations et rapports historiques
restent conservés ; ils décrivent leur date, pas automatiquement l'état déployé.

## Kit commercial réconcilié — PR #42

Fiche produit, messages non envoyés, proposition de pilote, entretien et indicateurs
restent des supports de découverte, sans prix accepté ni gain client mesuré.
Le script commercial décrit le parcours historique sur copie autorisée ; il ne
remplace pas le guide canonique de la future démo Keycloak. Aucun contact,
nouvelle installation ou pilote n'est réalisé par cette consolidation.


## V13.8.1 — composition HTTP intégrée (#51)

Mode b2b_business_test distinct, disabled par défaut : sessions/CSRF/Origin,
TenantContext serveur et routes dédiées B1/B2/B3. Liste des commandes et acheteurs
scoppée, réconciliation sans nouvelle clé, prix minor-unit exacts.
[Contrat API](../security/b2b-http-api.md), [preuves](../validation/v13.8.1-secure-b2b-http.md).
Aucune route assistant/RAG, migration historique ou activation publique.
La démonstration officielle unifiée n’est toujours ni opérationnelle ni promue.

## V13.8.2 — interface entreprise intégrée (#52)

Base distante et fusion #51 vérifiées à1c9f41c (21 octobre2026), V13.8.1 intégrée.
Vue /b2b-app/ séparée du parcours JWT historique : session/Keycloak, catalogue,
commandes explicites, réconciliation, stocks/mouvements/CSV selon permissions.
[Guide utilisateur](../b2b-user-guide.md), [validation](../validation/v13.8.2-unified-b2b-frontend.md).
Aucun token OIDC browser ; BFF et données d’autorisation restent l’autorité.
Campagne Chromium/Keycloak/BFF/Mongo HTTPS jetable, sans installation ni déploiement.
À sa livraison, l’assistant était réservé àV13.8.3, intégrée depuis par #53 ; la [démo canonique](../b2b-demo.md) n’est
pas promue, aucun SaaS public/multi-tenant prêt pour Internet n’est annoncé.

## V13.8.3 — assistant intégré (#53)

Assistant LangGraph opt-in dans b2b_business_test ; [contrat](../security/b2b-assistant.md),
[validation](../validation/v13.8.3-unified-b2b-assistant.md). Vue privée, catalogue
vérifié, RAG SQL-scoped avant LLM, préparation B2 sans réservation et confirmation
HTTP explicite. Journal PG0002 avant Mongo et réconciliation à clé constante.
Aucun déploiement, migration historique ou ouverture Internet.
Prochaine gate : V13.8.4, installation canonique isolée et backup/restore éprouvé.

## V13.8.4 — composition et reprise en revue

[Guide canonique unique](../b2b-demo.md) : opérateur validate seulement,
proxy HTTPS, provisioning synthétique, sauvegardes Mongo/PG/Keycloak et
restauration indépendante. [Rapport et limites](../validation/v13.8.4-canonical-b2b-demo.md).
Aucun mode durable ou Internet n’est activé ; les garde-fous de bases de test
restent obligatoires. Promotion et retrait des anciennes démos restent soumis
à une décision humaine distincte.
