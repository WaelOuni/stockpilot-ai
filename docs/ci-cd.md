# CI/CD et releases StockPilot AI

V10 est une étape interne, pas la version v10.0.0. Le projet reste pré-1.0 :
`v0.1.0` première release technique stable, `v0.2.0` évolution fonctionnelle,
`v0.2.1` correctif. Aucun tag ni publication pendant V10. La livraison se fait par commits et PR vers dev.

## Branches et PR

Feature depuis `dev` → PR vers `dev` → CI → PR `dev` vers `main` → tag annoté
sur un commit de `main`. Protéger les deux branches contre les push directs,
force-push et suppressions ; exiger une PR (0 approbation obligatoire en mode
solo) et les cinq checks
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
- Intégration : tous les tests marqués `integration`, sélectionnés par marqueur et non par
  nom de fichier (`-m "integration and not integration_optional" tests`). Seul le test
  de lifecycle, qui exige ses propres services dédiés, porte `integration_optional`.
  `tests/test_ci_integration_contract.py` échoue si un test d'intégration n'est pas
  sélectionné sans être explicitement optionnel. Mongo 8 rs0 primaire avec transactions,
  PostgreSQL 16 image pgvector (le test checkpoint ne nécessite pas CREATE EXTENSION).
- `docker/compose.ci.yml` est indépendant de la stack dev/demo. Stockage tmpfs,
  ports loopback 27027/55432, credentials publics jetables `ci/ci`, aucun volume
  de données hôte. `check_targets.py` exige les URI exactes, vérifie le primaire
  et `current_database()` avant toute fixture. Les fixtures Mongo créent leurs
  bases UUID `stockpilot_transaction_test_*`, les fixtures PostgreSQL leurs
  schémas UUID dans `stockpilot_ci`. Elles initialisent leurs propres index et
  checkpoints. Aucune seed, reset ou migration DEV/DEMO. Sans `MONGO_TEST_URI` /
  `POSTGRES_TEST_URI`, ou avec une cible applicative (ports 27017/5432, endpoints de
  `Settings`, bases `stockpilot*` hors tests), les fixtures échouent avant toute
  écriture (`tests/integration_targets.py`) ; aucune valeur par défaut n'est utilisée.
- Images : builds et lancement sur ces mêmes services éphémères, base runtime
  Mongo `stockpilot_ci_runtime`, Ollama inaccessible volontairement. Smoke HTTP
  santé API/web, fallback SPA et refus sûr 503 de `/api/products` via reverse proxy
  dans le mode production fermé (aucun contenu métier).
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

## Release solo : publication exclusivement manuelle

`release.yml` possède uniquement `workflow_dispatch`. Pousser un tag ne déclenche
ni ce workflow ni une publication. `publish=false` est le défaut : gate, cinq
validations et builds seulement. Aucun accès aux secrets de publication, login
GHCR, push d'image ou création de draft dans ce mode.

Trois entrées : `tag` existant, `publish` booléen et `confirmation`. Une publication
exige `publish=true`, la phrase exacte `PUBLISH vMAJOR.MINOR.PATCH`, ainsi que la
variable **repository** `RELEASE_PUBLICATION_ENABLED=true`. Un input SHA arbitraire
n'existe pas. Une confirmation vide/incorrecte, un flag absent ou faux refuse.
Ne pas utiliser une variable uniquement attachée à l'environnement : le gate
sans environnement doit pouvoir lire le flag repository. Il reste désactivé
pendant la préparation et tant que tous les prérequis ne sont pas vérifiés.

### Ref d'exécution et workflow approuvé

Le dispatch doit utiliser **le tag comme ref d'exécution**, pas `main` avec un tag
en entrée. GitHub accepte une branche ou un tag dans `workflow_dispatch.ref`
([API officielle](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)).
La restriction d'environnement s'applique au `GITHUB_REF` du run, pas au ref du
checkout ni à l'input `tag`
([environnements GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)).
Ainsi `--ref v0.1.0`, avec input `tag=v0.1.0`, produit `refs/tags/v0.1.0` et respecte
la politique **Tag `v*`** existante. Le dispatch sur main/feature/SHA est refusé.
Aucune ouverture de l'environnement à des branches n'est nécessaire.

Le workflow doit d'abord être intégré par PR dans dev puis main, branche par
défaut où GitHub exige la présence du workflow manuel. Le tag doit contenir cette
révision. Le gate vérifie :

- SemVer strict `vMAJOR.MINOR.PATCH`, sans zéro initial, suffixe/prérelease ni metadata.
- Tag annoté existant ; son commit égale `GITHUB_SHA`, `GITHUB_WORKFLOW_SHA` et HEAD.
- `GITHUB_REF` égale exactement l'input tag ; `GITHUB_WORKFLOW_REF` désigne
  `.github/workflows/release.yml` du même dépôt au même tag.
- Commit ancêtre de `origin/main`, aucune origine de branche supposée pour un tag.
- Contenu du workflow release, CI et guard identique au pipeline actuellement
  approuvé dans `origin/main` au checkout. Un ancien commit main portant une
  politique de publication obsolète refuse, même s'il appartient à l'historique.
- Environnement existant, sans règle `required_reviewers`, avec politique custom
  sélectionnée et **exactement un filtre de type tag `v*`**, aucune branche.
- Ruleset `release-tags-protection` réellement `active`, target tag, scope
  `refs/tags/v*` sans exclusion, règles update/deletion/non_fast_forward,
  aucune restriction de création. Un bypass visible dans la réponse est refusé.

GitHub masque `bypass_actors` aux tokens qui ne peuvent pas administrer le ruleset
([API rulesets](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)).
L'absence du champ n'est **pas** une preuve d'absence de bypass : vérifier ce point
manuellement avec le compte administrateur avant activation. Aucun token admin
n'est ajouté au workflow pour cette seule lecture.

### Validations et permissions

Le checkout du gate utilise explicitement `github.sha` avec l'historique complet.
Le checkout épinglé peut sinon comparer le SHA de l'objet tag annoté au SHA du
commit, puis remplacer la référence locale par le commit lors d'un second fetch.
Le guard exige aussi les deux références annoncées par `origin` (objet tag et
commit résolu), vérifie le dépôt distant, puis compare l'objet et le commit locaux.
Un tag léger, absent, inaccessible, divergent ou uniquement local est refusé.

Un tag déjà créé conserve son workflow et son guard historiques. Relancer un run
sur ce tag ne charge pas un correctif ultérieur de main. Après intégration d'un
correctif du pipeline, un nouveau tag/version approuvé est nécessaire ; ne pas
déplacer, supprimer ou recréer un tag existant pour appliquer ce correctif.

`gate` est en lecture seule. La CI réutilisable reçoit le SHA exact et tous ses
checkouts l'utilisent. Un agrégat `release-validation` exige le résultat `success`
de chacun des cinq jobs : backend-quality, eval-framework, frontend-quality,
integration-tests, containers. Failure, cancelled et skipped refusent le SHA
validé. Aucun résultat de PR ancien ou statut homonyme n'est utilisé comme preuve.
`e2e`, explicitement désactivé pour les raisons ci-dessus, n'est pas obligatoire.

`build` est aussi en lecture seule et exige le SHA validé. `publish` ne peut
commencer qu'après réussite du gate, des cinq validations et du build, en mode
publication confirmé, avec SHA de validation identique. Lui seul référence
l'environnement `release` et reçoit `contents: write`, `packages: write`.
Aucun secret hérité dans la CI ; aucune permission write pour les PR.
Checkouts sans credentials persistants, timeouts et actions officielles SHA
conservés. Une concurrence globale sérialise les releases, y compris des tags
différents pour un même SHA. Aucun déclencheur `push`, PR ou pull_request_target.

Pas de deuxième approbateur : le consentement est le dispatch explicite confirmé,
exécuté depuis le tag contrôlé, par le développeur ayant les droits Actions.
Le job protégé revérifie ref, tag, pipeline, flag, environnement et ruleset avant
les effets externes, puis après chaque publication d'un composant.

### GHCR privé et draft

Les deux packages doivent **déjà exister, être privés et liés au dépôt**.
404, 403, erreur réseau, package public/interne ou liaison absente refusent
avant tout push. Un 404 peut masquer un package inaccessible : il n'est jamais
considéré comme une preuve d'absence ou de confidentialité. Le pipeline ne fait
pas de bootstrap de package et ne s'appuie pas sur sa visibilité par défaut.

GitHub documente un **PAT classique** pour son API REST Packages
([API officielle](https://docs.github.com/en/rest/packages/packages)). Configurer
le secret d'environnement `GHCR_VISIBILITY_TOKEN` avec seulement `read:packages`,
accès aux deux packages, expiration et rotation ; pas de write:packages ni scope
repo nécessaire pour ce dépôt public. Il sert uniquement à la lecture de
visibilité. Les pushes GHCR et la création de draft utilisent `GITHUB_TOKEN`,
sans PAT de publication. Le secret ne figure dans aucun input, build arg, log,
artefact ou CI de PR. Les endpoints user/organization sont calculés depuis le
vrai owner et son type via l'API, sans coordonnées fictives.

Avant une première release, l'administrateur doit préparer les packages privés
séparément. GHCR n'offre pas ici une création de package vide dans le workflow :
un éventuel bootstrap par image minimale non sensible est une **publication
séparée à autoriser explicitement**, puis il faut vérifier visibilité privée,
liaison au dépôt et accès Actions. Cette préparation n'est pas exécutée par
cette mission. Tant qu'elle manque ou n'est pas vérifiable, la release refuse.

### Bootstrap initial réalisable, séparé de la release

Procédure **future, soumise à une autorisation explicite distincte**. Aucun de ces
builds/pushes n'est effectué pour préparer cette PR. Le gate normal reste strict :
il ne publie jamais dans un package absent ou de confidentialité invérifiable.

1. Garder `RELEASE_PUBLICATION_ENABLED` absent/faux. Avec le compte propriétaire,
   vérifier dans GitHub Packages que les deux noms attendus sont disponibles ;
   vérifier aussi l'accès à la liste des packages via un token autorisé. Un 404
   isolé ne prouve ni absence ni confidentialité. Si un package existe déjà,
   inspecter ses Settings et son accès ; ne pas le recréer ni l'écraser.
2. Préparer un PAT classique temporaire `write:packages` (incluant la lecture),
   sans `delete:packages` ni scope `repo`, avec durée courte. GitHub propose
   [un lien de création limitant le scope](https://github.com/settings/tokens/new?scopes=write:packages).
   Le conserver hors Git et le fournir à `docker login ghcr.io --password-stdin`
   dans un Docker config temporaire privé ; jamais en argument ou dans les logs.
3. Depuis un **répertoire temporaire vide**, créer une image de réservation
   sans code, fichier, couche applicative, donnée ni credential. Seul Dockerfile :

```dockerfile
FROM scratch
LABEL org.opencontainers.image.source="https://github.com/WaelOuni/stockpilot-ai"
LABEL org.opencontainers.image.description="Empty private package bootstrap; not a StockPilot release"
```

   Après autorisation, construire depuis ce seul contexte, puis publier sous
   `ghcr.io/waelouni/stockpilot-api:bootstrap-private` et
   `ghcr.io/waelouni/stockpilot-web:bootstrap-private`, **un package à la fois**.
   Ne pas utiliser un Dockerfile du projet, `latest`, une version SemVer ou un
   tag SHA de release. Ces pushes créent uniquement une réservation non sensible.
   GitHub documente la visibilité privée au premier push
   ([Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)).
   Ce comportement documenté permet ce bootstrap séparé ; il ne remplace jamais
   une vérification de visibilité pour les images applicatives du pipeline.
   On ne peut pas lire la visibilité d'un package inexistant avant sa création :
   si cette garantie serveur n'est pas acceptable, ne pas effectuer le bootstrap
   et laisser la première release bloquée. Aucun contenu confidentiel n'est fourni.
4. **Après chaque création, avant de passer au package suivant**, vérifier Settings
   → Visibility **Private**, puis une réponse REST authentifiée **200** avec
   `visibility=private`, `package_type=container`, nom attendu et
   `repository.full_name=WaelOuni/stockpilot-ai`. Le label source facilite la liaison,
   mais la vérifier explicitement. En cas de public/404/403/erreur, arrêter ; ne
   jamais poursuivre par une release ni interpréter cette erreur comme une preuve.
5. Dans les Settings de chacun des packages, vérifier les permissions héritées
   (ce dépôt est public), retirer l'héritage si nécessaire et conserver les accès
   humains limités au propriétaire. Dans **Manage Actions access**, donner au dépôt
   `WaelOuni/stockpilot-ai` l'accès write nécessaire à `GITHUB_TOKEN`. Ne pas rendre
   le package public pour corriger une erreur d'accès.
6. Créer un autre PAT classique permanent à expiration maîtrisée, limité à
   `read:packages`, et l'enregistrer uniquement dans le secret d'environnement
   `release/GHCR_VISIBILITY_TOKEN`. Avec ce token, revérifier les deux endpoints
   `/users/WaelOuni/packages/container/stockpilot-api` et `stockpilot-web` :
   réponses 200 privées et liaison au dépôt, comme l'exige le guard. Pour une
   organisation, employer `/orgs/{owner}/packages/container/{name}`.
7. Se déconnecter du registre, supprimer seulement le Docker config/contexte
   temporaires, puis révoquer le PAT de bootstrap. Garder les tags de réservation
   privés sans les confondre avec des versions livrées ; aucun nettoyage distant
   automatique. Documenter la création et les contrôles sans conserver de token.

Le bootstrap est une publication administrative minimale, pas un dry-run et pas
une exception dans `release.yml`. Il ne crée aucun tag Git ni GitHub Release, ne
change pas les protections main/dev/tags ou la restriction de l'environnement,
et n'active pas le flag. Une fois les deux packages privés vérifiés, suivre la
procédure normale : intégration sur main, tag annoté, dry-run sur le tag, puis
activation/confirmation uniquement après configuration complète. Le flag reste
absent/faux pendant ces préparatifs.

Les images sont reconstruites depuis le SHA tagué dans le runner de publication,
avec labels OCI source/revision ; aucun transfert Docker par artefact Actions,
accessible aux lecteurs de ce dépôt public. Seul le step de publication reçoit
les credentials registry. La reconstruction peut différer du premier build
(timestamps) : les digests consignés sont ceux du registre réellement publié.

Tags GHCR : version sans `v` et `sha-<SHA Git complet>`, jamais `latest`. Les quatre
cibles doivent être absentes avant tout push ; erreurs d'accès ne sont pas
interprétées comme absence. Les deux tags de chaque composant doivent retourner
le **même digest distant SHA256**. `images.md` contient tag Git, SHA et références
image/version/SHA/digest, uniquement des métadonnées non sensibles.

La visibilité des **deux** packages est revérifiée après chaque composant, avant
création d'une GitHub Release **draft**, jamais publiée automatiquement. La draft
est ensuite vérifiée (tag, SHA cible et statut draft) et reçoit `images.md` et les
notes générées depuis l'historique GitHub. Pas de dataset, dump, archive Docker
ni sauvegarde dans ses attachments. Une draft d'un dépôt public ne doit être
publiée que sur décision distincte : elle deviendrait publique.

Le YAML ne fixe pas la visibilité des packages. L'administrateur doit la
maintenir dans Settings. Aucune vérification API ne peut rendre atomiques une
modification concurrente de visibilité et un push ; contrôler les autres
publishers/admins et leur accès. Les tags GHCR ne sont pas immuables côté serveur :
utiliser les digests et limiter les publishers. Les deux images et la draft
n'ont pas de transaction commune. Tout échec après un push affiche les cibles
tentées et les pushes confirmés dans le résumé Actions, sans cleanup distant
ni suppression/écrasement automatique. Une perte réseau après un push peut
laisser un résultat indéterminé : réconcilier le registre et la draft avant toute
réexécution. Un tag existant ou une draft existante refuse la relance.

## Configuration GitHub à vérifier avant activation

Lecture seule du dépôt le 11/10/2026, lors de l'adaptation solo :

- Environnement `release` créé, aucun required reviewer, custom policy Tag `v*`
  uniquement : compatible avec la solution. `can_admins_bypass=true` est présent ;
  désactiver le bypass d'environnement si l'interface/le plan le permet. Le guard
  ne s'en sert jamais pour contourner la restriction de ref.
- Ruleset `release-tags-protection` id 24881288 **active**, trois règles prévues,
  création autorisée, exclusions vides, bypass_actors vides vérifiés avec l'accès
  administrateur. Le workflow revérifie activation/scope/règles à chaque run.
- Dev protégé par PR et cinq checks, sans bypass explicite et 0 approbation.
  Main protégé par PR mais exige actuellement seulement `Unit Tests` et
  `AI Eval Framework`. Ajouter aussi les trois checks restants pour la politique
  recommandée, sans imposer un deuxième reviewer au développeur solo.
- Les noms de checks de PR restent exactement `Unit Tests`, `AI Eval Framework`,
  `frontend-quality`, `integration-tests`, `containers` ; ne pas configurer les
  noms préfixés `validate / ...` de la release comme checks de branches.
- `RELEASE_PUBLICATION_ENABLED` absent (404). Les lectures des deux packages
  retournent 404 : existence/confidentialité non prouvées, publication bloquée.

Aucun réglage distant, protection de branche, secret, variable ou package n'a été
modifié. Avant activation : intégrer le pipeline dans main via PR ; conserver
l'environnement tag-only sans reviewers ; vérifier les règles tags avec le
compte admin ; préparer/vérifier les deux packages privés ; donner au dépôt
l'accès Actions aux packages (write pour GITHUB_TOKEN), configurer le secret
read:packages ; vérifier la politique Actions permettant le seul job publish
contents:write/packages:write. Définir seulement ensuite la variable repository
RELEASE_PUBLICATION_ENABLED=true. Ne pas ajouter de bypass pour simplifier.

En mode solo, l'administrateur ayant les droits d'écriture reste dans le périmètre
de confiance : un YAML modifié peut supprimer ses propres guards. Les checks
ci-dessus protègent le pipeline livré et les erreurs de déclenchement ; ils ne
remplacent pas les protections serveur ni une restriction des droits Actions/
write des autres utilisateurs. Ne pas considérer n'importe quel tag v* comme
une révision approuvée, même si l'environnement accepte son nom.

## Procédure solo : actions futures, non exécutées pendant cette mission

1. **GitHub / intégration** : PR dev → main, vérifier tous les checks, fusionner
   selon les protections ; attendre également le workflow CI du push main.
2. **GitHub / préparation** : vérifier les réglages ci-dessus et la confidentialité
   des deux packages ; activer le flag seulement une fois la configuration complète.
3. **Local / création du tag** : partir du main à jour, vérifier le SHA approuvé,
   créer puis pousser un tag annoté. Ces commandes ne publient aucune image avec
   le nouveau workflow, qui ne possède aucun déclencheur push :

```sh
git switch main
git pull --ff-only origin main
git rev-parse HEAD
git tag -a v0.1.0 -m 'StockPilot AI 0.1.0'
git push origin v0.1.0
```

4. **GitHub / validation sans publication** : lancer manuellement sur le tag.
   Le workflow doit être présent sur main et au tag ; le CLI évite une sélection
   accidentelle de main dans le sélecteur UI de ref :

```sh
gh workflow run release.yml --ref v0.1.0 -f tag=v0.1.0 -F publish=false
```

Inspecter le run : gate, `validate / ...`, agrégat et build réussis ; publish
skipped. Le dry-run ne nécessite ni flag activé ni secret de visibilité.
Il vérifie néanmoins l'environnement et le ruleset ; il ne prouve pas encore
l'accès registry/secret. Aucun nouveau tag ne doit être créé pour le relancer.

5. **GitHub / publication réelle et explicite** : après inspection et consentement,
   lancer un nouveau run. Il relance ses propres validations avant de pousser :

```sh
gh workflow run release.yml --ref v0.1.0 -f tag=v0.1.0 -F publish=true -f 'confirmation=PUBLISH v0.1.0'
```

6. **GitHub / vérification** : vérifier les quatre tags, les digests identiques
   version/SHA par composant, les labels OCI, la visibilité **Private** des deux
   packages et la draft non publiée avec `images.md`. La publication publique de
   cette draft est une autre décision ; aucune deuxième approbation n'est requise
   pour le workflow solo, et aucune publication de draft n'est automatisée.

Les étapes V9/V10 ne sont jamais traduites en v9.0.0/v10.0.0. Ne pas déplacer ni
supprimer un tag release. Un correctif reçoit une nouvelle version SemVer.
Ne jamais réactiver un ancien workflow de tag automatique.

### Récupération et rollback

Pour les images privées, se connecter avec un token read:packages via
`docker login ghcr.io --password-stdin`, sans token dans l'historique. Exemple de
coordonnées actuelles, calculées par le workflow depuis le vrai owner :

```sh
docker pull ghcr.io/waelouni/stockpilot-api:0.1.0
docker pull ghcr.io/waelouni/stockpilot-web:0.1.0
docker image inspect --format '{{json .RepoDigests}}' ghcr.io/waelouni/stockpilot-api:0.1.0
# Comparer avec images.md, puis utiliser image@sha256:<digest>.
```

Rollback d'un déploiement privé existant : sélectionner les deux digests d'une
version précédente validée, rétablir ces références dans sa configuration,
vérifier healthchecks et parcours applicatifs. Ne pas déplacer un tag, ne pas
réécrire main, ne pas supprimer une image automatiquement. Les données ne sont
pas restaurées automatiquement : vérifier la compatibilité des schémas. En cas
de publication partielle, réconcilier d'abord les pushes tentés/confirmés et la
draft, puis décider d'une nouvelle version ou d'une réparation explicite.
Aucun déploiement ni migration n'est effectué par ce workflow.

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
.venv/bin/python -m pytest -q -m "integration and not integration_optional" tests
docker compose -f docker/compose.ci.yml -p stockpilot-v10-validation down -v
git diff --check
```

Les ports doivent être libres et Docker accessible. Pour les smoke images,
reprendre les commandes containers de ci.yml en adaptant le nom du réseau au
projet Compose local, puis `python scripts/ci/smoke_images.py`. Nettoyer uniquement
les conteneurs créés pour cette validation. Ne jamais employer docker system prune.
Valider les workflows avec actionlint 1.7.7 (label self-hosted configuré).
Aucun workflow GitHub n'a été exécuté lors de la validation locale V10.

Pour cette adaptation solo :

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_release_guards.py tests/test_eval_ci.py
actionlint .github/workflows/ci.yml .github/workflows/release.yml
git diff --check
```

Les tests exercent les guards réels et le shell de publication avec les frontières
Git/REST/registry remplacées par des doubles contrôlés. Aucun tag réel, push
Docker, release ou mutation GitHub n'est exécuté. Une validation locale ne prouve
pas une exécution GitHub : le premier dry-run sur un tag autorisé reste à faire
après intégration/configuration, sur décision distincte.

Validation locale de cette adaptation : **100 tests ciblés réussis** (89 guards /
contrats / shell release et 11 tests eval CI), actionlint officiel **1.7.7** sur
l'ensemble des workflows, `git diff --check`. L'archive actionlint est vérifiée
avec les checksums de sa release officielle. Les nouveaux vérificateurs ont
également validé en lecture seule l'environnement solo et le ruleset de tags
actuels sur GitHub. Aucun workflow GitHub n'a été déclenché pour cette adaptation,
aucun build/push distant, tag, release, déploiement ou changement de base de données.
