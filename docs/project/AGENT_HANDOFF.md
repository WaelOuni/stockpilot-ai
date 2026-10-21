# Reprendre StockPilot — guide agent

## Vérifier avant d'agir

1. Lire [AGENTS](../../AGENTS.md), [état courant daté](PROJECT_STATUS.md),
   [roadmap](ROADMAP.md) et [décisions](ARCHITECTURE_DECISIONS.md).
2. Vérifier main sur GitHub, PR ouvertes et SHA de fusion réel. La branche GitHub
   par défaut est dev : cibler main explicitement, sans intégrer V11/dev.
3. Lire git status/worktree list ; créer un worktree dédié sur le main distant
   vérifié. Aucun pull/reset/checkout sur le checkout historique.
4. Lire l'ADR et le rapport du lot ; une fusion n'est pas un déploiement.
5. Vérifier l'autorisation avant toute action sur services/données.

## Invariants protégés

- /home/waelko/projects/stockpilot-ai/ à6f09496 : démo historique5175/8003.
- /home/waelko/deployments/stockpilot-demo-v017/ :5191.
- /home/waelko/deployments/stockpilot-candidate-v133/ :5193, dépendance
  /home/waelko/projects/stockpilot-v133-validation/.
- Preuves disponibles V13.4.3, worktrees historiques, secrets/certificats, volumes
  et images de rollback. Ne pas restaurer une ancienne absence automatiquement.
- Pas de mutation ou test sur les démos, pas de cleanup global ou de migration
  persistante implicite. customer_id n'est ni user_id ni organization_id.
- ORDER : SKU/quantités exacts, préparation sans réservation, confirmation explicite,
  atomicité, idempotence, ownership et gestion des issues inconnues.

## Point de reprise — 21 octobre 2026

Main vérifié à`1c9f41c`, fusion #51 ; SHA de branche distinct du SHA de fusion.
B1/B2/B3/B4 sont intégrés, [contrats de ressources](../security/tenant-authorization-boundary.md).
B4 : [ADR RAG/checkpoints](../security/tenant-rag-conversations.md),
[migrations PG](../postgresql-migrations.md), [validation](../validation/v13.7.3b4-rag-conversations.md).
Worktree /home/waelko/projects/stockpilot-v1382-unified-b2b-frontend,
branche feature/v1382-unified-b2b-frontend.

Le mode b2b_auth_test garde auth/session uniquement. V13.8.1 (#51 intégrée) fournit les routes
B1/B2/B3 via b2b_business_test, exclusivement jetable et désactivé par défaut.
[Contrat HTTP](../security/b2b-http-api.md), [validation](../validation/v13.8.1-secure-b2b-http.md).
Vue V13.8.2 : [guide utilisateur](../b2b-user-guide.md),
[validation](../validation/v13.8.2-unified-b2b-frontend.md). /b2b-app/ distinct du
JWT historique ; retours OIDC fixes et client cookie/CSRF sans token navigateur.
production_saas reste fermé. Aucune migration historique, aucun déploiement.
Une seule [cible de démo B2B future](../b2b-demo.md), pas encore opérationnelle.
B4 ne remplace pas les installations existantes.

Mongo0001/0002/0003 immuables. Mongo pending et PG pending ne prouvent ni succès
ni abort : garder les clés/contenus d'origine pour réconciliation.
PG : schéma dédié, recherche exacte filtrée avant LLM, saver racine privé au
créateur, pas de list global ni namespace arbitraire. Révocation Mongo/PG/BFF
non distribuée : ne pas promettre une garantie atomique inter-stores.

## Valider et livrer

Préparer son propre venv et node_modules ; ne pas réutiliser un package éditable
pointant vers les sources d'un autre worktree. [Tests et cibles jetables](../testing.md).

~~~sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
git diff --check
~~~

Intégrations uniquement avec MONGO_TEST_URI / POSTGRES_TEST_URI dédiées et vérifiées.
Pas de parcours Ollama réel sur les démos. Consigner tests exécutés, exclusions,
revue indépendante et CI sur le dernier SHA. PR vers main explicitement ; aucun
merge/tag/release sans instruction. La prochaine mission ne doit pas hériter
automatiquement d'une autorisation d'arrêt Windows propre à un chantier terminé.

Le [kit commercial #42](../product/README.md) distingue le script historique de
la future démo canonique ; aucun prix ou pilote n'est validé par les documents.

Prochaine étape : revue/CI V13.8.2, assistant V13.8.3, puis validation
E2E complète et restauration avant pilote distant. Kubernetes reste une option à arbitrer.
