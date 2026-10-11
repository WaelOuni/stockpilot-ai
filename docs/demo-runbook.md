# DEMO locale persistante

La DEMO utilise exclusivement MongoDB `stockpilot_demo` et PostgreSQL
`stockpilot_demo/public`. DEV (`stockpilot`) et E2E (`stockpilot_e2e`) sont exclus.
Les anciennes mesures V6/V7 sont des instantanés historiques, pas une contrainte
interdisant de modifier les produits de la démo.

## Démarrer, arrêter, reprendre

Avec les dépendances Python/Vue et les services Mongo rs0/PostgreSQL pgvector déjà
installés, depuis la racine, Node/npm sur PATH :

```sh
bash scripts/run-demo.sh
```

Lors de la toute première initialisation, le lanceur affiche la cible et demande
exactement `INITIALIZE stockpilot_demo` dans un terminal interactif. Il initialise
le seed Mongo transactionnellement ; il crée les schémas PostgreSQL uniquement si
la base DEMO est absente. Une base PostgreSQL préexistante doit déjà être compatible.
Il ne copie aucune donnée DEV/E2E, ne télécharge pas de modèle et n’ingère aucun RAG.
Une confirmation refusée, EOF ou une entrée non interactive n’autorise aucun seed.

Sur une DEMO existante valide, le même lancement conserve toutes les données,
y compris les modifications des produits du seed : nom, catégorie, prix et stock.
Créations de produits, mouvements de stock, nouvelles commandes, changements de
statut et annulations sont acceptés selon les autorisations métier existantes.
Le stock n’est jamais recalculé à partir du seed. Les replays métier conservent
l’idempotence existante. Renommer un produit ne réécrit pas ses commandes historiques.

Le lanceur demande les identifiants administrateur privés de la session ; les
sessions/cookies sont en mémoire et ne survivent pas à l’arrêt. Les produits,
commandes/owners/events, mouvements, éventuels reçus d’import et checkpoints
PostgreSQL sont persistants. Il n’existe pas de collection clients autonome : les
clients sont représentés par `customer_id` et l’authentification JWT existante.
Le secret JWT doit rester configuré côté serveur, jamais côté Vite.

UI : **http://127.0.0.1:5175** ; API : **http://127.0.0.1:8003**.
`Ctrl+C` arrête uniquement les deux processus démarrés par ce lanceur et libère
le verrou DEMO. Relancer la même commande reprend les données. Aucune sauvegarde
ni réinitialisation automatique n’a lieu à l’arrêt. Ne pas exposer cette auth
locale/cookie HTTP sur Internet.

## Référence immuable et base de travail

Le générateur versionné `demo_dataset.py`, seed `20261010`, fournit la référence
500 produits / 2 000 commandes / 3 000 mouvements. Son hash reste
`a7112fc09fb3afd2618dfd3bb7b8f5a2e3f83203b5e40d17e64083bdeee8d22c`.
Il est recalculé/vérifié en mémoire. Le manifeste initial stocké dans
`demo_manifests` doit correspondre exactement au générateur : il décrit la
provenance du seed, pas une promesse d’inventaire inchangé.

Une seule base physique suffit : les collections métier sont les copies de
travail mutables. `demo_dataset_id` indique leur provenance. Les noms/prix/stocks
courants ne sont plus comparés aux valeurs d’ouverture. Les identités du seed,
les payloads initiaux des commandes et les historiques immuables restent contrôlés.
Les événements de commandes peuvent être ajoutés via les transitions autorisées.
Les mouvements existants sont immuables et de nouveaux mouvements peuvent être ajoutés.

Au démarrage, vérifier : manifeste, collections/indexes approuvés, identités
requises du seed, types/valeurs/prix finis/stocks non négatifs, unicité des
opérations, liens produits/commandes/mouvements, arithmétique des mouvements et
cohérence des statuts/events. Aucun nombre fixe de documents métier n’est imposé.
Les nouvelles données métier n’ont pas besoin d’un manifeste de test technique.
Un fingerprint séparé `working_sha256` permet de comparer deux lectures ; sa
variation normale ne provoque pas de refus.

Le modèle ne contient pas un ledger complet de toutes les réservations : le
preflight ne prétend pas reconstituer le stock depuis les mouvements seuls.
Il conserve les règles métier et leurs transactions au lieu de fabriquer un
rééquilibrage. Les API actuelles interdisent la suppression des produits,
commandes et historiques du seed ; une disparition est donc considérée comme
une initialisation partielle ou une altération extérieure.

## Garde-fous et incompatibilités

Le serveur Mongo doit être un primaire transactionnel rs0 et son `replicaSetId`
doit correspondre au serveur indépendant de `MONGO_URI`/Settings normal.
Cette comparaison ne lit aucune collection DEV. PostgreSQL doit être l’instance
Docker existante `stockpilot-postgres`, vérifiée par `system_identifier` via son
endpoint TCP et son identité Docker. RAG et checkpoints doivent désigner la même
instance DEMO, le nom exact `stockpilot_demo`, namespace public et les schémas
compatibles avec la version installée. Pas de migration silencieuse d’une base
existante. Le startup FastAPI demande toujours ses index/checkpointer déjà compatibles.

Les paramètres DEV/E2E hérités MONGO_DATABASE/POSTGRES_URL/checkpoint sont ignorés.
Les overrides privés `STOCKPILOT_DEMO_MONGO_URI`, `STOCKPILOT_DEMO_POSTGRES_URL`,
`STOCKPILOT_DEMO_CHECKPOINT_URI` restent possibles uniquement pour des aliases de
ces mêmes instances et ce nom exact. Les valeurs vides ou ambiguës sont refusées.
Le lanceur ne source pas `.env.local` et ne change aucun fichier de configuration.
Ses credentials ne sont pas transmis à Vite ; l’env Vite temporaire reste vide.

Un verrou advisory PostgreSQL partagé avec le reset est tenu pendant toute la
vie du lanceur. Un deuxième lancement ou reset concurrent refuse. Ne pas lancer
manuellement une API pointant vers DEMO hors de ce lanceur : elle ignorerait ce
verrou. Le reset refuse aussi les ports DEMO occupés.

Diagnostic sans démarrer les serveurs ni modifier les bases :

```sh
.venv/bin/python -m scripts.check_demo
```

Ce diagnostic affiche des comptes, fingerprints et champs structurels fautifs,
pas de credentials ou données clients. Manifest inconnu/altéré, collection
étrangère, mauvais index, document incomplet ou schéma PostgreSQL incompatible :
refus explicite, aucun nettoyage. Sauvegarder puis inspecter/restaurer la DEMO
connue ; ne jamais essayer un seed normal/E2E pour réparer cette base.

Une initialisation interrompue laissant collections vides ou seulement un store
prêt est refusée. Elle n’est pas rejouée automatiquement. Les commandes historiques
insert-only `prepare_demo_dataset` / `prepare_demo_postgres` peuvent compléter une
première initialisation connue après diagnostic et autorisation explicite ;
elles ne servent pas à écraser une DEMO modifiée. Le reset normal demande une
provenance/structure identifiable ; il ne détruit pas une base inconnue.

La collection `product_imports` et son index unique sont reconnus lorsqu’ils ont
été provisionnés explicitement par les mécanismes existants ; le lanceur ne les
active pas sur une DEMO qui ne les possède pas. Pas de nouvelle capacité métier.

## Sauvegarder avant un reset volontaire

Arrêter DEMO. Le reset ne sauvegarde pas automatiquement et n’impose pas de
supprimer la sauvegarde. Utiliser les outils standard, avec les endpoints privés
correspondant aux instances vérifiées et les fichiers credentials en mode 0600 :

```sh
# Exemples à adapter aux fichiers privés ; aucune commande n’est lancée automatiquement.
mongodump --config /chemin/prive/mongodump.yml --db stockpilot_demo --archive=/chemin/prive/demo-mongo.archive
pg_dump --dbname='service=stockpilot-demo' --format=custom --file=/chemin/prive/demo-postgres.dump
```

Configurer le service PostgreSQL privé (et son .pgpass) pour la base exacte
stockpilot_demo sur l’instance vérifiée. Conserver archives et credentials hors
Git, à accès restreint. Les outils de sauvegarde ne sont pas installés par le
lanceur ; une restauration est une opération distincte, pas exécutée par le reset.

## Réinitialisation explicite

```sh
bash scripts/reset-demo.sh
```

Le script vérifie noms, identités, schémas, provenance et absence de lanceur actif,
affiche les comptes/cibles et les conséquences, puis exige exactement
**`RESET stockpilot_demo`** dans un terminal interactif. Pas d’option `--yes`,
pas de variable d’environnement permettant de passer la confirmation.

Après confirmation seulement : remplacement transactionnel des contenus Mongo
approuvés par le seed et suppression des trois tables de payload checkpoints
`public.checkpoints`, `public.checkpoint_blobs`, `public.checkpoint_writes`.
Collections/indexes, `checkpoint_migrations`, RAG `knowledge_chunks` et toutes
les tables non concernées sont conservés. Aucun dropDatabase, DROP DATABASE,
drop de collection ou TRUNCATE CASCADE. DEV et E2E sont exclus.

Mongo et PostgreSQL n’ont pas de transaction commune. Un marqueur
`demo_manifests/stockpilot-demo-workspace` lie les identités des instances et
reste `reset_pending` en cas d’interruption. Le démarrage refuse ce statut.
Reprendre le reset uniquement en relançant le script et en confirmant de nouveau ;
aucune reprise destructive automatique. Si la structure/provenance a été altérée
hors des outils autorisés, préserver la sauvegarde et diagnostiquer avant restauration.

## Tester sans toucher les données existantes

Tests offline :

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
```

Tests opt-in sur DEUX NOUVELLES instances tmpfs, ports 28027/56432. Ils refusent
les URI différentes et toute DEMO déjà présente sur ces ports. Ils injectent la
référence indépendante de ces instances uniquement dans le harness de test ;
aucun override de cette protection n’est ajouté au lanceur de production.

```sh
docker compose -f scripts/tests/demo-compose.yml up -d --wait
export DEMO_LIFECYCLE_TEST_MONGO_URI='mongodb://127.0.0.1:28027/?replicaSet=rs0&directConnection=true'
export DEMO_LIFECYCLE_TEST_POSTGRES_URI='postgresql://lifecycle:lifecycle@127.0.0.1:56432/stockpilot_demo'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -m integration tests/test_demo_lifecycle_integration.py
docker compose -f scripts/tests/demo-compose.yml down -v
```

Le test initialise les stores temporaires une fois, exerce les use cases réels,
réalise sept lancements API/Vue puis compare les documents et les checkpoints.
Il teste le refus de reset sans autorisation, sans effectuer de reset réel.
Pour répéter : détruire seulement ces conteneurs tmpfs et en créer de nouveaux,
sans utiliser les instances du développeur. Ne pas utiliser les anciens verifiers
`--full-checks` : ils ont un périmètre différent et peuvent réinitialiser E2E.
