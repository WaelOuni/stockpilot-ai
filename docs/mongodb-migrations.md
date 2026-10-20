# Migrations MongoDB versionnées — B1

## Périmètre et mécanisme

Le dépôt avait des scripts explicites de préparation d'index Mongo, du SQL
versionné pour pgvector et les migrations internes du checkpointer ; aucun
framework de migrations Mongo n'était utilisé. Les scripts historiques restent
inchangés. Ce runner léger utilise PyMongo déjà présent, sans second framework.
Alembic pourra être évalué lors d'une évolution relationnelle PostgreSQL réelle ;
aucune table pgvector/checkpoint n'est modifiée ici.

Le registre est constitué des JSON embarqués dans le package sous
`infrastructure/persistence/mongodb/migrations/`. Versions entières ordonnées,
contiguës depuis 1, ID unique et description dans le registre, SHA-256 des octets
du fichier complet. Git impose LF pour ces fichiers : pas de variation Windows/Linux.
Après application, un fichier est immuable. Corriger par une nouvelle version,
jamais en modifiant une migration déjà réussie.

**0001_b2b_catalogue** crée `b2b_products`, son validateur et ses quatre index
composites ; aucun produit inséré, aucune collection historique touchée.
Les définitions sont déclaratives, limitées à la collection B2B autorisée.
Chaque définition décrit le schéma complet attendu pour cette collection à cette
version. Le gate vérifie la dernière définition appliquée par collection, tout en
validant les empreintes de toutes les versions historiques : un ajout d'index en
0002 ne rend pas le gate minimum 0001 inutilisable. Les index doivent être cumulatifs
dans ce moteur ; modifier un validateur existant ou retirer un index nécessite
une extension explicite du moteur et sa propre revue, jamais une altération implicite.
Les créations sont idempotentes si leurs options sont identiques ; une structure
incompatible provoque un refus, jamais dropIndex/collMod/dropCollection.

Historique persistant : `stockpilot_schema_migrations`, `_id` = version unique,
migration_id, description, checksum, status, started_at, finished_at, runner_id,
code d'erreur assaini si échec. `started` et `failed` bloquent la suite et le gate
de lecture ; seuls des enregistrements `succeeded` complets comptent.
Versions inconnues, trous et empreintes divergentes bloquent aussi.

Écritures majoritaires avec wtimeout borné ; lectures primary/majority. Chaque
version réussie est écrite seulement après vérification des options et index.
Une perte réseau au moment de l'écriture peut rendre son résultat incertain :
inspecter le statut, ne pas présumer un échec sans effets.

## Commandes explicites, sans cible par défaut

B1 limite volontairement **apply aux bases jetables** nommées
`stockpilot_b2b_test_<32 caractères hexadécimaux>`. La CLI exige un endpoint
loopback sur un port distinct de 27017, une URI explicitement fournie dans
`STOCKPILOT_MIGRATION_URI`, le nom de base et, pour apply, sa répétition exacte.
Aucun chargement de `.env`, aucun fallback Settings/BFF/DEMO. Une URI portant
un autre nom de base est refusée. L'URI n'est pas un argument de commande à logger.
Le runner vérifie aussi le serveur effectif : primary, absence de bases
historiques protégées et de marqueurs demo_instance, avant toute écriture.
Un nom de base ressemblant à un test ne suffit pas sur un serveur DEMO.

Exemple **uniquement sur une infrastructure jetable identifiée et vérifiée** :

```sh
export STOCKPILOT_MIGRATION_URI='mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true'
DB=stockpilot_b2b_test_0123456789abcdef0123456789abcdef
python -m stockpilot.infrastructure.persistence.mongodb.migrate status --database "$DB"
python -m stockpilot.infrastructure.persistence.mongodb.migrate plan --database "$DB"
python -m stockpilot.infrastructure.persistence.mongodb.migrate validate --database "$DB"
python -m stockpilot.infrastructure.persistence.mongodb.migrate apply --database "$DB" --confirm-disposable-database "$DB"
```

- status expose l'historique et le verrou, y compris les échecs.
- plan valide l'historique puis liste les versions manquantes dans l'ordre.
- validate contrôle versions, états terminaux et empreintes, sans exiger qu'une base vide soit déjà migrée.
- apply exige les garde-fous, prend le verrou, revalide, applique et vérifie.

Les trois commandes de lecture n'insèrent ni collection ni document. Les erreurs
renvoient un code non nul sans détails du driver. Le gate interne
`MigrationRunner.require_version(1)` valide aussi les structures/index réels ;
chaque lecture du nouvel adapter le requiert. Aucune commande n'est appelée
par import, FastAPI, BFF, Docker Compose ou les scripts de démonstration.

## Verrou, concurrence et interruption

`stockpilot_schema_migration_locks` possède un seul document `_id=schema` ;
l'insertion atomique sous index `_id` arbitre les runners. Owner aléatoire,
date d'acquisition et deadline de 300 secondes par défaut (1–3600 configurable
dans l'API interne). Aucun TTL ni index supplémentaire sur le suivi n'est admis.
Le propriétaire et la deadline sont vérifiés avant chaque DDL et avant succès.
Le propriétaire libère uniquement son verrou sur sortie normale/erreur gérée.
Un arrêt brutal conserve verrou et entrée started ; un échec géré conserve failed.

**Un verrou expiré n'est jamais repris automatiquement.** La deadline n'est pas
une preuve de décès : un ancien processus peut encore terminer une DDL Mongo.
Interdire le takeover maintient l'exclusion mutuelle même après une pause prolongée.
Une interruption entre deux DDL peut laisser collection/index partiels. Aucune
transaction globale DDL n'est prétendue. Une migration failed/started ne se relance
pas automatiquement ; aucune migration suivante n'est appliquée silencieusement.

## Récupération, sauvegarde, rollback et rétention

Il n'existe ni commande `force`, ni réparation automatique, ni rollback destructif.
Procédure humaine avant toute réparation future :

1. Arrêter et vérifier l'absence de tous les runners concernés ; vérifier aussi les opérations DDL Mongo encore en cours.
2. Identifier serveur/base, version du code, empreinte, propriétaire du verrou et historique ; conserver le diagnostic assaini.
3. Sauvegarder les collections B2B, leur suivi et les options/index ; vérifier la restauration dans une nouvelle infrastructure isolée. Préserver tout état incertain.
4. Comparer les structures effectives à la migration immuable ; établir les effets partiels et l'issue des écritures majoritaires incertaines.
5. Faire approuver un plan spécifique et sa preuve de restauration avant toute correction manuelle de suivi/verrou. Ne jamais supprimer un verrou sur la seule base de sa date.

Dans ce chantier, seule la base synthétique créée par le test est supprimée à sa
fin ; recommencer sur une nouvelle base jetable est la récupération recommandée.
L'application sur des bases existantes ou la réparation de leur suivi n'est pas
autorisée par B1. Un futur mode opérateur pour production nécessite une décision
séparée : identité persistante de cible, ACL, sauvegarde/restauration prouvée,
revue du plan et attribution explicite des données. Ne pas assouplir le regex
pour faire passer une cible existante.

Rollback applicatif : ne pas composer les adapters B2B, conserver les données et
les fichiers de migrations. Aucun retour arrière des données historiques n'est
nécessaire. Ne pas réduire une quantité/réservation ni supprimer un index pour
faire passer une ancienne version. Les changements de modèle futurs doivent
privilégier des évolutions compatibles et de nouvelles migrations.

Conserver les entrées d'historique pendant toute la vie de la base, avec les
sauvegardes et le code/registre correspondant. Aucun TTL de suivi. Un verrou
d'incident est conservé jusqu'à résolution humaine ; les locks normalement
terminés sont retirés par leur propriétaire. Les bases et fixtures de test sont
éphémères. La rétention métier et les durées de backup client restent à décider
avant de traiter des données réelles ; aucun SLA/RPO/RTO n'est attesté par B1.

## Tests

Les tests utilisent les garde-fous `tests/integration_targets.py` puis une base
UUID distincte par test sur le replica set CI. Le projet Compose de test utilise
tmpfs, ne réutilise aucun volume et ne lance aucune migration implicitement.
Un troisième service Mongo auth, port loopback 27028, fournit le contrôle réel
read-only (credentials CI synthétiques uniquement).

```sh
export MONGO_TEST_URI='mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true'
export MONGO_MIGRATION_AUTH_TEST_URI='mongodb://ci:ci@127.0.0.1:27028/?authSource=admin'
export POSTGRES_TEST_URI='postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci'
python -m pytest -q -m 'integration and not integration_optional' tests
```

La campagne couvre base vide, réexécution, suivi, divergence, version inconnue,
trous, concurrence, lock expiré, crash avec DDL partiel, arrêt avant la migration
suivante, index/collection incompatibles, cible absente, droits insuffisants,
réseau indisponible et configuration CLI incomplète. Voir le
[rapport](validation/v13.7.3b1-tenant-catalogue-stock.md) pour les résultats exécutés.
