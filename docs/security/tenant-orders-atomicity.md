# ADR V13.7.3-B2 — Commandes B2B et réservations atomiques

Décision demandée le 21 octobre 2026. Base réelle `15f4fb80eb54e05d5c30abee94ce398b8c6ec36b`,
fusion de #47, distincte du HEAD de branche `49b1e65`.
Ce lot ne compose aucune route et ne rend pas StockPilot SaaS exploitable.

## Domaine, ownership et frontière

`MongoTenantOrders` implémente le port de cas d'usage `TenantOrdersPort` :
prepare, confirm, cancel, get. Commandes typées et snapshots immuables dans le
domaine ; aucune dépendance FastAPI/Keycloak. Aucun import du moteur ORDER historique.
Le modèle B1 quantity/reserved diffère du stock historique décrémenté : réutiliser
son adapter aurait changé ses invariants. Les principes transactionnels sont
conservés sans refactorisation des chemins historiques ni duplication de fallback.

`organization_id` possède la commande, `created_by` est l'humain créateur et
`customer_id` l'acheteur métier. **own = created_by**, politique révisable par
décision métier, jamais tous les humains partageant le même acheteur.
Un USER lit/annule ses commandes avec ses permissions ; MANAGER et ORGANIZATION_ADMIN
peuvent lire/annuler les commandes de leur organisation avec les permissions
organisationnelles. Seul le créateur autorisé peut confirmer sa préparation.
Les ressources étrangères et absentes produisent le même TenantOrderNotFound.

`MongoBuyerAssociations` implémente le contrat A avec un enregistrement courant
unique (organisation, utilisateur, customer). Statut active/disabled, identifiant
et revision explicites ; aucun email, header ou claim ne crée ce lien.
Le customer demandé à PREPARE est un candidat de sélection, jamais une preuve :
il doit correspondre à une association serveur active. Aucun provisioning au login.
Les managers doivent également retrouver l'association active du **créateur réel**
de la commande. Sa désactivation bloque lecture, confirmation, rejeu et annulation,
y compris manager : conserver les réservations et rétablir un droit approuvé via
une procédure future, jamais libérer sans autorisation. Un acheteur durable
indépendant des grants humains pourra être étudié séparément.

Le port de contexte et Principal V13.7.1/A sont réutilisés par resolve_context.
La politique générique A garde ORDER_CREATE et le rejeu réservés : ce lot ajoute
un chemin spécialisé, scoppé et transactionnel, sans élargir sa liste générique.
BFF, JWT historique et API globale restent indépendants et inchangés.

## Cycle et preuve de préparation

PREPARE → confirmation explicite → RESERVED → CANCELLED.
La préparation est un document persistant au statut prepared, pas une réservation.
Elle exige contexte actuel, ORDER_CREATE et association acheteur, résout chaque
SKU exact dans le catalogue de l'organisation et contrôle quantité/prix/disponibilité.
Une à vingt lignes, SKU uniques, quantité de type int strict entre 1 et 1 000 000 :
booléen, texte, fraction, zéro et négatif refusés. Aucun parsing LLM.

Lignes persistées : ObjectId natif du produit, SKU, quantité, prix entier en unités
mineures et devise. Identité produit exacte, pas de recherche par nom ni fallback.
Pas d'addition/conversion implicite entre devises. Les prix restent des snapshots.

Preuve SHA-256 canonique de l'organisation, commande aléatoire, acheteur, créateur,
lignes, opération de préparation, liaison à la session et dates. La liaison session
est hachée ; aucun cookie ou token n'est stocké. Expiration de préparation : 30 min,
sans TTL destructif. Le digest est une liaison d'intégrité, pas une signature ni une
capacité d'autorisation. Les records Mongo et le contexte serveur restent nécessaires.

CONFIRM exige un ConfirmTenantOrder typé, confirmed is True, order_id et digest
exacts, créateur et session d'origine, préparation valide non consommée.
Rotation de session avant première confirmation : préparer de nouveau, pas de
transfert implicite de preuve. Une prose LLM ne constitue aucune confirmation.
Prix/devise changés ou identité remplacée : conflit. Statut inactif/stock insuffisant :
conflit. Une simple modification de revision/stock est réévaluée contre les faits
actuels ; elle n'impose pas un conflit si le stock est toujours suffisant.

## Transaction et stock

`quantity` = stock physique total. `reserved` = quantité réservée.
Disponible = quantity - reserved ; inactive ne fournit aucun disponible.
Invariants Mongo B1 : 0 <= reserved <= quantity, revision positive.
La réservation ne soustrait pas quantity : elle incrémente reserved et revision.
La consommation physique et l'expédition ne sont pas implémentées ici.

Une transaction snapshot, primary, commit majority contient :
1. Contrôles/fences d'identité et membership.
2. Contrôle/écriture du grant acheteur.
3. Recherche du résultat idempotent ou vérification de la préparation.
4. Mise à jour de chaque produit par org + ObjectId + SKU + revision + statut actif
   + prix/devise attendus + disponibilité calculée dans le filtre Mongo.
5. Passage prepared → reserved et écriture du résultat idempotent.

Une insuffisance sur la dernière ligne annule aussi les premières modifications.
Aucune écriture globale après un préflight d'autorisation. L'organisation est
conservée dans toutes les recherches et mutations des ressources métier.

Annulation atomique par org + commande + revision + statut, permission own/org,
association actuelle et identités natives persistées. reserved diminue une seule
fois ; une préparation peut aussi être annulée sans stock. CANCELLED est terminal
pour ce lot. Un produit devenu inactif peut restituer sa réservation. Produit
absent/stock réservé incohérent : erreur technique sûre, aucun fallback ou correction.
Les futurs statuts livraison/consommation exigent une nouvelle politique.

## Révocation, concurrence et limites

À chaque appel/retry, resolve_context revalide la session, le Principal et ses droits.
Dans la transaction, les documents b2b_users, b2b_organizations, b2b_memberships
sont filtrés par identité exacte/statut/role puis modifiés par un compteur
authorization_fence. Le grant acheteur est également touché sur sa revision.
Une révocation concurrente sur ces mêmes documents produit un conflit d'écriture :
si elle gagne, retry puis refus ; si l'opération gagne, elle est sérialisée avant
la révocation. Les replays sensibles passent par les mêmes contrôles.

**Précondition de composition future** : identités, organisations, memberships,
associations et ressources sur la même base/replica set. Préprovisionner les
identités et leurs index uniques via l'initialiseur BFF explicite, jamais par le
callback ni par cette migration. Le TenantContextSource doit être un adapter serveur
de confiance, pas une désérialisation de Principal envoyé par le navigateur.

La session BFF est relue avant le corps et avant commit, mais sa révocation/logout,
expiration et le fournisseur OIDC ne font pas partie d'une transaction distribuée :
une révocation après la dernière vérification reste une fenêtre résiduelle.
Pas d'ouverture HTTP avant décision sur cette frontière et CSRF.
Le compteur organisation sérialise volontairement les commandes d'une même org :
compromis sûreté/débit, contention et retries bornés à mesurer avant pilote.
Aucune garantie de haute disponibilité ou de transaction distribuée Mongo/PG.

## Idempotence, retries et résultat inconnu

Journal durable, sans TTL : clé unique (organization_id, namespace, operation_id),
namespace prepare/confirm/cancel. Empreinte canonique inclut org, acteur et commande ;
lignes PREPARE triées par SKU. Même clé/contenu : résultat existant courant.
Contenu/acteur différent : TenantOrderConflict sans mutation. Même ID dans une
autre organisation ou un autre namespace : opérations indépendantes.

Le résultat journalisé référence la commande, pas un vieux statut : rejouer une
confirmation après annulation renvoie CANCELLED sans nouvelle réservation.
Une autre clé de confirmation sur une préparation déjà consommée est un conflit.
Autre clé d'annulation sur CANCELLED peut enregistrer son résultat, sans libération.

Quatre tentatives de transaction maximum, quatre tentatives commit par corps,
deadline PyMongo globale de 15 s par transaction, commit borné à 3 s.
TransientTransactionError/write conflict : nouvelle transaction et droits relus.
UnknownTransactionCommitResult : retry du **commit uniquement**, pas du corps.
DuplicateKey : transaction avortée et réessai borné de lookup/replay.
Après ambiguïté réseau/commit, une réconciliation majoritaire autorisée peut prouver
un résultat existant. Absence ou panne ne prouvent jamais un abort :
TenantOrderOutcomeUnknown, réessayer uniquement la même clé et le même contenu.
La réconciliation est elle aussi bornée et revalide identité, grant et ownership.
Les retries de lecture/autorisation touchent seulement leurs fences, pas le stock.

## Migration et index

[0002_b2b_orders](../../src/stockpilot/infrastructure/persistence/mongodb/migrations/0002_b2b_orders.json)
ajoute trois collections dédiées, aucun registre de réservation séparé :
- b2b_orders : org/order_id unique ; org/created_by/date et org/customer/date.
- b2b_buyers : org/user/customer unique ; org/association_id unique.
- b2b_order_operations : org/namespace/operation_id unique ; org/order_id.

Options strictes, types et bornes, collation simple, aucun TTL.
0001 reste identique. Le runner accepte un groupe déclaratif de collections en
0002, au sein de sa liste B2B fermée. Historique/checksum/verrou/états partiels et
absence de takeover restent conservés. Un échec après la première DDL de 0002
bloque les gates B1 et B2, sans suppression ni rollback automatique.
[Guide migrations](../mongodb-migrations.md) : apply reste limité aux bases jetables
explicitement identifiées. Aucune migration ni attribution historique effectuée.

## Preuves, rollback et suite

[Campagne exécutée](../validation/v13.7.3b2-tenant-orders.md) :
vraies transactions replica set, concurrence et injections de réponses autour de
commits réels. Les injections ne simulent pas une panne réseau physique, un arrêt
brutal de processus ou une bascule de replica set ; ces campagnes restent à prévoir.

Rollback applicatif : ne pas composer les ports B2B ; conserver les données, le
journal et les migrations. Aucune suppression/TTL/backfill automatique. Ne jamais
retirer le journal pour « débloquer » un retry. Sauvegarde/restauration appariée et
migration de données client exigent une autre mission approuvée.

B3 : mouvements et imports scoppés, inventaire physique compatible avec reserved,
audit d'administration puis RAG/conversations/checkpoints au niveau stockage.
Aucune route B2B métier, aucun déploiement, aucun mode SaaS ouvert par ce lot.
