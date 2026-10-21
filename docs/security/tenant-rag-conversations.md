# ADR V13.7.3-B4 — RAG et conversations B2B

Décision demandée le 21 octobre 2026. Base `146e74a637bdc6c5ae200dcd3f047534d13cd28e`,
fusion réelle de #49, distincte du HEAD de branche `9dc2ace`.
Aucune route métier, migration historique, installation ou promotion de démo.

## Frontières et composants

TenantContext/Principal, IdentityAuthorizationService et TenantAuthorizationService
restent les autorités serveur. Les ports internes et erreurs sont dans
application/ports/tenant_knowledge.py. PgTenantKnowledge et PgTenantConversations
utilisent un schéma PostgreSQL B2B dédié ; aucun accès aux knowledge_chunks ou
checkpoints historiques. TenantRetrieval réutilise EmbeddingPort et
LlmPort.generate(prompt=...), avec un prompt documentaire B4 propre.
TenantRagConversation compose un graphe RAG en lecture seule ; il ne compose pas
le workflow historique ni les outils ORDER, stocks ou imports.

| Permission nouvelle | Attribution minimale |
|---|---|
| rag:read | Tous les rôles existants |
| rag:manage | ORGANIZATION_ADMIN uniquement |
| conversation:own | Tous les rôles ; créateur seulement |
| Partage de conversation | Non implémenté, aucun droit implicite de manager/admin |

Le client ne fournit aucune identité faisant autorité. customer_id n'est pas
utilisé pour déduire l'utilisateur ou son organisation. Les opérations génériques
réservées de la politique A restent fermées ; ces adaptateurs spécialisés
revalident le contexte courant et leurs permissions à chaque opération.

## Documents, embeddings et publication

Documents : clé (organization_id, document_id), titre, revision, statut,
génération active, modèle/version d'embedding, dates. Métadonnées volontairement
typées et minimales, aucun objet JSON libre interprété comme filtre ou permission.
Ingestion : clé (organization_id, operation_id), acteur, empreinte canonique,
génération et état pending/ready/failed. Chunks : organisation/document/génération/
numéro, contenu et vector(768), avec FK composite.

Commande immuable : identifiants bornés, titre200 caractères, texte64000 caractères,
revision attendue explicite. Découpage déterministe par2000 caractères, au plus32
chunks. Modèle/version sont configurés côté serveur et doivent correspondre à la
commande. Vecteurs768 finis, non nuls et bornés pour le stockage float32.
Pas d'ingestion des fichiers historiques et pas de création au login OIDC.

Une transaction déclare l'ingestion pending ; les embeddings sont calculés hors
transaction. Une seconde transaction publie tous les chunks et le pointeur actif.
Aucun chunk partiel n'est visible. Un remplacement en échec conserve l'ancienne
génération ready. Une panne technique de publication conserve pending ou un
résultat déjà commité : réessayer la même clé/contenu, jamais déduire l'abort d'un
timeout. Une génération failed peut être reprise avec la même commande tant que
la revision attendue reste valide. Une mise à jour concurrente gagne seule ;
l'autre échoue sans écraser le document.

La suppression autorisée est idempotente par org/opération/empreinte et revision.
Elle retire les chunks de ce document seulement, désactive son pointeur et garde
un tombstone ainsi que les registres d'opération. Rejouer une ancienne ingestion
ready ne ressuscite pas un document supprimé. Rétention et purge ne sont pas
implémentées et nécessiteront une politique approuvée.

## Recherche avant LLM

Le CTE SQL MATERIALIZED restreint organisation, génération active, état ready et
modèle/version **avant** le tri par distance cosinus et LIMIT. Lecture exacte par
document_id et titre également scoppée dans SQL. Aucun fallback global.
Les passages portent leur organisation ; le service la contrôle encore en défense
et revalide les droits juste avant de construire le prompt.

Pas de HNSW/IVFFlat global : recherche exacte pour le premier pilote, évitant le
rappel insuffisant d'un ANN global filtré. La PK composite commence par org.
Coût linéaire dans les chunks sélectionnés du tenant ; topK borné1..20.
Mesurer le corpus réel avant ANN/partitionnement et maintenir les tests de
non-fuite et rappel avec tenants dominants. Pas de promesse de capacité ou SLA.

Documents/question sont des données non fiables, pas des instructions ni des
permissions. La résistance universelle au prompt injection n'est pas revendiquée :
l'isolation est assurée avant le modèle, qui ne reçoit aucun passage étranger.
Le modèle peut toujours produire une erreur factuelle ; aucun résultat de prose
ne peut confirmer une commande ou modifier un stock.

## Conversations et checkpoints

Comparaison : simple namespace/hash seul insuffisant ; wrapper d'un saver global
risqué pour list/config=None/writes/delete ; modifier les tables internes
AsyncPostgresSaver risquerait l'historique. Choix : BaseCheckpointSaver compatible
avec la version verrouillée, tables B2B dédiées et ownership SQL à chaque méthode.

Conversation : clé(org,user,conversation_id aléatoire), unicité du thread externe
par(org,user), statut actif/deleted. Même thread externe dans deux organisations
ou pour deux utilisateurs = conversations distinctes. Une bind étrangère produit
la même erreur qu'une absence. Aucun partage ni fallback customer/thread global.

Saver lié à une requête/contexte/conversation vérifiés. Toute lecture/écriture
porte org+user+conversation et vérifie l'enregistrement propriétaire actif.
Checkpoint complet sérialisé, métadonnées, parent, écritures intermédiaires, clés
composites et FK vers la conversation. Pas de tables/blob stores historiques.
Snapshots complets : coût supérieur à la déduplication du saver natif, choisi
pour la simplicité et la frontière explicite du pilote.

L'API LangGraph peut écrire les pending_writes avant son checkpoint principal :
elles sont liées à la conversation mais invisibles à get_tuple tant que le
checkpoint exact n'existe pas. Suppression transactionnelle writes+checkpoints+
tombstone empêche une écriture tardive de ressusciter la conversation.
Le service de graphe sérialise l'exécution d'une conversation par advisory lock ;
une exécution/suppression concurrente est refusée explicitement. Les méthodes du
saver verrouillent aussi la ligne propriétaire pour leurs opérations atomiques.

Support sync/async, historique paginé par before (limite100), pending writes,
interrupt/reprise et nouvelle connexion. Graphe racine seulement : namespace
non vide, list sans config et filtres arbitraires de métadonnées refusés.
Snapshots et valeurs bornés à1Mio, métadonnées à64Kio au niveau applicatif.
JsonPlus sans pickle, allowlist MsgPack limitée au type Interrupt de LangGraph.
Pas d'objets arbitraires importés depuis le contenu documentaire.
Toute évolution de LangGraph exige de rejouer ces contrats réels.

## Erreurs et révocation

AuthorizationDenied : identité/contexte/droits non valides.
TenantDataNotFound : ressource absente ou étrangère.
TenantDataConflict : revision, clé idempotente ou concurrence incompatibles.
TenantDataUnavailable : identité, schéma, embedding, génération ou stockage requis
indisponible ; jamais présenté comme un corpus vide prouvé.
TenantDataOutcomeUnknown : une écriture a pu être commitée ; réconciliation avec
la même commande. Les erreurs publiques internes sont assainies sans URI/tokens.

Le contexte est relu avant opération et avant commit, après calcul d'embeddings
et immédiatement avant envoi au LLM. Les tests révoquent réellement les
memberships Mongo autour de ces frontières. **MongoDB, PostgreSQL, session BFF
et appel LLM ne forment pas une transaction distribuée** : révocation après la
dernière relecture reste une fenêtre résiduelle, pas une révocation atomique
inter-stores. L'ouverture HTTP doit arbitrer et valider cette limite, quotas,
CSRF et cycle de session avant activation. Aucun service public n'est ouvert ici.

## Migration, rollback et suite

[Runner PostgreSQL explicite](../postgresql-migrations.md), SQL0001 dédié,
historique/checksums, empreinte de structure incluant typmods/index, advisory lock
et DDL transactionnelle. Aucun setup au démarrage ni attribution d'historique.
Mongo0001/0002/0003 et migrations RAG/checkpoints historiques inchangées.

Rollback applicatif : ne pas composer les nouveaux ports ; conserver tables,
registres et preuves. Pas de downgrade destructif ni purge automatique.
Sauvegarde/restauration cohérente Mongo/PG et rétention des conversations/
documents nécessitent une campagne séparée avant données client.

Suite : intégration HTTP sécurisée des ports B1–B4, UX Vue V13.7.4,
validation E2E de la [future démo canonique](../b2b-demo.md). L'isolation interne
ne rend pas StockPilot SaaS prêt pour Internet.
