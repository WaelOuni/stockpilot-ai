---
name: stockpilot-backend
description: "Implémenter ou diagnostiquer les API FastAPI/Pydantic, cas métier, ports et adaptateurs MongoDB/PostgreSQL de StockPilot AI. Utiliser pour validation stricte, persistance ou migration backend ; pas pour une retouche Vue, le tuning LLM ou une opération de déploiement."
---

# Backend StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md). Identifier le parcours historique ou B2B,
l'entrée HTTP, le port et l'adaptateur concernés avant toute édition. Réutiliser
les dépendances verrouillées ; installer le package dans le venv de ce worktree,
jamais un editable pointant vers un autre checkout.

## Workflow
1. Partir des [routes](../../../src/stockpilot/presentation/api/routes/) et, en B2B,
   des [schémas stricts](../../../src/stockpilot/presentation/api/schemas/b2b.py).
   Vérifier types, bornes, champs supplémentaires et réponses ; bool/string/float
   ne deviennent pas des quantités entières par coercition.
2. Mettre la règle métier dans le use case/service existant ; adapter HTTP mince,
   domaine sans driver. Lire les [ports](../../../src/stockpilot/application/ports/)
   et les [adaptateurs](../../../src/stockpilot/infrastructure/persistence/) correspondants.
   Conserver les prix B2B en unités mineures exactes et leur transport en chaînes ;
   ne pas appliquer au catalogue historique un contrat monétaire B2B supposé.
3. Si ORDER est explicitement dans la demande, lire [ORDER historique](../../../docs/order-flow.md)
   et [ORDER B2B](../../../docs/security/tenant-orders-atomicity.md).
   Préparation sans réservation, preuve persistée et confirmation explicite ;
   transaction stock/commande/journal, replay lié au même acteur et payload.
   En B2B, stock disponible = quantity - reserved. Une issue inconnue se réconcilie
   avec la clé d'origine ; un timeout ou un 404 ne prouve pas un rollback.
4. Faire traverser l'organisation vérifiée côté serveur jusqu'aux requêtes,
   écritures, unicités et résultats idempotents. Tester deux organisations avec les
   mêmes SKU/IDs, ownership du créateur et association acheteur ; ne jamais déduire
   une membership de customer_id. Consulter Security pour une nouvelle frontière.
5. Si un schéma change, lire [Mongo](../../../docs/mongodb-migrations.md) ou
   [PostgreSQL](../../../docs/postgresql-migrations.md), puis le runner actuel.
   Ajouter une version plutôt que modifier une migration appliquée. Prévoir plan,
   checksums, échec partiel et restauration ; pas de DDL automatique au démarrage.
   Distinguer écriture du code de migration et autorisation de l'appliquer.

## Tests et acceptation
Utiliser les commandes d'AGENTS et [testing](../../../docs/testing.md).
Choisir selon l'impact : [contrats ORDER tenant](../../../tests/test_tenant_orders_contracts.py),
[autorisation](../../../tests/test_tenant_authorization.py),
[contrats stock](../../../tests/test_tenant_stock_operations_contracts.py),
[API B2B](../../../tests/test_b2b_http_contracts.py).
Pour les garanties de persistance, les mocks ne suffisent pas : planifier/exécuter
les tests integration du store sur services jetables vérifiés, avec concurrence,
replay, rollback et panne. Ne pas inventer une cible pour débloquer les tests.

## Livrable et arrêt
Fournir diff minimal, contrat avant/après, preuves et limites, besoin de migration
et GO/NO-GO si une activation est envisagée. Arrêter la mutation si isolation,
identité de cible ou issue transactionnelle ne peut être établie ; ne pas contourner
le garde. Aucune modification ORDER sans demande distincte explicite.

## Exemple
« Ajoute une validation stricte à un filtre de lecture B2B existant et couvre
les entrées invalides ainsi que l'isolation entre organisations. »
