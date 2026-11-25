# Développer StockPilot AI v0.2.9

Les commandes utilisent Bash depuis la racine du dépôt, sous Linux ou WSL2. Prévoir Git, Python 3.12 avec venv, Node 22.13 ou ultérieur dans la série 22, npm, curl, Docker avec Compose 2.24.4 ou ultérieur et Chromium pour Playwright. Ollama est nécessaire pour l’inférence réelle. Les dépendances sont verrouillées dans le dépôt.

## Installer les dépendances

```sh
git clone --branch main https://github.com/WaelOuni/stockpilot-ai.git
cd stockpilot-ai
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m pip check
(cd frontend && npm ci && npx playwright install chromium)
```

Si Chromium signale une bibliothèque système absente, utiliser `npx playwright install --with-deps chromium` depuis `frontend/` sur le poste de développement.

## Démarrer une instance DEV isolée

Le parcours suivant lance l’API locale en mode `development` et le frontend Vite. Le parcours B2B complet avec Keycloak utilise un autre opérateur, décrit plus bas. Utiliser un terminal neuf, un checkout sans configuration privée et le moteur Docker local. Vérifier les noms de projet et les ports avant la première initialisation.

```sh
devdc() { docker compose -p stockpilot-dev029 -f docker-compose.yml -f docker/compose.local-dev.yml "$@"; }
docker compose ls -a
devdc config --quiet
devdc up -d --wait mongodb postgres
devdc ps
```

L’overlay publie Mongo sur `127.0.0.1:37029` et PostgreSQL sur `127.0.0.1:55429`. Compose crée les volumes du projet. Les identifiants PostgreSQL ci-dessous sont les valeurs publiques du Compose de développement. Copier les exports dans le terminal qui lance l’API. [Options complémentaires](../.env.example)

```sh
export DEPLOYMENT_ENVIRONMENT=development
export MONGO_URI='mongodb://127.0.0.1:37029/?replicaSet=rs0&directConnection=true'
export MONGO_DATABASE=stockpilot_dev
export POSTGRES_URL='postgresql://stockpilot:stockpilot@127.0.0.1:55429/stockpilot_dev'
export LANGGRAPH_CHECKPOINT_DB_URI="$POSTGRES_URL"
export CUSTOMER_JWT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen2.5:7b OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

Démarrer `ollama serve` dans un terminal séparé si le service n’est pas déjà actif. Installer les modèles sur le serveur DEV choisi. Avec Ollama sur Windows, `source scripts/dev-env.sh` propose l’URL depuis WSL. Vérifier l’URL avant téléchargement.

```sh
OLLAMA_HOST="$OLLAMA_BASE_URL" ollama pull qwen2.5:7b
OLLAMA_HOST="$OLLAMA_BASE_URL" ollama pull nomic-embed-text
curl -fsS "$OLLAMA_BASE_URL/api/tags"
```

Sur la **nouvelle base DEV** seulement, créer le corpus SQL et un article synthétique. L’exemple Mongo refuse une collection déjà remplie. L’index SQL 001 s’applique une seule fois. Pour une base existante, passer directement au démarrage.

```sh
devdc exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d stockpilot_dev < migrations/000_create_knowledge_chunks.sql
devdc exec -T postgres psql -v ON_ERROR_STOP=1 -U stockpilot -d stockpilot_dev < migrations/001_knowledge_chunks_source_unique.sql
devdc exec -T mongodb mongosh --quiet stockpilot_dev --eval 'if (db.products.countDocuments({})) quit(2); db.products.insertOne({sku:"DEV-001", name:"Perceuse DEV", category:"DRILL", price:99, stock:8})'
python scripts/ingest_products.py
```

```sh
# Terminal avec le venv et les exports ci-dessus
python -m uvicorn stockpilot.main:app --host 127.0.0.1 --port 8000
# Deuxième terminal, depuis la racine
(cd frontend && API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1 --port 5173 --strictPort)
```

Ouvrir `http://127.0.0.1:5173/` et contrôler `curl -fsS http://127.0.0.1:8000/health`. Les routes client du parcours historique demandent un JWT DEV signé dans l’environnement de l’API. [Connexion et interface locale](../frontend/README.md)

### Accès administrateur du parcours historique

Pour tester l’administration sur cette instance DEV, exécuter `python scripts/create_admin_verifier.py` et conserver le vérificateur côté serveur. Configurer `ADMIN_USERNAME`, `ADMIN_PASSWORD_VERIFIER` et l’origine exacte `ADMIN_TRUSTED_ORIGIN=http://127.0.0.1:5173`. Le HTTP local exige aussi `ADMIN_ALLOW_LOCAL_HTTP=true` en mode `development`. Relancer l’API après modification. Pour le parcours client, `python scripts/create_customer_token.py dev-user` délivre un jeton à durée limitée dans le terminal privé de l’émetteur. [Matrice d’accès](security/access-matrix.md)

## Vérifier le schéma B2B et les intégrations

La CLI Mongo B2B accepte une base jetable explicitement nommée. Utiliser le Compose CI indépendant ci-dessous. Ses bases en tmpfs sont réservées aux tests. Les ports 27027, 27028 et 55432 doivent être libres. Lancer ce bloc dans un terminal neuf avec le venv activé, sans importer de variables privées.

```sh
testdc() { docker compose -p stockpilot-local-tests -f docker/compose.ci.yml "$@"; }
testdc up -d --wait
export MONGO_TEST_URI='mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true'
export MONGO_MIGRATION_AUTH_TEST_URI='mongodb://ci:ci@127.0.0.1:27028/?authSource=admin'
export POSTGRES_TEST_URI='postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci'
python scripts/ci/check_targets.py
export STOCKPILOT_MIGRATION_URI="$MONGO_TEST_URI"
DB="stockpilot_b2b_test_$(python -c 'import uuid; print(uuid.uuid4().hex)')"
mig() { python -m stockpilot.infrastructure.persistence.mongodb.migrate "$@" --database "$DB"; }
mig plan
mig apply --confirm-disposable-database "$DB"
mig validate
mig status
mig plan
python -m pytest -q -m 'integration and not integration_optional' tests
testdc down
```

Le dernier plan doit être vide et l’historique doit contenir les sept succès. Les fixtures d’intégration préparent aussi les schémas PostgreSQL isolés dont elles ont besoin. La procédure manuelle PostgreSQL identifie la base et son `system_identifier` avant application. [Migrations PostgreSQL](postgresql-migrations.md)

Pour une recette B2B intégrale avec SSO, utilisateurs synthétiques, corpus par organisation et restauration indépendante, l’opérateur dédié exige Chromium, le binaire Linux `certutil` (paquet `libnss3-tools` sous Ubuntu) et deux URL d’inférence privées joignables respectivement depuis l’hôte et Docker. Après avoir réservé ses ports, depuis le checkout de test et avec les deux modèles installés, exporter `STOCKPILOT_DEMO_OLLAMA_URL` et `STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL`, puis exécuter `python scripts/b2b_demo.py validate`. Le mode `validate` crée sa propre installation de recette. [Prérequis et topologie B2B](b2b-demo.md)

## Contrôles locaux de la CI

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider
python scripts/run_evals.py --mode deterministic --suite enriched
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
(cd frontend && npx playwright test -c playwright.advice-synthetic.config.ts)
(cd frontend && npx playwright test -c playwright.b2b-ux.config.ts)
docker build -t stockpilot-dev-api:local .
docker build -f frontend/Dockerfile -t stockpilot-dev-web:local .
docker build -f docker/keycloak/Dockerfile -t stockpilot-dev-keycloak:local docker/keycloak
git diff --check
```

La sélection pytest par défaut exclut les intégrations. Playwright Advice lance ses serveurs sur 8002 et 5174. Le parcours B2B UX utilise 5186 et intercepte les réponses HTTP. L’inférence réelle et la connexion Keycloak se vérifient dans la recette dédiée. [Tests et sélections](testing.md)

## Arrêter, reprendre et diagnostiquer

Arrêter Vite et Uvicorn par Ctrl+C. Dans le terminal où la fonction `devdc` est définie, `devdc stop mongodb postgres` conserve les données. Reprendre avec `devdc up -d --wait mongodb postgres`, puis relancer l’API et Vite. Après un changement de clé JWT locale, émettre un nouveau jeton DEV.

| Symptôme | Vérification |
|---|---|
| Port occupé ou conteneur du même nom | Examiner `docker compose ls -a`, `docker ps -a` et `ss -ltn`. Identifier le projet propriétaire avant de lancer les services. |
| API indisponible | Vérifier `devdc ps`, les deux URI, le primaire rs0 et `devdc exec -T postgres pg_isready -U stockpilot -d stockpilot_dev`. |
| RAG sans réponse | Vérifier les modèles via `/api/tags`, l’ingestion du corpus et la dimension de l’embedding (768). |
| Migration refusée | Relire `mig status`, l’identité du serveur et le nom exact de la base jetable. Un verrou actif ou une migration interrompue nécessite une analyse de reprise. |
| UI B2B fermée en DEV | Le BFF exige la composition HTTPS/Keycloak et les permissions serveur. Utiliser le parcours B2B de recette pour valider cette interface. |
