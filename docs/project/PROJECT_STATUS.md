# StockPilot AI — état du projet

État vérifié le **21 octobre 2026** : main distant à
`394bd7cebd1ae807bdac8478c10ebc84ab3ad8f9`, fusion réelle de la PR #50.
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

La chaîne de fusions #44→#50 est vérifiable dans l'historique first-parent de main.
Fusionner un port métier ne le rend pas accessible par Vue ou le BFF.

## B4 intégré — activation métier toujours bloquée

[ADR RAG/conversations](../security/tenant-rag-conversations.md) :
documents/embeddings tenant-scoped dans SQL avant tri vectoriel exact et avant
LLM, ingestion versionnée à publication atomique, conversations privées du créateur
et checkpoints dédiés, sans toucher l'historique.
[Migration PostgreSQL explicite](../postgresql-migrations.md), aucun setup automatique
ni migration de données existantes. [Rapport B4](../validation/v13.7.3b4-rag-conversations.md)
et [PR #50](https://github.com/WaelOuni/stockpilot-ai/pull/50) font autorité pour les tests réellement exécutés et la CI finale.

BFF auth/session uniquement ; aucune nouvelle route métier. production_saas et
production restent fail-closed. Les ports internes B1–B4 ne constituent pas un
parcours SaaS complet. Fenêtres de révocation inter-stores, sauvegarde/restauration,
quotas, exploitation, composition HTTP et UX Vue restent des gates distinctes.

## Démonstrations et cible produit

Une seule cible future : [StockPilot AI B2B Demo](../b2b-demo.md), Keycloak + BFF +
Vue, **non opérationnelle et non promue**. Le guide canonique définit les scénarios
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

1. Réconcilier le [kit commercial #42](../product/README.md) sans modifier les contrats B2B.
2. Cadrer V13.8 : composition HTTP B2B sécurisée, UX Vue puis assistant et E2E.
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
