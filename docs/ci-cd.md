# CI/CD et releases StockPilot AI

V10 est une étape interne, pas la version v10.0.0. Le projet reste pré-1.0 :
`v0.1.0` première release technique stable, `v0.2.0` évolution fonctionnelle,
`v0.2.1` correctif. Aucun tag ni publication pendant V10. La livraison se fait par commits et PR vers dev.

## Branches et PR

Feature depuis `dev` → PR vers `dev` → CI → PR `dev` vers `main` → tag annoté
sur un commit de `main`. Protéger les deux branches contre les push directs,
force-push et suppressions ; exiger une review et les cinq checks
`Unit Tests` (backend-quality), `AI Eval Framework` (eval-framework),
`frontend-quality`, `integration-tests`, `containers`.
Exiger une branche à jour avant merge. Ne pas exiger `e2e`, explicitement désactivé.
La CI s'exécute sur PR vers dev/main et push dev/main. Le push main valide
l'intégration ; la release relance également les contrôles sur le commit tagué.

## CI et isolation

- Python 3.12 ; installation `requirements-dev.lock` puis projet editable
  `--no-deps`. `pip check`, Pytest déterministe (`not integration` du manifeste),
  framework eval offline et suite enriched déterministe. Aucun nouveau linter Python.
- Node 22 supporté par engines ; `npm ci`, Vitest, typecheck, ESLint, build Vue.
- Intégration : uniquement les trois fichiers `test_mongo_transaction_integration.py`,
  `test_mongo_product_import_integration.py`, `test_postgres_checkpoint_integration.py`,
  avec marqueur `integration`. Mongo 8 rs0 primaire avec transactions,
  PostgreSQL 16 image pgvector (le test checkpoint ne nécessite pas CREATE EXTENSION).
- `docker/compose.ci.yml` est indépendant de la stack dev/demo. Stockage tmpfs,
  ports loopback 27027/55432, credentials publics jetables `ci/ci`, aucun volume
  de données hôte. `check_targets.py` exige les URI exactes, vérifie le primaire
  et `current_database()` avant toute fixture. Les fixtures Mongo créent leurs
  bases UUID `stockpilot_transaction_test_*`, les fixtures PostgreSQL leurs
  schémas UUID dans `stockpilot_ci`. Elles initialisent leurs propres index et
  checkpoints. Aucune seed, reset ou migration DEV/DEMO.
- Images : builds et lancement sur ces mêmes services éphémères, base runtime
  Mongo `stockpilot_ci_runtime`, Ollama inaccessible volontairement. Smoke HTTP
  santé API/web, fallback SPA et `/api/products` vide via reverse proxy.
- Nettoyage `always()` limité aux ressources créées par le job. Pas d'artefacts
  de données, de logs applicatifs ou de credentials uploadés.

Les PR n'ont que `contents: read`, sans secrets ni write-token.
Checkout ne conserve pas de credentials. Pas de `pull_request_target`.
Actions officielles fixées par SHA complet, vérifiés via les tags officiels
checkout v4, setup-python v5, setup-node v4, upload-artifact v4 le 11/10/2026.
Timeouts, caches indexés sur lockfiles et annulation des validations obsolètes.
`actionlint.yaml` reconnaît le label existant `stockpilot-evals`.
Le workflow manuel `ai-evals.yml` reste inchangé et hors gates obligatoires.

## E2E et contrôles de sécurité

Le harness Playwright existant lance FastAPI 8002 / Vite 5174 et exige un vrai
Ollama (classification ORDER), une base exacte `stockpilot_e2e`, un verrou
PostgreSQL et un initializer capable de créer la base. Il vérifie Ollama avant
reset. Il ne peut pas devenir un gate déterministe sans nouveau harness approuvé.
Le job `e2e` est donc explicitement `if: false`, sans exécution simulée.
Pour l'activer ultérieurement : fournir un harness déterministe autorisé ou un
runner dédié avec Ollama/modeles provisionnés, services exclusivement éphémères,
`E2E_PYTHON`, les URI E2E exactes, `npm ci`,
`npx playwright install --with-deps chromium`, puis `npm run test:e2e`.
Ne jamais le connecter aux bases E2E conservées du développeur depuis CI.
Traces, vidéos et captures contenant des tokens restent désactivées.
Aucune campagne real AI eval n'est exécutée par V10.

Bloquants : tests, pip check, lint/typecheck/build, intégration, builds/smoke Docker.
Validation locale supplémentaire : actionlint, nginx -t, utilisateurs/healthchecks,
allowlist du contexte Docker, git diff --check. Le build npm signale les
vulnérabilités disponibles (0 lors du build local), sans en faire un gate distinct.
Pas de scanner CVE d'image ni de scanner de secrets exhaustif ajouté : le contexte
Docker est une allowlist et les images ne copient jamais .env, backups ou datasets.
Cela ne remplace pas un audit supply-chain. Les actions et bases doivent être
mises à jour régulièrement par PR revue.

## Packaging Docker

Construire depuis la racine :

```sh
docker build -t stockpilot-api:local .
docker build -f frontend/Dockerfile -t stockpilot-web:local .
```

API : Python slim 3.12 fixé par digest, venv dans stage séparé, utilisateur 10001,
source seule `src/stockpilot`, Uvicorn en exec form, port 8000, healthcheck `/health`.
`LOG_FILE=""` donne stdout/stderr seulement. Aucun service de données dans l'image.
Fournir à l'exécution MONGO_URI/MONGO_DATABASE, POSTGRES_URL,
LANGGRAPH_CHECKPOINT_DB_URI si distinct, OLLAMA_BASE_URL et les credentials
CUSTOMER_JWT_SECRET/admin via gestionnaire de secrets ou fichier d'environnement
privé. Ne jamais les mettre dans des build args. Le startup existant initialise
les index commandes/checkpoints : choisir les bases et permissions avant lancement.
Les index admin SKU/import/stock et les schémas RAG ne sont pas provisionnés par
l'image ; utiliser les mécanismes existants avec autorisation explicite.
`/health` est une liveness existante, pas une readiness Ollama/RAG.

Web : Node 22 pour compiler, Nginx unprivileged UID 101, port 8080 et `/healthz`.
Bases Node/Nginx fixées par digest. `tsconfig.production.json` exclut les tests,
donc aucune fixture DEMO n'entre dans le build. Seul dist est copié dans le runtime.
SPA fallback et `/api/` vers `API_UPSTREAM` (défaut `http://api:8000`), préfixe retiré.
API_UPSTREAM est une URL interne de confiance fournie par l'opérateur au démarrage,
sans secret ; la substitution Nginx se limite à cette variable. VITE_API_BASE_URL
reste `/api`, public. Une même image fonctionne dans plusieurs environnements.
TLS et ingress sont à configurer hors de cette mission ; aucun déploiement Internet.

Les dépendances Python et transitives sont un snapshot de l'environnement V9
fonctionnel, validé par installation propre en image et `pip check`. Les outils
Pytest sont séparés dans requirements-dev.lock. Pas de hash des wheels ; les
extras transitifs présents dans l'environnement historique peuvent rester dans le
snapshot. Pour une mise à jour, résoudre pyproject.toml dans un venv Python 3.12
Linux propre, reconstruire les deux lockfiles et relancer les gates. Ne pas faire
un freeze global contenant le projet editable, des paths locaux ou des credentials.
Les images ciblent Linux amd64 dans GitHub ; aucune validation multiarchitecture.

## Release et GHCR

`release.yml` réagit aux tags `v*` mais refuse tout format autre que
`vMAJOR.MINOR.PATCH` sans zéro initial, tout tag léger, tout commit absent de main
ou toute publication sans variable repository `RELEASE_PUBLICATION_ENABLED=true`.
`workflow_dispatch` est toujours dry-run : commit de main requis, validations et
builds, aucune connexion GHCR, aucune création de release.

Le gate exporte le SHA exact ; la CI réutilisable valide le ref déclencheur et le
build checkout ce SHA explicitement. Aucun transfert d'image via artefact Actions :
les artefacts d'un dépôt public sont accessibles à ses lecteurs
([GitHub](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)).
Le job build reste sans droits de publication. Après validation et approbation
`release`, publish checkout le même SHA puis reconstruit les images sur son runner
éphémère, avant de recevoir les credentials GHCR dans le seul step de publication.
Il exécute donc les Dockerfiles revus appartenant à main ; ses droits de job sont
contents:write/packages:write, sans actions:read. Pas d'archive Docker uploadée.
Les bytes de cette reconstruction peuvent différer du premier build (timestamps) ;
les digests documentés sont ceux des images réellement poussées, jamais ceux du
premier build. Le SHA OCI et les deux tags proviennent du même gate et les deux
tags locaux désignent le même ID d'image. Pas de promesse de builds bit-reproductibles.
L'environnement doit exister avec au moins un reviewer et prevent_self_review=true :
le gate vérifie sa configuration avant de permettre un job publish. Le dépôt étant
public, cette lecture de configuration est possible sans permission write.
Aucun `latest`. Tags : version sans v et `sha-<SHA complet>`.
Les coordonnées sont calculées depuis GITHUB_REPOSITORY_OWNER et le label source
GITHUB_REPOSITORY : pour le dépôt actuel,
`ghcr.io/waelouni/stockpilot-api` et `ghcr.io/waelouni/stockpilot-web`.

Une release **draft** est créée avec notes GitHub générées, SHA et fichier
`images.md` contenant les deux références par digest. Les images elles-mêmes
restent dans GHCR ; aucun dump/dataset ni archive du working tree n'est attaché.
La publication finale de la release draft reste une décision humaine explicite.
Le job refuse un package existant non privé ou une visibilité invérifiable.
Les packages nouvellement créés sont privés par défaut :
[documentation GitHub Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
Le YAML ne configure ni n’impose la visibilité du registre : l’opérateur doit
vérifier et maintenir les packages privés dans leurs Settings et accorder
l’accès Actions au dépôt. Les contrôles API avant/après push détectent les
écarts, mais ne peuvent empêcher un administrateur de rendre un package public
ou une modification concurrente de visibilité. Un 404 API peut masquer un
package inaccessible : vérifier aussi les droits et l’absence de collision de noms
avant la première publication. Les tags déjà présents sont refusés. Les deux
images sont pré-vérifiées avant tout push ; erreur d’accès/réseau registre = refus,
jamais interprétée comme tag absent. GHCR n'impose pas l'immuabilité de ces tags :
utiliser les digests pour déploiement et contrôler les droits des autres publishers.
Les deux pushes et la création de release ne sont pas atomiques : un échec peut
laisser une image privée sans release. Réconcilier manuellement avant réexécution,
sans écraser un tag existant. Un second tag de version sur le même SHA est refusé.

## Configuration GitHub manuelle obligatoire

État lu le 11/10/2026 : dépôt public, default branch main, permissions Actions
par défaut read, can_approve_pull_request_reviews=false ; environnement release
absent (API 404). Aucune configuration distante modifiée.

Le ruleset actif main exige déjà `Unit Tests` et `AI Eval Framework`, sans
review obligatoire (0 approbation). Ces deux noms restent présents dans la CI. Les jobs de PR ont exactement ces
noms ; ne pas utiliser les noms préfixés `validate / ...` du workflow réutilisable
de release pour les required checks des branches.
Les protections classiques API retournent 404 mais ce ruleset s’applique bien à main.
Dev n’a pas de protection identifiée. Aucun réglage n’a été modifié.

Un workflow de tag est lu depuis le commit tagué : une version malveillante
pourrait retirer ses propres guards YAML. Les protections serveur sur la création
des tags et la review indépendante de l’environnement sont donc indispensables ;
le YAML seul ne permet pas d’affirmer qu’un tag non autorisé ne publiera jamais.

1. Configurer les protections dev/main et checks requis ; activer Actions.
2. Créer l'environnement `release`, required reviewers, empêcher self-review,
   autoriser uniquement les tags de release. Ajouter également un ruleset sur
   les tags v* limitant leur création aux release managers et interdisant leur
   déplacement/suppression. Un tag n’a pas de branche d’origine fiable : le
   contrôle d’ancestry porte sur le commit ; un tag posé sur un commit absent de
   main est refusé, quelle que soit la branche du poste qui le pousse. Vérifier que le plan GitHub permet
   effectivement ces protections ; ne pas activer la variable sinon.
3. Vérifier politique GITHUB_TOKEN autorisant packages:write et contents:write
   pour ce seul job ; packages liés au repository et accès Actions du repository.
4. Maintenir packages privés ; publication publique uniquement après décision
   explicite et adaptation revue du guard de visibilité. Dans les paramètres
   du package, changer visibility/access uniquement après cette décision.
5. Seulement ensuite définir RELEASE_PUBLICATION_ENABLED=true. Par défaut le
   workflow de tag échoue avant build/publication. Aucun PAT requis pour publier.
6. Releases d'un dépôt public : un draft reste non publié, mais sa publication
   devient publique. Vérifier visibilité du dépôt avant toute publication humaine.
7. Pour un transfert vers une organisation, adapter le lookup de visibilité
   `/users/{owner}/packages` en `/orgs/{owner}/packages` avant activation.

## Tags, récupération et rollback

Commandes **pour une future décision de release**, non exécutées pendant V10 :

```sh
git switch main
git pull --ff-only origin main
git tag -a v0.1.0 -m 'StockPilot AI 0.1.0'
git push origin v0.1.0
```

Après approbation environnement, inspecter la draft release, les digests et les
notes ; publier la draft seulement après décision explicite. Jamais retagger
une version déjà publiée, ni déduire SemVer du numéro d'étape V9/V10.

Pour un package privé : token classic read:packages (ou accès Actions accordé),
connexion `docker login ghcr.io` avec password-stdin. Ne pas copier le token dans
les commandes/historique. Références et récupération :

```sh
docker pull ghcr.io/waelouni/stockpilot-api:0.1.0
docker pull ghcr.io/waelouni/stockpilot-web:0.1.0
docker image inspect --format '{{json .RepoDigests}}' ghcr.io/waelouni/stockpilot-api:0.1.0
# Comparer avec images.md puis utiliser la référence image@sha256:<digest>.
```

Rollback : sélectionner les deux digests d'une release précédente validée,
remettre ces références dans le déploiement privé existant, vérifier healthchecks
et parcours applicatifs. Ne pas modifier main ou déplacer un tag. Les données
ne sont pas restaurées automatiquement ; vérifier la compatibilité des schémas
avant rollback. Cette mission n'ajoute aucune migration ni déploiement.

## Validation locale sans toucher les services dev/demo

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
cd frontend
npm ci
npm test
npm run typecheck
npm run lint
npm run build
cd ..
docker compose -f docker/compose.ci.yml -p stockpilot-v10-validation up -d --wait
export MONGO_TEST_URI='mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true'
export POSTGRES_TEST_URI='postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci'
.venv/bin/python scripts/ci/check_targets.py
.venv/bin/python -m pytest -q -m integration tests/test_mongo_transaction_integration.py tests/test_mongo_product_import_integration.py tests/test_postgres_checkpoint_integration.py
docker compose -f docker/compose.ci.yml -p stockpilot-v10-validation down -v
git diff --check
```

Les ports doivent être libres et Docker accessible. Pour les smoke images,
reprendre les commandes containers de ci.yml en adaptant le nom du réseau au
projet Compose local, puis `python scripts/ci/smoke_images.py`. Nettoyer uniquement
les conteneurs créés pour cette validation. Ne jamais employer docker system prune.
Valider les workflows avec actionlint 1.7.7 (label self-hosted configuré).
Aucun workflow GitHub n'a été exécuté lors de la validation locale V10.
