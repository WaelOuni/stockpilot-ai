# V11.2 lot 1A : sauvegardes et restauration isolée

**Procédure préparée, NON exécutée.** Chaque phase ci-dessous requiert accord
explicite : pause des écritures, sauvegarde, création des instances temporaires,
restauration, nettoyage, puis éventuellement maintenance. Aucun accord de
maintenance n'est déduit d'un accord de sauvegarde.

## Couverture et cohérence

Mongo : protéger `stockpilot`, `stockpilot_demo`, `stockpilot_e2e`. Le dump complet
sans filtre inclut également les autres bases exportables, dont les bases de tests
historiques et admin/config selon les règles de l'outil. L'inventaire actuel compte
175 bases hors local, environ 31 MiB alloués ; ce n'est pas une taille de dump.
`local` et la configuration physique du replica set ne sont pas des données
restaurées par mongodump : conserver séparément les identités/configurations de
l'instance, les volumes et les plans privés. Ne supprimer aucune base auxiliaire.

Choix : `mongodump --oplog --archive --gzip` complet, puis restauration complète
`mongorestore --oplogReplay`. Ne pas ajouter --db, --collection, --nsInclude,
--nsExclude ou renommage de namespace : filtrage et replay ne sont pas compatibles.
Les transactions applicatives en cours doivent être terminées avant la capture.
Aucun DDL, seed, reset, import, campagne E2E ou transaction métier pendant celle-ci.
L'oplog couvre les écritures durant le dump Mongo, mais ne coordonne pas Mongo
avec PostgreSQL. Une pause des **écrivains applicatifs** est donc requise du relevé
initial jusqu'aux dumps et aux comparaisons finales. Ne pas verrouiller les bases
avec fsyncLock ni modifier les rôles, replica set ou isolation des services.

PostgreSQL : protéger les cinq bases non-template actuellement présentes :
`postgres`, `stockpilot`, `stockpilot_demo`, `stockpilot_e2e`, **stockpilot_eval**.
Un dump custom --create par base conserve données, schémas, contraintes, index,
séquences, propriétaires et ACL. Un `pg_dumpall --globals-only` conserve rôles,
appartenances et tablespaces, y compris les vérificateurs de mots de passe : ce
fichier est sensible et doit rester privé. Tablespaces actuels : pg_default et
pg_global uniquement. Les extensions vector nécessaires sont fournies par la
même image pgvector/pg16. pg_dump donne un snapshot cohérent par base, pas un
point atomique entre cinq bases ou avec Mongo : la pause est nécessaire.

Outils constatés : Mongo Database Tools 100.19.1 ; pg_dump 16.15.
Dumps = charge de lecture/CPU/I/O ; aucune écriture de données métier attendue.
La durée de pause dépend des tailles et des vérifications : ne pas promettre une
durée avant mesure. Les API et tests écrivains restent arrêtés pendant la capture,
mais les bases restent en fonctionnement. Tout écrivain externe non maîtrisé
empêche de déclarer la cohérence interstores. Aucun arrêt n'a été réalisé ici.

Sources : [Mongo dump](https://www.mongodb.com/docs/database-tools/mongodump/),
[Mongo restore](https://www.mongodb.com/docs/database-tools/mongorestore/),
[pg_dump 16](https://www.postgresql.org/docs/16/app-pgdump.html),
[pg_dumpall 16](https://www.postgresql.org/docs/16/app-pg-dumpall.html).

## 1. Préflight et pause : accord préalable

Inventorier les processus qui écrivent : API DEV, éventuelle DEMO, tests E2E,
ingestion/evals, CLI et clients externes. Utiliser `ss -lntp` et les terminaux
propriétaires pour identifier puis arrêter proprement uniquement ces écrivains,
après accord. Attendre les requêtes/transactions en cours et vérifier l'absence
de reprise automatique. Ne pas arrêter Mongo/PostgreSQL, Apache ni Docker Desktop.
Ne pas tuer un PID historique ou faire pkill général. Relever en lecture les
transactions longues (`pg_stat_activity`, `db.currentOp`), sans afficher les
requêtes ni leurs paramètres. Aucune modification de données pour imposer la pause.

## 2. Capture et dumps : commandes proposées, après accord

Depuis la racine du dépôt, dans un shell privé sans `set -x`. Choisir un chemin
**nouveau** ; ne jamais écraser un backup existant. Le stockage hôte contient des
données utilisateurs, messages, checkpoints et des vérificateurs de mots de passe.
Pas de logs, dumps ou manifestes en artefacts GitHub ou dans le repository.

```bash
set -euo pipefail
umask 077
BACKUP_DIR=/tmp/stockpilot-v112-lot1a-backup-approved
mkdir -m 700 "$BACKUP_DIR"
```

Pour conservation durable, préférer ensuite un répertoire sauvegardé privé hors
Git : /tmp est éphémère. Vérifier espace libre et permissions avant capture.
Créer un contexte privé à partir des inventaires réels, sans credentials :

```bash
export BACKUP_DIR
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import json, os, subprocess
from scripts.demo_network_snapshot import collect
root = Path(os.environ['BACKUP_DIR'])
state, _ = collect()  # Lecture seule ; ne persiste aucun environnement Docker.
mongo = json.loads(subprocess.check_output([
    'docker','exec','stockpilot-mongodb','mongosh','--quiet','--eval',
    'print(JSON.stringify(db.adminCommand({listDatabases:1,nameOnly:true}).databases.map(d=>d.name).filter(n=>n!=="local").sort()))']))
pg = json.loads(subprocess.check_output(['docker','inspect','stockpilot-postgres']))[0]
user = dict(v.split('=',1) for v in pg['Config']['Env'])['POSTGRES_USER']
result = subprocess.run(['docker','exec','-i','stockpilot-postgres','psql','-X','-At','-U',user,'-d','postgres','-v','ON_ERROR_STOP=1'],
    input=b"SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname;", capture_output=True, check=True)
postgres = result.stdout.decode().splitlines()
assert postgres == ['postgres','stockpilot','stockpilot_demo','stockpilot_e2e','stockpilot_eval']
context = {'started_at_utc':datetime.now(timezone.utc).isoformat(), 'mongo_databases':mongo,
           'postgres_databases':postgres, 'source_fingerprint':state['fingerprint']}
with (root/'backup-context.json').open('x') as stream: json.dump(context,stream,indent=2)
PY
```

Capturer les empreintes de données/options/indexes, privées, puis les dumps :

```bash
docker exec -i stockpilot-mongodb mongosh --quiet --file /dev/stdin < scripts/backup/mongo-fingerprint.js > "$BACKUP_DIR/mongo-before.json" 2> "$BACKUP_DIR/mongo-fingerprint-before.log"
docker exec -i stockpilot-postgres sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1' < scripts/backup/postgres-globals-fingerprint.sql > "$BACKUP_DIR/globals-before.txt" 2> "$BACKUP_DIR/globals-fingerprint-before.log"
for database in postgres stockpilot stockpilot_demo stockpilot_e2e stockpilot_eval; do
  docker exec -i stockpilot-postgres sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1' sh "$database" < scripts/backup/postgres-fingerprint.sql > "$BACKUP_DIR/$database-before.txt" 2> "$BACKUP_DIR/$database-fingerprint-before.log"
done

docker exec stockpilot-mongodb mongodump --host 127.0.0.1:27017 --oplog --archive --gzip --quiet > "$BACKUP_DIR/mongo-full.archive.gz" 2> "$BACKUP_DIR/mongo-dump.log"
docker exec stockpilot-postgres sh -c 'exec pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$BACKUP_DIR/postgres-globals.sql" 2> "$BACKUP_DIR/postgres-globals.log"
for database in postgres stockpilot stockpilot_demo stockpilot_e2e stockpilot_eval; do
  docker exec stockpilot-postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" --format=custom --create --lock-wait-timeout=5s --dbname="$1"' sh "$database" > "$BACKUP_DIR/$database.dump" 2> "$BACKUP_DIR/$database-dump.log"
done
```

Les mots de passe restent dans la configuration serveur, jamais dans les arguments
ou l'historique. La connexion se fait à l'intérieur des conteneurs connus. Aucun
fichier n'est créé dans leurs volumes. Les diagnostics restent privés : ne pas les
coller dans un rapport ; en cas d'échec, conserver les fichiers partiels comme
non validés et ne pas créer un manifeste de réussite.

Reprendre les mêmes empreintes dans mongo-after.json et *-after.txt, comparer
**avant de reprendre les écrivains**. Toute différence inexpliquée invalide la
cohérence annoncée ; ne pas contourner ce contrôle. La pause ne peut être levée
qu'après décision opérateur, même si les dumps échouent.

```bash
docker exec -i stockpilot-mongodb mongosh --quiet --file /dev/stdin < scripts/backup/mongo-fingerprint.js > "$BACKUP_DIR/mongo-after.json" 2> "$BACKUP_DIR/mongo-fingerprint-after.log"
cmp "$BACKUP_DIR/mongo-before.json" "$BACKUP_DIR/mongo-after.json"
for database in postgres stockpilot stockpilot_demo stockpilot_e2e stockpilot_eval; do
  docker exec -i stockpilot-postgres sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1' sh "$database" < scripts/backup/postgres-fingerprint.sql > "$BACKUP_DIR/$database-after.txt" 2> "$BACKUP_DIR/$database-fingerprint-after.log"
  cmp "$BACKUP_DIR/$database-before.txt" "$BACKUP_DIR/$database-after.txt"
done
docker exec -i stockpilot-postgres sh -c 'exec psql -X -At -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1' < scripts/backup/postgres-globals-fingerprint.sql > "$BACKUP_DIR/globals-after.txt" 2> "$BACKUP_DIR/globals-fingerprint-after.log"
cmp "$BACKUP_DIR/globals-before.txt" "$BACKUP_DIR/globals-after.txt"
.venv/bin/python scripts/demo_backup_manifest.py "$BACKUP_DIR"
.venv/bin/python scripts/demo_backup_manifest.py "$BACKUP_DIR" --check
```

Le manifeste ne contient que couverture, dates de fichiers, tailles, SHA-256 et
statuts explicites. Les contrôles CRC gzip et catalogues pg_restore ne prouvent
pas une restauration complète. `restoration_tested` reste false. L'opérateur doit
également vérifier que chaque commande de dump est sortie avec code zéro.

## 3. Restauration temporaire : accord séparé obligatoire

Aucune commande ci-dessous n'a été exécutée. Vérifier les empreintes avant import.
Instances neuves, network=none, aucun port publié, tmpfs uniquement. Le répertoire
backup est monté read-only ; aucun montage des trois volumes existants ni du socket
Docker. Aucun serveur temporaire ne reçoit l'environnement privé des bases actives.

```bash
MONGO_IMAGE=sha256:d731d77bfd7afd66bd487bdf627b5bf7ce4c3602ec461d635977021db529ebbc
POSTGRES_IMAGE=sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a
TEMP_MONGO=v112-lot1a-mongo-restore
TEMP_POSTGRES=v112-lot1a-postgres-restore
# Les noms doivent être libres ; sinon arrêter, ne pas réutiliser les conteneurs.
TEMP_MONGO_ID=$(docker run -d --name "$TEMP_MONGO" --label stockpilot.backup-test=v112-lot1a --network none --tmpfs /data/db --tmpfs /data/configdb -v "$BACKUP_DIR:/backup:ro" "$MONGO_IMAGE" mongod --replSet v112_restore_rs --bind_ip 127.0.0.1 --setParameter ttlMonitorEnabled=false)
TEMP_POSTGRES_ID=$(docker run -d --name "$TEMP_POSTGRES" --label stockpilot.backup-test=v112-lot1a --network none --tmpfs /var/lib/postgresql/data -v "$BACKUP_DIR:/backup:ro" -e POSTGRES_USER=v112_restore_operator -e POSTGRES_DB=v112_restore_control -e POSTGRES_HOST_AUTH_METHOD=trust "$POSTGRES_IMAGE")
```

Inspecter les IDs enregistrés : label attendu, network=none, PortBindings vide,
images exactes, aucun volume de production, uniquement tmpfs et backup read-only.
Échec d'un démarrage = arrêter la procédure, pas de nettoyage global. L'auth trust
concerne seulement le PostgreSQL temporaire sans réseau, pas les bases actives.

Attendre mongod/pg_isready. Initialiser **uniquement** le replica set temporaire,
puis attendre PRIMARY. Les nouvelles identités d'instance doivent être différentes
de rs0 `6ac95bfeb8f0f616f58f6d93` et PostgreSQL `7694703380913774630`.

```bash
docker exec "$TEMP_MONGO_ID" mongosh --quiet --eval 'rs.initiate({_id:"v112_restore_rs",members:[{_id:0,host:"localhost:27017"}]})'
docker exec "$TEMP_MONGO_ID" mongosh --quiet --eval 'printjson(rs.status())'
docker exec "$TEMP_POSTGRES_ID" pg_isready -U v112_restore_operator -d v112_restore_control

docker exec "$TEMP_MONGO_ID" mongorestore --host 127.0.0.1:27017 --archive=/backup/mongo-full.archive.gz --gzip --oplogReplay --stopOnError --quiet 2> "$BACKUP_DIR/mongo-restore.log"
docker exec "$TEMP_POSTGRES_ID" psql -X -U v112_restore_operator -d v112_restore_control -v ON_ERROR_STOP=1 -f /backup/postgres-globals.sql > "$BACKUP_DIR/globals-restore.log" 2>&1
# Les tablespaces intégrés sont initialement possédés par le bootstrap temporaire.
# Rétablir leur owner source stockpilot uniquement dans cette instance temporaire.
docker exec "$TEMP_POSTGRES_ID" psql -X -U v112_restore_operator -d v112_restore_control -v ON_ERROR_STOP=1 -c 'ALTER TABLESPACE pg_default OWNER TO stockpilot; ALTER TABLESPACE pg_global OWNER TO stockpilot;' > "$BACKUP_DIR/tablespaces-restore.log" 2>&1
# postgres existe dans toute instance neuve : supprimer seulement la base vide
# de CE conteneur temporaire, après vérification de son ID et de l'isolation.
docker exec "$TEMP_POSTGRES_ID" dropdb -U v112_restore_operator postgres
for database in postgres stockpilot stockpilot_demo stockpilot_e2e stockpilot_eval; do
  docker exec "$TEMP_POSTGRES_ID" pg_restore -U v112_restore_operator --exit-on-error --create --dbname=v112_restore_control "/backup/$database.dump" > "$BACKUP_DIR/$database-restore.log" 2>&1
done
```

Ne pas utiliser --drop contre Mongo ni restaurer le replica set de production.
Le dump Mongo full-oplog se restaure sans filtre ; les UUID d'instance/collections
peuvent être nouveaux, tandis que les _id des documents et identités métier doivent
être identiques. Le moniteur TTL temporaire est désactivé pour ne pas expirer des
copies avant comparaison. Ne jamais démarrer StockPilot sur ces copies : son
startup pourrait initialiser des index/schémas et fausser la preuve de restauration.

Exécuter mongo-fingerprint.js par docker exec -i contre TEMP_MONGO_ID ; comparer
avec mongo-before.json. Exécuter postgres-fingerprint.sql dans chaque base contre
TEMP_POSTGRES_ID avec utilisateur v112_restore_operator ; comparer aux *-before.txt.
Vérifier collections/options/indexes, nombre et SHA-256 des documents (dont orders,
products, stock_movements), tables et contenus (dont checkpoints/knowledge_chunks),
index et séquences. Vérifier également propriétaires/ACL, rôles/appartenances,
extensions/contraintes à partir des catalogues source et restaurés ; garder les
preuves privées. Le rôle de contrôle temporaire est une différence attendue.
Aucune opération de création/annulation de commande ou de stock n'est nécessaire :
les empreintes comparent les données sans les modifier. Les empreintes de lignes
requièrent les mêmes versions, encodages et réglages serveur.

Commandes de comparaison, uniquement après restauration autorisée :

```bash
docker exec -i "$TEMP_MONGO_ID" mongosh --quiet --file /dev/stdin < scripts/backup/mongo-fingerprint.js > "$BACKUP_DIR/mongo-restored.json" 2> "$BACKUP_DIR/mongo-restored-fingerprint.log"
cmp "$BACKUP_DIR/mongo-before.json" "$BACKUP_DIR/mongo-restored.json"
for database in postgres stockpilot stockpilot_demo stockpilot_e2e stockpilot_eval; do
  docker exec -i "$TEMP_POSTGRES_ID" psql -X -At -U v112_restore_operator -d "$database" -v ON_ERROR_STOP=1 < scripts/backup/postgres-fingerprint.sql > "$BACKUP_DIR/$database-restored.txt" 2> "$BACKUP_DIR/$database-restored-fingerprint.log"
  cmp "$BACKUP_DIR/$database-before.txt" "$BACKUP_DIR/$database-restored.txt"
done
docker exec -i "$TEMP_POSTGRES_ID" psql -X -At -U v112_restore_operator -d postgres -v ON_ERROR_STOP=1 < scripts/backup/postgres-globals-fingerprint.sql > "$BACKUP_DIR/globals-restored.txt" 2> "$BACKUP_DIR/globals-restored-fingerprint.log"
cmp "$BACKUP_DIR/globals-before.txt" "$BACKUP_DIR/globals-restored.txt"
.venv/bin/python scripts/demo_backup_manifest.py "$BACKUP_DIR" --check
```

Après réussite réelle, produire une preuve privée distincte restoration-verification.json
avec dates, IDs temporaires, hashes du manifeste et des empreintes, statuts des
comparaisons et absence de ports/volumes actifs. Ne pas modifier artificiellement
restoration_tested avant cette preuve. Une lecture de catalogue ou dry-run ne suffit pas.

## 4. Nettoyage : après validation des résultats uniquement

Réinspecter les IDs/labels/isolation enregistrés. Supprimer seulement les deux IDs
créés pour ce test, jamais les noms des instances actives ni leurs volumes :

```bash
# Accord explicite de nettoyage requis ; IDs contrôlés, non vides et distincts.
docker rm -f "$TEMP_MONGO_ID" "$TEMP_POSTGRES_ID"
```

Pas de volume prune/down -v. Les archives/preuves privées sont conservées. Si les
IDs ne sont plus identifiables, demander validation avant tout nettoyage.

## 5. Maintenance future : reste interdite

Les plans privés /tmp/stockpilot-v112-network-reviewed/{loopback,rollback}.compose.json
sont corrigés : source WSL existante /home/waelko/projects/stockpilot-ai/docker/init-replica-set.js,
permissions 0644, mount read-only, images exactes et trois volumes external conservés.
Les validations Compose et dry-runs ne modifient aucun service. Le générateur
scripts/demo_network_snapshot.py refuse désormais un bind ambigu/inaccessible ou
writable et utilise le chemin WSL canonique pour le plan.

Après sauvegarde ET restauration validées, approbation de maintenance distincte,
nouveau relevé à l'arrêt des écrivains et contrôle Windows :

```bash
docker compose -p stockpilot-ai -f /tmp/stockpilot-v112-network-reviewed/loopback.compose.json config --quiet
docker compose -p stockpilot-ai -f /tmp/stockpilot-v112-network-reviewed/loopback.compose.json up -d --no-deps --no-build --pull never mongodb postgres
```

Retour arrière après approbation : même commande avec rollback.compose.json.
Ne jamais ajouter --remove-orphans, --renew-anon-volumes ou suppression de volume.
Le retour arrière rétablit les anciennes expositions réseau larges. Les contrôles
post-maintenance restent ceux de docs/demo-network-isolation.md. Toute dérive de
configuration depuis les plans capturés nécessite une nouvelle validation.

Verdict tant que dumps et restauration restent non exécutés : **NO-GO**.
