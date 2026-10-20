# Reprendre StockPilot — guide agent

## Les cinq premières vérifications

1. Vérifier le dossier et l'identité Git : `test -d /home/waelko/projects/stockpilot-ai`,
   `git -C /home/waelko/projects/stockpilot-ai rev-parse --show-toplevel`,
   `git -C /home/waelko/projects/stockpilot-ai remote get-url origin`.
   Attendu : `https://github.com/WaelOuni/stockpilot-ai.git`. Lire [AGENTS](../../AGENTS.md).
2. Depuis ce dépôt : `git status --short --branch`, `git worktree list --porcelain`.
   Ne pas nettoyer les worktrees historiques, même marqués prunable. L'[inventaire](../reports/worktree-inventory-2026-10-17.md)
   est historique ; la commande donne les entrées actuelles.
3. `git fetch origin main && git rev-parse origin/main`, puis
   `gh pr list --state open --base main`. GitHub a **dev** par défaut ;
   ne pas partir du main local ancien, de dev ou d'une PR déjà fusionnée.
4. Lire [l'état daté](PROJECT_STATUS.md), [la roadmap](ROADMAP.md),
   [les décisions](ARCHITECTURE_DECISIONS.md) et le rapport du chantier concerné
   dans [validation](../validation/) ou [rapports](../reports/).
5. Vérifier l'autorisation et le périmètre : aucun accès aux DEMO v017/V13.3,
   données, secrets, services ou ports sans accord explicite. Ne pas lancer les
   recettes DEMO présentes dans les anciens rapports. Tests : cibles jetables uniquement.

## Point de reprise V13.6.4 (20 octobre 2026)

Main distant vérifié à `85df5c4` avant consolidation ; retrouver son état courant
sur GitHub. [Parcours V13.4.3](../validation/v13.4.3-real-user-journey.md) et
[rapport V13.6](../reports/v13.6-local-consolidation.md) distinguent preuves locales,
code fusionné et installations. PR #42 (kit pilote) ouverte au relevé initial :
la réexaminer séparément, sans supposer qu'elle est intégrée.

Le checkout `/home/waelko/projects/stockpilot-ai/` à `6f09496` sert la démo
historique 5175/8003. Aucun pull/reset/checkout dessus pour suivre main. Fetch des
références puis worktree dédié seulement. Protéger aussi v017/5191, candidate/5193,
le worktree `stockpilot-v133-validation`, les preuves `stockpilot-v1343-*` et les
fichiers non suivis du worktree V13.4.3. Aucun déploiement implicite après merge.

Prochaine étape : audit B2B V13.7, puis préparation d'un test distant isolé.
Kubernetes/K3s reste une option à décider. Ni multi-tenant opérationnel ni aptitude
à une production commerciale ne sont attestés. Ne pas réutiliser le harnais local
V13.4.3 comme outil générique : voir ses limites dans le rapport publié.

## Exécuter et préparer une PR

Depuis le dépôt vérifié, choisir un **nouveau** chemin et une branche par tâche :
`git worktree add -b <branche> <nouveau-chemin> origin/main`.
Travailler uniquement dans ce worktree. Préparer ses dépendances selon
[getting-started](../getting-started.md) et [testing](../testing.md), sans copier
d'environnement privé ni utiliser le venv d'un autre worktree avec un package
éditable pointant vers d'autres sources.

Commandes de validation à la racine du worktree (Python 3.12) :

~~~sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
git diff --check
~~~

Les intégrations exigent les services jetables et URI explicitement vérifiés de
[testing](../testing.md). Les évaluations déterministes ne mesurent pas Ollama réel.
Consigner séparément ce qui est exécuté, désélectionné, ignoré ou non testé.

Relire le diff, vérifier liens/secrets et garanties ORDER. Mettre à jour seulement
les entrées concernées selon [la synchronisation](ROADMAP.md#synchronisation-légère).
Commit et push de la branche, puis `gh pr create --base main --head <branche>`.
Contrôler `gh pr checks <numéro>` sur le dernier commit poussé ; corriger uniquement
les défauts liés au chantier. Aucun merge, tag, release ou déploiement sans instruction.

Restituer : URL/base/HEAD et état de PR, fichiers changés, résultats locaux et CI
distincts, exclusions, risques/incertitudes et prochaine action humaine.
Un commit fusionné, un commit déployé et une version stable sont trois preuves distinctes.

## Reprise V13.7.1

PR [#44](https://github.com/WaelOuni/stockpilot-ai/pull/44), branche
`feature/v1371-b2b-identity-foundations`, worktree
`/home/waelko/projects/stockpilot-v1371-b2b-identity-foundations` ; base `3eba881`.
Fondations inactives : [ADR et contrats](../security/b2b-identity-foundations.md).
Le port serveur HumanAuthentication précède la résolution ; aucune clé externe
ou principal envoyé par le navigateur n'est une preuve d'authentification.
Pas de provisioning automatique ; `customer_id` reste distinct du contexte humain.
PR #44 fusionnée à `eecf8a4`, vérifiée le 20 octobre 2026.

## Reprise V13.7.2

Worktree `/home/waelko/projects/stockpilot-v1372-keycloak-oidc-bff`, branche
`feature/v1372-keycloak-oidc-bff`, base distante `eecf8a4`.
[Guide OIDC/BFF et tests isolés](../security/keycloak-bff.md).
Entrypoint indépendant : `stockpilot.bff:create_app`, BFF_MODE désactivé par défaut.
Ne pas le brancher à l'API globale : seul le flux auth/session est composé.
Le harnais crée et retire uniquement ses ressources Docker tmpfs identifiées.
Les collections d'identité/session sont distinctes, aucun provisioning au callback.
Pas de refresh ; les tokens restent transitoires côté serveur, sessions bornées
par l'ID token. Pas de readiness Internet ni de multi-tenant opérationnel.
Inspection initiale : 5 conteneurs candidate actifs, 28 conteneurs au total,
30 volumes ; absence préexistante de certains artefacts temporaires V13.4.3.
Ne pas restaurer automatiquement ni attribuer cet écart à la branche.
Checkout historique à `6f09496` inchangé ; aucun merge, déploiement ou arrêt Windows.

## Reprise V13.7.3-A

PR #45 fusionnée : main vérifié à `80da436e455c48afc5db15c8feaad950960e604f`,
distinct du HEAD de branche `26c68f9`. Worktree
`/home/waelko/projects/stockpilot-v1373a-tenant-authorization-boundary`, branche
`feature/v1373a-tenant-authorization-boundary`.
[ADR, ownership et lots B](../security/tenant-authorization-boundary.md).
Le pont BFF réutilise current() et le resolver V13.7.1, sans nouvelle route.
Ne pas traiter TenantContext ou ALLOW comme une preuve persistante de mutation.
Ownership/association sont des ports, sans adapter réel ni migration ; aucune
organisation des données historiques n'est devinée. Garder le BFF fermé aux métiers.
Le harnais Keycloak a seulement un délai client de test plus long que le timeout
du fournisseur pour observer sa réponse 503 ; aucune assertion n'est affaiblie.
Tests sur ressources jetables uniquement ; préserver les 5 services actifs
initialement constatés, sans restaurer les services arrêtés ou preuves absentes.

## Reprise V13.7.3-B1

Base réelle vérifiée : `b8fb6ee229ed64de5e00881d43c66ff79e4a1f24`, fusion de #46
(son HEAD `cb778eb` ne sert pas de base). Branche
`feature/v1373b1-tenant-catalogue-stock`, worktree Windows isolé ; aucun changement
du checkout Linux historique. [ADR](../security/tenant-catalogue-stock.md),
[guide migrations](../mongodb-migrations.md),
[validation](../validation/v13.7.3b1-tenant-catalogue-stock.md).
Les ports B2B ne sont composés dans aucune route. Ne pas remplacer l'adapter
historique ni relier le BFF aux métiers. Appliquer seulement sur une infrastructure
jetable identifiée ; aucun `apply` au démarrage. Verrou expiré/started/failed :
intervention humaine, aucun takeover automatique ou commande force.
La suite B2 exige mapping acheteur et transactions/révocation ; l'observation de
disponibilité B1 ne réserve aucun stock et ALLOW n'est pas un ticket de mutation.

## Reprise V13.7.3-B2

Base `15f4fb80eb54e05d5c30abee94ce398b8c6ec36b`, fusion réelle de #47.
Branche `feature/v1373b2-tenant-orders-atomicity`, worktree
`/home/waelko/projects/stockpilot-v1373b2-tenant-orders-atomicity`.
[ADR](../security/tenant-orders-atomicity.md), [preuves](../validation/v13.7.3b2-tenant-orders.md).
Aucune composition HTTP : le port spécialisé MongoTenantOrders garde sa propre
frontière transactionnelle ; ne pas ouvrir la politique générique A réservée.
0002 est explicite, seulement jetable, 0001 immuable ; ne pas réécrire l'historique.
Même base/replica set pour identités/grants/commandes/produits et index BFF uniques
préprovisionnés. own = created_by, pas le customer partagé. Mapping désactivé bloque
également manager ; outcome unknown impose même clé/contenu, jamais une nouvelle
commande de compensation. Le journal n'a aucun TTL. Voir les limites session,
contention, sauvegarde et futurs imports/mouvements avant activation.
