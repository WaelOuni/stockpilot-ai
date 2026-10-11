# Validation V10 — 11 octobre 2026

Branche `feat/ci-cd-release`, base `4e448b22eb1c7099018d7341450e6819de474379`.
Fetch dev/main effectué, dev fast-forward ; V9 intégrée (`8e3dba6`, `d73a470`,
`b43572c`, puis `4bda0e9`). Working tree propre avant création de branche.
origin/main `38c7e4cac40a88ff87520d5d53e7b900f63e2cd5`. Main local non modifié.

## Contrôles locaux

| Contrôle | Résultat |
|---|---|
| Backend Pytest déterministe | 1416 passed, 22 deselected, 84,96 s |
| Framework eval ciblé après adaptation du contrat CI | 36 passed |
| Suite enriched déterministe offline | 88 cas, 0 échec |
| Vitest | 22 fichiers, 391 tests passed |
| Vue typecheck / ESLint / build | PASS |
| Intégration Mongo transactions/import + PostgreSQL checkpoints | 22 passed, 2,52 s |
| Vérification des URI CI / primaire rs0 / base PostgreSQL | PASS avant fixtures |
| Docker API / web builds | PASS |
| API / web non-root et healthchecks | UID 10001 / 101, healthy |
| Smoke HTTP santé API/web, SPA, proxy /api/health, /api/products vide | 5 PASS |
| pip check hôte et image API | PASS |
| nginx -t | PASS |
| Contexte API /app limité à src, absence .env et jsonl | PASS |
| Contexte web | dist compilé uniquement, aucun dataset/fixture DEMO copié |
| actionlint 1.7.7, trois workflows | PASS avec label self-hosted déclaré |
| Parsing YAML / permissions / SHA des actions / gates publication | PASS |
| Cas SemVer autorisés/refusés | v0.1.0/v0.2.0/v0.2.1 acceptés ; formats incomplets, suffixes et zéros initiaux refusés |
| git diff --check | PASS |

Le premier build web échouait car vue-tsc incluait des tests important une
fixture DEMO ; corrigé par tsconfig.production.json, rebuild puis smoke réussis.
Le premier Pytest complet : 1415 passed, 1 failure, 22 deselected ; assertion sur
l'ancienne structure ci.yml. Adaptation du test de contrat existant, suite ciblée
36 passed, puis relance complète : 1416 passed, 22 deselected.
Le premier Pytest en sandbox a été interrompu sans résultat exploitable ; les
fixtures HTTP locales nécessitent l'exécution autorisée hors sandbox.

Images locales finales : API environ 84,8 MB, web environ 25,6 MB. Digests locaux
(manifest lists BuildKit, distincts des futurs digests GHCR) :

- API `sha256:dd8a2e4e35f30f6f7af348b01dc9ffae236efe7ecfb9731445cb20eec1eae2b7`.
- Web `sha256:d20084b6b33195aabd068b272ee21b682e2866c8f4313e9daad70e055984192d`.

Mongo/PostgreSQL utilisés : nouveaux conteneurs du projet Compose
`stockpilot-v10-validation`, ports 27027/55432, stockage tmpfs, aucun mount de
données hôte. Backend image : Mongo `stockpilot_ci_runtime`, PostgreSQL
`stockpilot_ci`, Ollama URL inaccessible. Aucun seed DEV/DEMO/E2E conservé.
Tous les conteneurs et le réseau V10 ont été arrêtés/supprimés après validation.
Images locales conservées pour review ; aucune publication distante.

## Scénarios CI/release examinés

PR dev/main : jobs sans secrets, checkout sans credentials, contenus en lecture.
Push dev/main : mêmes gates. Release : format strict, tag annoté, ancestry main,
publication activée explicitement, CI réutilisable sans secrets, checkout SHA
exact, reconstruction du même SHA dans le job protégé sans artefact d’image public,
et publish seul privilégié.
Dry-run manuel : validations/build sans publication. Package déjà public ou
visibilité invérifiable : refus ; tags d'image existants : refus. Pas de latest.
Ces contrôles de configuration ont été vérifiés localement ; ni événement GitHub
ni tag réel n'a été créé pour les tester. La protection environnement et les
permissions GHCR devront être vérifiées sur GitHub après review et activation.

Dépôt GitHub public ; permissions Actions par défaut read ; environnement
release absent (API 404). Aucune mutation des paramètres GitHub.
Playwright réel, campagne real AI eval, scan CVE d'images et publications :
NON EXÉCUTÉS. E2E explicitement désactivé et justifié dans ci-cd.md.
Aucun workflow GitHub exécuté ; aucune revendication de validation CI distante.

## Fichiers livrés

- Adaptés : `.github/workflows/ci.yml`, `frontend/package.json`, `tests/test_eval_ci.py`.
- Créés : `.github/workflows/release.yml`, `.github/actionlint.yaml`, `Dockerfile`,
  `frontend/Dockerfile`, `.dockerignore`, `frontend/tsconfig.production.json`,
  `requirements.lock`, `requirements-dev.lock`, `docker/compose.ci.yml`,
  `docker/web.conf.template`, `scripts/ci/check_targets.py`,
  `scripts/ci/smoke_images.py`, `tests/test_release_guards.py`, `docs/ci-cd.md`, `docs/v10-pr.md`, ce rapport.

Première phase : PR locale préparée dans docs/v10-pr.md, sans commit ni push.
La livraison par commits et PR vers dev est autorisée dans la revue finale.
Aucun tag, release, publication GHCR ou déploiement.

État Git à la fin de la première phase : HEAD/dev/origin-dev = 4e448b2,
branche feat/ci-cd-release, main local inchangé à 67d0767.
Conclusion : READY FOR REVIEW pour la préparation V10 ; activation distante
conditionnée aux prérequis GitHub et à la première exécution réelle des workflows.

## Revue finale avant PR

Défaut corrigé : un tar des images en artefact Actions aurait rendu leurs bytes
accessibles aux lecteurs du dépôt public. Transfert supprimé ; reconstruction
sur SHA validé dans publish protégé, sans artefact d’image ni actions:read.
Le gate vérifie reviewers et prevent_self_review ; le registre refuse les erreurs
inattendues, préflight des deux images avant push et visibilité vérifiée après push.
Les packages doivent être réglés privés manuellement ; le YAML ne modifie pas
leur visibilité. Variable RELEASE_PUBLICATION_ENABLED absente (API 404),
environnement release absent (404), aucun paramètre distant modifié.
Les protections classiques dev/main sont absentes (API 404), mais un ruleset
actif nommé main existe ; ses règles sont examinées séparément.

Le ruleset main actif (24821745) exige Unit Tests et AI Eval Framework,
avec branche à jour et zéro approbation obligatoire. Les noms des deux checks
sont conservés avec leurs validations effectives ; les trois checks V10
supplémentaires sont à ajouter manuellement et dev est à protéger.

Validation des corrections finales : 50 tests ciblés passed (dont 14 tests des
guards release avec commandes Git/REST mockées ; aucun tag réel), Actionlint
1.7.7 PASS, configuration YAML/permissions/SemVer PASS, diff --check PASS.
Dockerfiles et configuration runtime inchangés : les builds/smoke précédents
restent applicables. La CI distante de la PR déterminera les résultats effectifs.
