# ADR V13.7.3-B1 — Catalogue et stock MongoDB par organisation

Décision du 21 octobre 2026, dans le périmètre demandé pour B1. Base vérifiée :
`b8fb6ee229ed64de5e00881d43c66ff79e4a1f24`, fusion de la PR #46 ; son HEAD
`cb778eb` est distinct. Ce chantier ne rend pas StockPilot SaaS opérationnel.

## Choix de stockage

| Critère | A : documents scoped dans les collections historiques | B : collections B2B dédiées, documents scoped (retenu) | C : base par organisation |
|---|---|---|---|
| Isolation | Filtre obligatoire, coexistence risquée avec chemins globaux | Filtre obligatoire + séparation des chemins historiques | Frontière physique supplémentaire ; credentials/routage à sécuriser |
| Complexité | Modèle mixte et attribution de chaque ancien document | Un adapter explicite et une collection supplémentaire | Pools, routage, provisionnement et migrations par base |
| MongoDB | Natif | Natif, même replica set possible | Natif ; orchestration plus importante |
| Recherche/index | Index existants globaux à transformer | Index composites versionnés sans toucher l'historique | Index reproduits par base |
| SKU | Uniques globaux historiques incompatibles avec duplication interorg | Unique `(organization_id, sku)`, collation simple | Unique par base, organisation néanmoins vérifiée |
| Atomicité future ORDER | Transactions possibles, modèle historique à modifier | Stock embarqué ; future transaction commandes/produits/mouvements | Transactions possibles, complexité du routage des ressources |
| Exploitation | Risque de mélange et filtres oubliés dans vieux chemins | ACL dédiées possibles ; pas d'activation implicite | Plus de cibles, monitoring et opérations |
| Migration | Attribution historique requise | Création seulement ; aucun backfill | Répartition et attribution de données nécessaires |
| Sauvegarde/restauration | Mélange des populations | Backup de la base dédiée ; restauration tenant sélective à concevoir | Granularité organisation plus naturelle |
| Coûts | Peu de collections ; forte surface de modification | Surcoût limité d'index/collection | Surcoût opérationnel/pools, sans forcément serveur par organisation |
| Scalabilité | Index composites et charge partagée | Même limite ; shard key tenant à étudier si besoin mesuré | Multiplication des bases ; automatisation indispensable |

La préférence B est cohérente avec le modèle réel : `products` contient `stock`,
sans quantité réservée séparée. `stock_movements` est un journal séparé ;
`order_transaction.py` et `stock_adjustment_transaction.py` possèdent les sessions,
retries et invariants historiques. Aucune séparation artificielle du stock,
aucun second algorithme de réservation, aucune collection/base par organisation.

## Modèle et ports

Collection **`b2b_products`** : `_id` ObjectId, `organization_id`, `sku`, `name`,
`description`, `category`, `price_minor` entier, `currency` explicite, `status`
active/inactive, `attributes` liste bornée de caractéristiques littérales,
`quantity`, `reserved`, `revision` positive. Prix 49 EUR = 4900 unités mineures.
Le caller doit connaître l'échelle de la devise ; aucune conversion FX implicite.
Les filtres de prix exigent une devise afin de ne pas comparer des devises différentes.
Identité exacte, sensible à la casse ; SKU globalement répétable entre organisations.

Mongo valide les champs requis, types, bornes et `0 <= reserved <= quantity`.
Le stock disponible est quantity - reserved, ou zéro lorsque le produit est inactif.
L'adapter revalide aussi les faits persistés et refuse données ambiguës/corrompues.
Les réponses catalogue et stock sont deux contrats distincts ; le catalogue
n'expose pas les quantités. Les lectures de stock exigent STOCK_READ.

`TenantCataloguePort` : `search`, `by_sku`, `by_id`.
`TenantStockPort` : `stock`, `available` (observation, sans réservation).
`MongoTenantCatalogue` implémente ces ports, avec contexte obligatoire et contexte
de requête serveur opaque à chaque appel. Il ne reçoit pas d'organisation HTTP.
`MongoCatalogueOwnership` fournit le port ownership existant pour catalogue/stock,
sur la même identité native, organisation et revision.

`TenantAuthorizationService.decide_scope` est la seule extension du contrat A :
préflight de lecture de collection pour CATALOGUE_READ/STOCK_READ, revalidation
du contexte complet et même matrice de permissions. Il ne donne aucun droit
de mutation ou d'accès à une ressource exacte. `decide` conserve son contrat.
Les lectures exactes passent aussi par `decide` et l'ownership réel.

Chaque lecture métier est bornée par `organization_id` **dans MongoDB** :
`find` exact avec limite deux, ou agrégation dont le premier `$match` porte
l'organisation, suivie du tri/pagination et d'un `$facet` pour un total cohérent.
L'identifiant natif étranger et l'absence produisent le même résultat absent.
Aucun filtre libre, opérateur utilisateur ou regex n'est accepté ; des scalaires
typés deviennent des égalités littérales. Aucun filtrage tenant Python après
lecture globale, aucun fallback historique, aucun contournement administrateur.

Refus d'identité : AuthorizationDenied / DENY. Ressource étrangère ou absente :
None / NOT_FOUND. Panne, schéma non prêt ou corruption :
TenantCatalogueUnavailable / ResourceOwnershipUnavailable / INDETERMINATE.
Les erreurs du driver ne sont jamais rendues avec credentials.

## Index retenus

La migration 0001 crée exclusivement sur `b2b_products` :

- `b2b_org_sku_unique` : `(organization_id, sku)` unique ; sert aussi le tri SKU.
- `b2b_org_status_sku` : `(organization_id, status, sku)`.
- `b2b_org_category_sku` : `(organization_id, category, sku)`.
- `b2b_org_currency_price` : `(organization_id, currency, price_minor, sku)`.

L'index `_id` existant sert l'identité native ; le filtre organisation reste présent.
Les caractéristiques sont filtrées après restriction du tenant, sans index multikey
spéculatif. Pas d'index de mouvements ou commandes avant leur implémentation.
Des index supplémentaires, partiels, TTL, hidden ou une collation différente
sont refusés par la vérification de structure ; une évolution exige une migration.
Les tests `explain` prouvent l'accès indexé pour l'égalité organisation/SKU.

## Compatibilité, limites et suite

Inventaire examiné : MongoProductRepository/ProductRepositoryPort, résolution
produit et références ORDER, catalogue/Advice, check/reserve/release stock,
création et métadonnées, ajustements et mouvements, dashboard, import CSV,
MongoOrderRepository et transactions ORDER. La composition `main.py` continue
à utiliser exclusivement ces chemins historiques. Ils ne sont pas modifiés.

Ni `main.py`, ni BFF, ni frontend, ni LangGraph ne composent les nouveaux ports.
Aucune route métier, aucun changement d'index historique, aucun import automatique
du catalogue, aucun `apply` au démarrage. Le BFF garde auth/session uniquement ;
`production_saas` reste fermé. Le gate de version et les vérifications d'index
s'exécutent avant les lectures B2B ; aucun cache de readiness n'est introduit.

La révocation est relue à chaque appel, mais la lecture identité + Mongo métier
n'est pas une transaction d'autorisation distribuée. Révocation concurrente après
le préflight et mutations entre lectures restent des limites documentées d'A.
L'organisation est néanmoins conservée jusque dans la requête finale.

B2 doit définir acheteur durable, mapping humain/customer, permissions de création,
idempotence et filtres atomiques `(org, _id, revision, status, disponibilité)` dans
la transaction de commande. quantity/reserved/revision préparent ce contrat,
sans le livrer. Mouvements/import/admin puis conversations/checkpoints/RAG ne sont
pas isolés par B1 et restent inaccessibles aux métiers BFF. Aucun historique
n'obtient une organisation par déduction.

Voir [migrations et récupération](../mongodb-migrations.md),
[frontière A](tenant-authorization-boundary.md) et
[preuves B1](../validation/v13.7.3b1-tenant-catalogue-stock.md).

Complément B2 : [commandes, acheteurs et transactions](tenant-orders-atomicity.md)
utilisent ce stock embarqué sans changer 0001 ou le modèle historique.
Les lectures B1 restent compatibles après 0002 appliquée. Aucun endpoint n'est ouvert.
