# Correction du cycle de vie DEMO — validation locale

## Cause vérifiée

L'ancien preflight appelait `demo_content_summary` puis `verify_content`, qui
comparent tous les champs des documents initiaux avec le seed : prix, nom,
catégorie, stock et événements de commandes inclus. Les ajouts n'étaient acceptés
que s'ils appartenaient à un manifeste de test technique. Cette règle convenait
à un audit du seed, mais interdisait une base de travail métier persistante.

Lecture de la DEMO existante : manifeste original intact, aucun identifiant de
seed manquant, un produit initial avec un stock différent. Un mouvement
BACKOFFICE enregistré explique exactement son stock avant/après et son delta.
C'est une opération compatible avec l'usage métier autorisé ; les données ne
permettent pas de prouver si elle a été envoyée depuis le frontend ou un autre
client de l'API. PostgreSQL n'est pas la cause du refus constaté.

Les comptes existants sont 554 produits, 2 018 commandes, 3 040 mouvements et
7 manifestes ; RAG PostgreSQL : 0 ligne. L'empreinte complète de toutes les
collections Mongo avant/après le développement reste :
`076dcd679bfb5fceec5636e7f924ff1abee5f5dac9ed27ac1f5c7cb7fbe46117`.
La nouvelle vérification passe sur cette base en lecture seule. Aucun démarrage
d'application, seed, reset, suppression ni migration n'a été exécuté dessus.
Aucune écriture DEV/E2E n'a été réalisée.

## Architecture livrée

Une seule base DEMO physique, avec séparation logique : générateur versionné et
manifeste original immuables ; collections métier mutables/persistantes.
L'initialisation conserve son validateur strict. Les reprises contrôlent la
provenance, les identités immuables, les historiques du seed, les références,
les opérations uniques et la structure métier actuelle. Les modifications
ordinaires n'ont pas besoin d'un marqueur de test artificiel.

Le lanceur tient un verrou PostgreSQL partagé avec le reset. Seule une DEMO
réellement absente peut être initialisée, après confirmation interactive.
Une collection existante, même vide, entraîne une vérification, jamais un reseed.
Les identités indépendantes Mongo/PG, les noms exacts et le namespace public
restent obligatoires. Le reset ajoute un marqueur persistant lié aux instances ;
une interruption entre stores laisse `reset_pending`, refusé au démarrage.

## Scénarios exercés

Le harness opt-in utilise deux nouvelles instances tmpfs indépendantes, Mongo
rs0 sur 28027 et PostgreSQL pgvector sur 56432. Il refuse toute autre URI et toute
DEMO préexistante sur ces instances. Les fournisseurs d'identité de référence
sont injectés uniquement dans les processus de test ; aucune option de bypass
n'a été ajoutée au code de production.

| Scénario | Résultat vérifié |
| --- | --- |
| DEMO absente | Initialisation contrôlée unique, puis lancement API/Vue |
| Relancement sans modification | Documents et checkpoint conservés |
| Nom/prix/catégorie d'un produit initial modifiés | Redémarrage accepté, valeurs conservées |
| Stock ajusté avec replay du même operation_id | Redémarrage accepté, delta appliqué une seule fois |
| Commande créée pour un nouveau customer_id | Redémarrage accepté, owner et réservation conservés |
| Commande annulée, puis annulation rejouée | Redémarrage accepté, stock restitué une seule fois |
| Nouveau produit ordinaire sans manifeste technique | Redémarrage accepté, produit conservé |
| Deuxième lanceur pendant le verrou | Refus |
| Collection étrangère / corruption / identité incorrecte | Refus explicite, aucune réparation automatique |
| Initialisation partielle | Refus sans seed |
| Cibles DEV/E2E et autres noms | Refus avant connexion aux stores |
| Reset sans terminal ou avec mauvaise confirmation | Refus sans écriture |
| Plan transactionnel de reset | Simulation seule : aucun drop/DDL, pas de listCollections dans la transaction |

Sept démarrages réels Uvicorn/Vite ont répondu aux sondes `/api/health`,
`/api/products` et `/products`, puis ont été arrêtés par SIGINT. Les snapshots
Mongo complets et les cinq tables PostgreSQL (avec un checkpoint réellement
persisté) sont comparés avant/après chaque démarrage. Les opérations métier
passent par les use cases/repositories existants ; ce n'est pas une nouvelle
campagne Playwright du frontend et aucune évaluation LLM réelle n'est exécutée.

Résultats locaux : **1 461 tests déterministes réussis**, 23 tests d’intégration
exclus de la suite par défaut ; **1 scénario d’intégration réussi en 28,09 s**,
avec sept démarrages réels ; **29 tests workspace ciblés réussis**. `bash -n`,
la validation YAML/isolation Compose, `docker compose config --quiet` et
`git diff --check` réussissent également. Les conteneurs temporaires sont supprimés après
validation ; les conteneurs et volumes normaux ne sont pas concernés.

## Fichiers concernés

- `src/stockpilot/demo_workspace.py` : validation du workspace mutable.
- `src/stockpilot/demo_lifecycle.py` : verrou, initialisation et reset confirmés.
- `src/stockpilot/demo_runtime.py`, `demo_persistence.py` : preflight et schéma approuvé.
- `scripts/run_demo.py`, `check_demo.py`, `reset_demo.py`, `reset-demo.sh` : commandes autonomes.
- `scripts/verify_demo_dataset.py`, `verify_demo_runtime.py` : audit du workspace courant.
- `scripts/tests/demo-compose.yml`, `tests/test_demo_lifecycle_integration.py`,
  `test_demo_workspace.py`, `test_demo_launcher.py`, `test_demo_runtime.py` : non-régression.
- `README.md`, `docs/demo-runbook.md`, `docs/demo-dataset.md`, ce rapport : exploitation.

## Exploitation et limites

Démarrer/reprendre : `bash scripts/run-demo.sh`. Arrêter : `Ctrl+C`.
Diagnostiquer sans écrire : `.venv/bin/python -m scripts.check_demo`.
Sauvegarder puis réinitialiser volontairement : `bash scripts/reset-demo.sh`,
avec la phrase exacte `RESET stockpilot_demo`. Voir le runbook pour les backups.

Aucun reset réel n'a été exécuté, même sur les instances temporaires, conformément
à la restriction de cette mission. Son refus et son plan sont testés ; son
exécution destructive complète n'est donc pas revendiquée comme validée.
Mongo/PG n'ont pas de transaction commune ; le marqueur pending empêche une
reprise normale après une interruption du reset. Une API lancée manuellement
hors du lanceur ne participe pas au verrou : arrêter tous les clients avant reset.

Le preflight lit les collections métier : coût linéaire acceptable pour cette
DEMO locale, à réévaluer si elle croît fortement. Il ne reconstitue pas le stock
à partir d'un ledger incomplet. Les corruptions de provenance et schémas inconnus
restent refusées ; sauvegarde/diagnostic/restauration sont alors nécessaires.
Les imports CSV optionnels ne sont pas activés automatiquement. L'authentification,
le moteur ORDER, les transactions et l'idempotence métier ne sont pas modifiés.
La phase de développement et de validation a été réalisée sans commit ni push ;
la publication Git fait l’objet d’une autorisation distincte.
