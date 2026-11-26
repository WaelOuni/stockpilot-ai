# Reprendre StockPilot AI

La branche stable est `main`. La release applicative v0.2.9 correspond à `fccef111641c648ea1e9e21211523899d91353e5`. Vérifier le SHA distant et la version déployée séparément. Commencer par [AGENTS.md](AGENTS.md) et [le guide local](docs/LOCAL_DEVELOPMENT.md).

## Points d’entrée

- Vue 3/TypeScript dans `frontend/src/`. Le navigateur entre par `/b2b-app/`, appelle `/b2b/` et conserve uniquement le cookie de session. BFF FastAPI dans `src/stockpilot/bff.py`. `main.py` compose le parcours historique JWT/admin.
- Sous `src/stockpilot/`, `domain/` porte les invariants, `application/` les cas d’usage, ports et workflows LangGraph, `infrastructure/` les adaptateurs et `presentation/` les routes. Mongo en replica set fait autorité sur identité applicative, catalogue et réservation. PostgreSQL/pgvector conserve documents, conversations, checkpoints et journal.
- Keycloak utilise OIDC confidentiel et PKCE S256. Garder state, nonce, issuer, audience, callbacks fixes, cookies Secure/HttpOnly, Origin et CSRF. Résoudre côté serveur organisation, membership, permission et ownership à chaque accès. Utilisateur, acheteur et organisation ont des identités distinctes. Une conversation reste privée à son créateur. [Frontière tenant](docs/security/tenant-authorization-boundary.md)
- ORDER exige quantité entière positive lexicalement valide, SKU et order_id exacts, préparation sans réservation, confirmation explicite avec preuve persistée puis désarmement. Préserver idempotence, transaction stock/commande, isolation acheteur et checkpoints. Réconcilier un résultat incertain avec la même clé Mongo/PG. Toute évolution de logique ORDER, prompt ou modèle exige une demande dédiée.
- RAG filtre les passages en SQL par tenant avant top-K et avant l’appel au modèle. Vérifier les citations contre les passages autorisés avant restitution, refuser une réponse insuffisamment étayée. Préserver `nomic-embed-text`, version `v1`, dimension 768 et corpus existant. [Contrat RAG](docs/security/tenant-rag-conversations.md)

## Boucle de validation

Depuis un worktree dédié basé sur `origin/main`, installer Python 3.12 et Node 22 selon le guide local. Exécuter depuis la racine avec le venv activé.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
python scripts/run_evals.py --mode deterministic --suite enriched
(cd frontend && npm ci && npm test && npm run typecheck && npm run lint && npm run build)
(cd frontend && npx playwright install chromium && npx playwright test -c playwright.advice-synthetic.config.ts && npx playwright test -c playwright.b2b-ux.config.ts)
git diff --check
```

Intégrations sur services dédiés exclusivement. Suivre la séquence Compose CI et les trois URI de test du guide local, puis `python scripts/ci/check_targets.py` et `python -m pytest -q -m 'integration and not integration_optional' tests`. Les suites Playwright ci-dessus utilisent des données synthétiques. [Tests et recette SSO réelle](docs/testing.md)

Mongo B2B attend les migrations immuables 0001–0007, PostgreSQL 0001–0002. Lire status/plan, vérifier cible et checksum, appliquer explicitement sur une base jetable identifiée puis vérifier historique et structure. Garder le DDL hors démarrage. [Mongo](docs/mongodb-migrations.md) · [PostgreSQL](docs/postgresql-migrations.md)

## Livraison et exploitation

PR explicitement vers `main`. Garder `dev` indépendant. Documenter commandes exécutées, échecs et limites. Fusion, tag, release et déploiement requièrent une instruction. Interdiction de modifier directement les données de production ou de tester sur VM/DEMO. Garder secrets, clés TLS, sessions, données client et sauvegardes hors Git et des logs. Pour la recette VPS, les restrictions du BFF v0.2.9 et la reprise, suivre [le runbook VPS](docs/VPS_DEPLOYMENT.md).
