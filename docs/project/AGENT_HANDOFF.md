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
