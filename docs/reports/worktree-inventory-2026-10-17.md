# Inventaire des worktrees — 17 octobre 2026

Snapshot de pré-nettoyage, avant publication des nouveaux chantiers B/C/D/E.
Baseline de comparaison `origin/main=b503af9`. Les branches des travaux courants
ont avancé depuis ; leur état de livraison est dans le rapport consolidé.
Toutes les entrées sont conservées. Ce document ne contient aucun log ou secret.

| Chemin | Branche | HEAD initial | État initial | Commits hors main / hors remotes |
|---|---|---|---|---|
| `/home/waelko/projects/stockpilot-ai` | `main` | `6f0949676e33` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-order-quantity-guard` | `fix/v015-order-integrity` | `b5c2f41093b7` | modifications | 0 / 0 |
| `/home/waelko/projects/stockpilot-product-readiness` | `docs/commercial-pilot-readiness` | `b503af9b2fa3` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v014-hotfix` | `fix/v014-order-hotfix` | `10403877f265` | modifications | 0 / 0 |
| `/home/waelko/projects/stockpilot-v1232-conversation-audit` | `fix/v016-product-reference-routing` | `e1313edf05ba` | modifications | 0 / 0 |
| `/home/waelko/projects/stockpilot-v1238-conversation-fix` | `fix/v017-conversation-quantity-routing` | `0b0eba938c6b` | modifications | 0 / 0 |
| `/home/waelko/projects/stockpilot-v131-ci` | `chore/v131-test-safety-ci` | `41b0d49da335` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v131-docs` | `chore/v131-docs-hygiene` | `f210ec4e796f` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v132-admin-sessions` | `security/v132-admin-sessions` | `e63285b19939` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v132-auth-callers` | `security/v132-auth-callers` | `392f94d2465e` | propre | 1 / 1 |
| `/home/waelko/projects/stockpilot-v132-composed` | `validation/v132-composed` | `739d070dea73` | propre | 20 / 0 |
| `/home/waelko/projects/stockpilot-v132-final` | `détaché` | `05f0d7852d1f` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v132-foundation` | `security/v132-foundation` | `064b7ad166e1` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v132-http-boundary` | `security/v132-http-boundary` | `9aa1f3ce7875` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v132-input-limits` | `security/v132-input-limits` | `b8e5fcb5e8ef` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v132-io-measurement` | `security/v132-io-measurement` | `6ac9f271ec33` | propre | 1 / 1 |
| `/home/waelko/projects/stockpilot-v1321-integration` | `validation/v1321-integration` | `58d969535f85` | propre | 20 / 20 |
| `/home/waelko/projects/stockpilot-v133-validation` | `validation/v133-integrated-demo` | `b503af9b2fa3` | modifications | 0 / 0 |
| `/home/waelko/projects/stockpilot-v13c1-advice` | `feature/v13c1-catalogue-advice` | `f1bafc98c47f` | propre | 0 / 0 |
| `/home/waelko/projects/stockpilot-v14-design` | `docs/v14-saas-design` | `b503af9b2fa3` | propre | 0 / 0 |
| `/tmp/stockpilot-ci-order-integration-coverage` | `fix/ci-order-integration-coverage` | `64ca016ac662` | absent | — / — |
| `/tmp/stockpilot-order-product-resolution` | `fix/order-product-resolution` | `1689b0cc53cc` | absent | — / — |
| `/tmp/stockpilot-order-product-resolution-recovery` | `fix/order-product-resolution-recovery` | `9e38a5c55ad5` | absent | — / — |
| `/tmp/stockpilot-release-v0.1.2` | `release/v0.1.2` | `aa20c473e61a` | absent | — / — |
| `/tmp/stockpilot-v12-preflight-20261013` | `détaché` | `3963554d4934` | absent | — / — |
| `/tmp/stockpilot-v125-order-natural` | `fix/order-natural-language-v013` | `9bbcabffa568` | Git invalide | — / — |
| `/tmp/stockpilot-v127-release` | `détaché` | `94b242f7c18c` | absent | — / — |

Le worktree `stockpilot-e2e-safety` puis `stockpilot-order-confirmation-ux` ont été
créés ensuite pour les corrections B/C. B3 est conservé ; le worktree C a été
retiré après fusion #34, libération par son agent et preuves de sûreté détaillées
dans le rapport consolidé.

Les huit worktrees propres et inclus dans main inspectés contiennent des logs
ignorés non archivés ; C1 conserve aussi des résultats navigateur. Aucun cwd actif
ou lien Compose direct observé dans ces huit worktrees. Cela ne prouve pas que tout
artefact ignoré est inutile ni que tout ancien montage traduit par Docker Desktop
est sans dépendance : aucune suppression sur cette seule base. Les worktrees
ayant `reports/` non suivi ou des commits non publiés sont conservés sans ambiguïté.

L'inventaire de `/home/waelko/projects/` comprend en plus des fichiers HTML et
`:Zone.Identifier`, qui ne sont pas des worktrees Git. Aucune suppression de ces
fichiers ordinaires. Aucun prune des métadonnées `/tmp` absentes.

Suppression effectuée : **seul le nouveau worktree C terminé**, par
`git worktree remove` sans force. Empreinte retirée : **152 028 KiB (~148,5 MiB)**,
sans prétendre isoler les blocs physiques libérés sur le système. Dépôt principal,
candidate, données et secrets inchangés par cette phase. Avant une reprise du
nettoyage, refaire état Git, remotes/PR, cwd et fichiers ouverts, dépendances de
montages, utilité/archivage des rapports ; utiliser uniquement `git worktree remove`
sans force quand toutes les conditions de conservation sont levées.
