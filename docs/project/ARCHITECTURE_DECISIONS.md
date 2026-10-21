# Décisions d'architecture — StockPilot AI

Snapshot : **18 octobre 2026**. « En place » décrit le code et les contrats existants
à ce snapshot ; cela ne constitue pas une nouvelle approbation humaine ni une preuve
de déploiement. Les changements futurs demandent leur propre revue.
Les sources liées font autorité pour les détails et les garanties.

| Décision / statut | Motivation | Compromis et questions ouvertes | Source |
|---|---|---|---|
| FastAPI, couches domain/application/ports/adapters — **en place** | Garder les règles métier testables et séparer HTTP/persistance | Composition dans `main.py`, LangGraph reste dans l'application ; isolation framework partielle | [Architecture](../architecture.md) |
| LangGraph et checkpoints PostgreSQL persistants — **en place** | Reprendre contexte et confirmations après reprise API | Pas de fallback mémoire en production ; pas de sérialisation automatique même thread ni transaction distribuée Mongo/PG | [Architecture](../architecture.md), [checkpoints](../database.md) |
| MongoDB produits/stocks/commandes, transactions replica set — **en place** | Réservation atomique, identité exacte, idempotence et annulation cohérente | Adapter propriétaire des sessions/retries ; PyMongo synchrone, commit incertain à réconcilier, mono-nœud non redondant | [ORDER](../order-flow.md), [données](../database.md) |
| PostgreSQL/pgvector pour RAG — **en place** | Recherche documentaire par embeddings et sources | Corpus historique réduit ; pas de filtre tenant actuel ; changement d'embeddings implique un plan corpus/migration | [RAG](../rag.md) |
| Ollama fournisseur actuel — **en place** | Inférence locale et ports remplaçables | Coût/latence matériel et extraction probabiliste ; pas de changement des modèles/prompts ORDER sans demande explicite | [Architecture](../architecture.md), [évaluations](../ai-evals.md), [AGENTS](../../AGENTS.md) |
| Sélection catalogue déterministe — **en place** | Critères, budget, ordre et cardinalité explicables, aucun SKU inventé par le modèle | Grammaire bornée ; génération de présentation contrainte, clarification si identité ambiguë | [Advice](../catalogue-advice.md), [V13.4.2](../validation/v13.4.2-natural-search.md) |
| Vérifier les faits métier dans les stores — **en place** | Prix/stock/SKU/statut issus de données vérifiées, pas de prose libre faisant autorité | Faits instantanés, conseil sans réservation ; spécifications absentes et devise non renseignée restent inconnues | [Advice](../catalogue-advice.md), [V13.4](../validation/v13.4-conversation-ux.md), [ORDER](../order-flow.md) |
| Isolation client/thread et ownership — **en place** | JWT signé, identité injectée côté serveur, clé de checkpoint dérivée du couple client/thread | Ne réutilise pas les anciennes clés brutes ; ne constitue pas une isolation d'organisation ; back-office local privilégié | [Architecture](../architecture.md), [auth SaaS](../security/saas-auth-architecture.md) |
| Confirmation ORDER explicite avec preuve persistée/désarmement — **en place** | Pas de réservation pendant conseil/préparation ; mutation seulement après décision valide | Conserver quantités lexicales, SKU/order_id exacts, ownership, operation_id idempotent et atomicité ; coût de clarification et reprise | [ORDER](../order-flow.md), [AGENTS](../../AGENTS.md) |
| `customer_id != tenant_id` — **distinction de conception documentée** | Client acheteur et organisation sont des périmètres différents | `customer_id` actuel ne prouve pas une membership ; modèle Organization/User/Membership/Role proposé, pas implémenté | [Proposition V14](../security/v14-saas-design.md) |
| SaaS fail-closed jusqu'à isolation complète — **en place** | Refuser l'accès sensible plutôt qu'exposer des stores globaux | Modes `production_saas`/`production` inutilisables pour servir un SaaS actuel ; activation exige preuves et validation humaine distinctes | [Frontière actuelle](../security/saas-auth-architecture.md), [V14](../security/v14-saas-design.md) |
| Protéger stable v017 et candidate V13.3 — **règle active** | Préserver données, disponibilité et traçabilité | Worktree par tâche, services de tests jetables vérifiés ; pas d'accès aux installations sans autorisation, pas de merge/release implicite | [AGENTS](../../AGENTS.md), [tests](../testing.md) |

## Propositions non approuvées comme choix d'implémentation

- **V14** : pilote isolé puis bases dédiées par organisation sur serveurs partagés,
  contexte serveur immuable, OIDC, rôles, quotas et migrations sont proposés.
  Hébergement, identité, coûts, permissions et calendrier restent à arbitrer ;
  la publication de [#36](https://github.com/WaelOuni/stockpilot-ai/pull/36) n'active rien.
- **Sessions HMAC** : clé indépendante versionnée proposée pour durcir l'empreinte
  de configuration ; provisionnement durable, invalidation/réauthentification,
  rotation et rollback à valider avant implémentation. Voir [analyse résiduelle](../security/admin-session-fingerprint-review.md).

Pour une nouvelle décision approuvée, ajouter seulement une entrée : date, périmètre,
motivation, compromis, questions et lien vers l'accord humain/PR. Ne pas convertir
une hypothèse commerciale ou une proposition V14 en décision par simple recopie.

## 20 octobre 2026 — frontière d'autorisation V13.7.3-A

Choix demandé : réutiliser Principal et la résolution V13.7.1 via les sessions BFF,
avec un contexte serveur immuable revalidé, ownership scoped et mapping acheteur
explicite. [ADR et limites](../security/tenant-authorization-boundary.md).
Les permissions métier et l'organisation ne se déduisent pas de Keycloak ou du
navigateur. Aucun store métier tenant ou migration n'est activé ; l'atomicité
future, l'attribution historique et la politique d'acheteurs restent des gates B.

## 21 octobre 2026 — stockage B1 et migrations Mongo explicites

Demande V13.7.3-B1 : collection B2B dédiée `b2b_products`, organisation obligatoire,
stock embarqué, unicité SKU par organisation ; [comparaison A/B/C et compromis](../security/tenant-catalogue-stock.md).
Isolation dans les requêtes et revalidation des contrats A, sans activation de
route. [Runner Mongo léger](../mongodb-migrations.md) versionné, checksums, suivi
et exclusion mutuelle sans takeover ; apply autorisé seulement sur cibles jetables
dans ce lot. Aucune attribution historique, migration existante ou modification PG.
L'extension production, les commandes/acheteurs et l'ouverture du BFF restent
des décisions et preuves distinctes, non acquises par ce choix.

## 21 octobre 2026 — commandes B2B et atomicité B2

Demande B2 : organisation propriétaire, own = created_by, acheteur explicitement
associé ; [ADR](../security/tenant-orders-atomicity.md). Préparation persistée
sans réservation puis transaction stock/commande/journal, namespace idempotent
par org et empreinte canonique. quantity physique, reserved séparé ; annulation
au plus une fois. Migration 0002 dédiée sans backfill, 0001 immuable.
Fences Mongo identité/grant pour la révocation concurrente ; session BFF relue
hors transaction distribuée. Aucun endpoint métier ni activation de SaaS.

## 21 octobre 2026 — opérations physiques et imports B3

[ADR](../security/tenant-stock-operations.md) : ajustements quantity>=reserved,
sans toucher reserved, mouvements et résultat idempotent atomiques ; modes CSV
CREATE_ONLY/METADATA_ONLY/STOCK_ONLY et rejet total d'un lot borné.
Déclaration pending durable avant admission d'une mutation puis terminal réel ;
ne jamais convertir une indisponibilité en succès/abort implicite. Helpers B2
et CSV partagés, migrations0001/0002 immuables et0003 additive explicite.
Pas de nouveaux mouvements ORDER : journal physique B3 non exhaustif des réservations.
Aucune composition HTTP ; révocation BFF et ouverture Internet restent distinctes.

## 21 octobre 2026 — RAG/conversations B4 et cible de démo

[ADR B4](../security/tenant-rag-conversations.md) : tables PG dédiées, recherche
exacte filtrée avant calcul topK/LLM, publication atomique de génération,
ownership privé du créateur et saver racine conforme à l'API verrouillée.
[Migrations SQL explicites](../postgresql-migrations.md) transactionnelles,
sans ORM ajouté, checksums/structure/verrou et cibles jetables vérifiées.
Aucun changement des repositories ou migrations historiques/Mongo.
Fenêtre de révocation inter-stores explicitement conservée, pas de promesse
d'atomicité Mongo/PG/LLM ni d'ouverture publique.

Une seule [démo B2B future canonique](../b2b-demo.md), non opérationnelle.
Anciens guides/runbooks conservés pour reprise et preuves ; aucun retrait
d'installation ni déploiement. Les propositions historiques ne remettent pas en
cause le choix utilisateur validé Keycloak + BFF ; activation et hébergement
restent des décisions et preuves séparées.


## V13.8.4 — recette canonique et restauration appariée

Décision du chantier, 21 octobre 2026 : une seule composition et un seul
opérateur de validation, sans installateur durable ni activation Internet.
Les volumes de recette sont propres au projet et conservés pendant les
redémarrages, puis retirés après attestation d'identité ; aucun volume historique.

Réutiliser les migrations Mongo0001–0003 et PG0001–0002 immuables. Pas de nouveau
schéma ni de DDL au startup. Les sauvegardes sont appariées **à écritures
applicatives arrêtées**, avec hashes logiques avant/après et restauration dans
des bases indépendantes vides. Cela ne constitue pas un snapshot distribué
atomique ou une politique de sauvegarde client.

Conserver issuer/subject et résultat Mongo autoritaire après restauration ;
tourner la clé BFF, reconnecter et réconcilier le journal PG à clé constante.
Ne pas réparer une incertitude en créant une autre commande.
La cible durable exigera une autorisation, des secrets/rôles DB dédiés,
chiffrement/rétention hors machine et validation de son exposition propre.
[Guide canonique](../b2b-demo.md), [preuves](../validation/v13.8.4-canonical-b2b-demo.md).
