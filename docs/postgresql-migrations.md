# Migrations PostgreSQL B2B explicites

Le RAG et le checkpointer historiques restent inchangés. Leur provisioning manuel
et AsyncPostgresSaver.setup ne sont pas réutilisés pour les tables B2B.

## Choix

Les dépendances réelles sont psycopg/pgvector, sans ORM SQLAlchemy ni Alembic.
Un runner SQL transactionnel réduit les couches : aucune dépendance ajoutée,
migration0001 empaquetée avec l'application. Il couvre le besoin actuel sans
introduire un ORM uniquement pour ses migrations.

PostgresTenantMigrations gère le schéma dédié stockpilot_b2b (ou le schéma UUID
créé par la fixture jetable), schema_migrations et le fichier immuable
0001_b2b_knowledge.sql. Historique de version/checksum/empreinte structure/dates.
Empreinte : colonnes/types/namespace/typmods/nullabilité/defaults, contraintes,
définitions et disponibilité des index. Version inconnue, checksum modifié,
dimension vectorielle changée, index ajouté ou schéma partiel : refus.

Apply prend un advisory lock transactionnel non bloquant, vérifie la cible, puis
DDL et historique sont commités ensemble. En cas de panne avant commit, PostgreSQL
annule aussi la DDL : aucune réparation destructive automatique. Un résultat de
commit réseau incertain exige status/inspection avant reprise, jamais un downgrade.

## Cibles autorisées dans ce lot

Apply refuse les ports5432 par défaut, les noms autres que stockpilot_ci ou
stockpilot_test avec suffixe autorisé, les serveurs portant des bases stockpilot,
stockpilot_e2e, stockpilot_demo* ou stockpilot_candidate*, et le marqueur
public.demo_instance. Il exige aussi confirmation du nom de base effectif et du
system_identifier PostgreSQL lu sur la cible. Les tests vérifient leur serveur
jetable via les garde-fous existants avant toute création de schéma.

Ce runner n'est pas encore autorisé pour une installation pilote persistante :
une mission future doit ajouter son manifeste/identité et son contrôle opérateur.
Ne jamais renommer une base protégée pour contourner ces garde-fous.
Le rôle jetable a les droits DDL/extension/pg_control_system nécessaires ;
préparer un rôle de migration distinct du rôle runtime avant hébergement.

## Commandes documentaires

URI dédiée dans POSTGRES_B2B_MIGRATION_URI ; aucun fallback Settings, aucune URI
dans les arguments/logs. Ne pas recopier un secret de démo.

~~~sh
.venv/bin/python scripts/b2b_postgres_migrations.py target
.venv/bin/python scripts/b2b_postgres_migrations.py status
.venv/bin/python scripts/b2b_postgres_migrations.py plan
# Uniquement sur la base JETABLE identifiée, avec les deux valeurs vérifiées :
.venv/bin/python scripts/b2b_postgres_migrations.py apply \
  --database <base_jetable> --system-identifier <identifiant_verifie>
~~~

Aucune commande apply sur les démos n'a été exécutée.
Aucun appel du runner dans main.py, bff ou au démarrage d'un adapter.

## Reprise et rollback

Status/plan sont en lecture seule. Les opérations B2B vérifient l'historique et
la structure sans les créer. Après rollback applicatif, laisser les données et
registres en place, sans downgrade/drop automatique. Reprise d'un pending
d'ingestion : même clé/contenu ; ne pas supprimer le registre pour forcer un retry.

Tests : pgvector réel, plan/status/reapply, cible incorrecte, verrou concurrent,
checksum/version/type/index altérés, DDL interrompue transactionnellement puis
réapplication. Mongo0001/0002/0003 restent indépendantes et immuables.
