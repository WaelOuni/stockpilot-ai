# ADR V13.7.3-B3 — Stock physique, mouvements et imports par organisation

Décision demandée le 21 octobre 2026. Base réelle `d6a0bcdb7709d2a8e54d6104ce77043b2493d325`,
fusion de #48, distincte du HEAD `7c7a1d2`. Aucun endpoint métier, aucun déploiement.

## Composants et compatibilité

`MongoTenantStockOperations` implémente le port interne `TenantStockOperationsPort`.
Contrats immuables AdjustTenantStock, ApplyTenantImport, StockOperationResult,
TenantImportPreview et TenantStockMovement ; aucune dépendance HTTP/Keycloak du domaine.
La consultation du stock reste celle de B1 (`MongoTenantCatalogue`), pas un second
chemin global. Aucun port nouveau n'est composé dans main.py ou le BFF.

Le coordinateur B2 est extrait en `MongoTenantUnitOfWork` : contexte courant,
fences user/org/membership, transaction snapshot/majority, retries bornés et
réconciliation. MongoTenantOrders conserve schema_version=2 ; B3 exige 3.
Comparaison AST locale : toutes les méthodes ORDER gardent leur corps, à la
seule différence du paramètre de gate de version dans le coordinateur partagé.
La suite B2 réelle est rejouée ; aucun changement d'algorithme ORDER.

Le validateur de quoting CSV historique est extrait dans un helper pur partagé ;
ses use cases gardent leur AST métier. Le moteur historique reste CREATE_ONLY,
stock initial nul, prix float et catégories historiques. B3 a un schéma distinct
adapté au prix entier/devise et au stock réservé B1 : aucun copier/coller de son
repository global, aucun changement de ses routes ou de ses règles.

## Stock physique et ajustements

quantity = physique ; reserved = engagements ORDER ; disponible = quantity - reserved.
Invariants 0 <= reserved <= quantity, revision positive. Un ajustement ne modifie
**jamais reserved**. AdjustTenantStock exige product_id exact, quantité entière
physique entre 0 et 1 000 000 000, revision attendue et motif imprimable non vide
(maximum 500 caractères). Les booléens, fractions et textes sont refusés.

Transaction : produit par org/ObjectId/SKU/revision + filtre Mongo
`$expr: reserved <= nouvelle_quantity`, puis changement quantity/+revision,
mouvement et résultat idempotent ensemble. Une baisse sous reserved est un rejet
métier durable, sans mouvement. Produit absent et étranger : même code générique.
Le statut inactif n'empêche pas un inventaire physique ; il n'active pas le produit.
Une quantité identique peut être validée comme constat d'inventaire, avec mouvement
delta=0 et nouvelle revision. La consommation/expédition reste hors périmètre.

## Mouvements et audit

Deux types effectivement émis :
- ADJUSTMENT : ajustement physique explicite.
- IMPORT_ADJUSTMENT : création avec stock initial ou import STOCK_ONLY.

Organisation, produit natif/SKU, namespace/opération, acteur humain, quantités
physiques et réservées avant/après, revision après, date, motif et référence source.
Le delta est dérivé du physique avant/après. Les valeurs réservées restent identiques.
Index unique (org, namespace, operation, product_id) : pas de double mouvement.
Les mouvements sont insérés dans la transaction de stock, jamais après le commit.

Les réservations/libérations ORDER **ne génèrent pas de nouveaux mouvements B3** :
aucune écriture d'audit supplémentaire ajoutée au moteur B2. Un import METADATA_ONLY
ne produit pas de mouvement physique ; son aperçu persistant contient snapshot
avant et valeurs ciblées, son état/résultat identifie l'application.
Ce journal n'est donc **pas un audit exhaustif** des réservations, ventes ou prix.
Un futur audit global devra rapprocher ces sources sans fabriquer l'historique.

## Permissions côté serveur

| Opération | Permission | Rôles de la matrice existante |
|---|---|---|
| Lire le stock B1 et les mouvements B3 | stock:read | READ_ONLY, USER, MANAGER, ORGANIZATION_ADMIN |
| Ajuster le physique | stock:adjust | MANAGER, ORGANIZATION_ADMIN |
| Preview/apply/historique des imports | import:execute | ORGANIZATION_ADMIN |
| Consulter un résultat d'opération | permission de son namespace + acteur identique | Ajusteur/importeur concerné |
| Gérer les membres | organization:manage_members | ORGANIZATION_ADMIN ; aucune nouvelle gestion des membres implémentée |

L'organisation vient du TenantContext revalidé, jamais du CSV, de headers ou des
claims de rôle Keycloak. Aucun PLATFORM_ADMIN ou accès global implicite.
Les administrateurs d'organisation peuvent lire l'historique d'import de leur org,
mais seul le créateur de l'aperçu, lié à sa session, peut l'appliquer.
L'administration de stock ne requiert pas de customer/grant acheteur : elle repose
sur les permissions humaines, contrairement à la commande B2. Aucun mapping inféré.

## Import et limites

Voir le [format et les exemples](tenant-csv-imports.md).
UTF-8 strict avec BOM facultatif, séparateur virgule ou point-virgule déterminé
par un header exact, quoting partagé, champs littéraux uniquement.
256 Kio, 100 lignes, champ 8 192 caractères, 50 diagnostics maximum.
Un lot plus gros est refusé ; découpage/chunking futur non activé.

Trois modes explicitement distincts :
- CREATE_ONLY : SKU absent dans l'organisation ; produit actif, attributes=[],
  reserved=0, quantity initiale explicite, prix/devise entiers. Un SKU homonyme
  dans une autre org n'est ni consulté ni copié.
- METADATA_ONLY : produit existant ; nom/description/catégorie/prix/devise seulement,
  aucune modification de quantity, reserved, SKU, attributes ou statut.
- STOCK_ONLY : produit existant ; quantité physique seulement, reserved préservé.

Workflow : parse/validate → preview persistant → apply explicite → résultat.
Aucun changement produit/stock/mouvement au preview ; seul son dossier et les
compteurs internes d'autorisation sont écrits. CSV brut non conservé.
Erreurs de type/taille avant admission : refus sans dossier. CSV borné mal formé :
preview INVALID persistant avec diagnostics assainis, sans plan partiel.

Le preview contient org, acteur, liaison de session hachée, import_id aléatoire,
SHA-256 du fichier, règles b2b-csv-v1, plan normalisé, snapshot avant, digest canonique
et expiration 30 minutes. Le digest n'est pas une capacité d'autorisation.
Le retour public interne ne contient pas les snapshots avant ni le cookie.
Une rotation de session impose un nouvel aperçu pour une première application.

APPLY exige confirmed is True et le digest/id exacts. Les droits sont relus.
La revision de chaque produit doit encore être exactement celle du preview :
réservation, annulation, inventaire ou métadonnée concurrente impose un nouvel
aperçu. La contrainte reserved <= physique cible reste dans le filtre Mongo.
Une première passe vérifie **tout** le lot avant les écritures. Puis produits,
mouvements, import terminal et résultat idempotent sont commités ensemble.
Une erreur sur une ligne entraîne rejet total ou abort total, jamais application
partielle annoncée comme atomique.

Un conflit métier enregistre REJECTED sans changement produit/mouvement. Un CSV
INVALIDE appliqué devient aussi REJECTED en conservant ses diagnostics lisibles.
Un preview étranger/absent reste indistinguable ; mauvaise liaison/digest, expiration
ou consommation produisent un résultat rejeté sans invalider le preview d'autrui.
Un second identifiant d'opération ne peut pas consommer un import déjà appliqué.

## Opérations durables, erreurs et réconciliation

Collection b2b_stock_operations séparée du journal ORDER : le schéma immuable de
0002 impose order_id et namespaces prepare/confirm/cancel ; il ne représente ni
les imports ni leur état pending. Pas de fusion artificielle des espaces de clés.

Clé unique (org, namespace adjust/import, operation_id), acteur et empreinte canonique.
Même clé/contenu retourne le même résultat ; autre contenu/acteur : conflit sans
mutation. Aucun TTL de preuve d'idempotence, de mouvement ou de preview.

1. Une transaction autorisée déclare PENDING avant d'accepter la mutation.
2. La transaction métier enregistre SUCCEEDED ou REJECTED avec les effets réels.
3. Un incident technique après déclaration conserve PENDING, pas un faux rejet.
4. Une ambiguïté de commit utilise les retries/réconciliation B2 : un résultat
   durable retrouvé fait autorité ; PENDING n'est jamais un succès ni preuve d'abort.

StockOperationResult refuse la conversion booléenne : comparer explicitement status.
Le client doit conserver clé et commande originales ; ne pas changer operation_id
après timeout. Une reprise avec nouvelle connexion peut continuer un PENDING,
ou retrouver le résultat terminal sans rejouer les écritures.
Le replay terminal autorisé ne dépend plus d'un preview encore valide.
L'état PENDING peut aussi subsister après une révocation : il ne donne aucun droit
de reprise à un utilisateur désormais interdit.

Les exceptions techniques/issue inconnue B2 sont réutilisées (notamment
TenantOrdersUnavailable et TenantOrderOutcomeUnknown, noms conservés pour
compatibilité interne). Aucune route HTTP ne les expose ici.
Si Mongo est indisponible avant la déclaration, aucune admission durable ne peut
être prouvée : erreur technique, jamais succès/rejet fabriqué.
Ne pas effacer un PENDING pour « débloquer » un retry. Réparation, rétention et
exploitation restent des procédures humaines futures avec sauvegarde.

## Concurrence et révocation

Les fences B2 touchent dans la transaction les mêmes documents que la révocation :
user, org, membership. Si la révocation gagne, write conflict/retry puis refus ;
si l'opération gagne, elle est sérialisée avant la révocation.
La session est relue avant le corps et le commit ; logout/expiration OIDC/BFF
reste hors transaction distribuée, fenêtre résiduelle déjà documentée en B2.
Identités, memberships, produits et registres doivent partager la même DB/replica set,
avec index d'identité BFF explicitement provisionnés. Aucun bootstrap automatique.

Quatre tentatives de corps et de commit, deadline de 15 s par transaction et
commit borné à 3 s, puis réconciliation séparément bornée. La déclaration ajoute
une transaction et la fence org crée une contention volontaire. Mesures locales
faible concurrence dans le [rapport](../validation/v13.7.3b3-stock-imports.md) :
échantillon petit, pas un SLA ou benchmark de capacité. Les tests injectent des
erreurs PyMongo autour de commits réels, pas une panne réseau/processus physique.

## Migration, rollback et suite

0003_b2b_stock_operations : b2b_stock_movements, b2b_imports, b2b_stock_operations,
validateurs racines stricts, bornes, index organisationnels et unicité.
Les plans sont en outre validés et liés par digest lors de chaque décodage.
0001 et 0002 gardent exactement leurs octets/checksums.
Un échec DDL partiel marque failed et bloque tous les gates ; ni takeover ni
rollback destructif. Apply seulement sur bases jetables explicitement identifiées.
Aucune collection/index historique modifié, aucune donnée réattribuée.

Rollback applicatif : ne pas composer les ports B3 ; garder les registres et
migrations. Aucun down de données, recalcul reserved ou journal compensatoire.
Sauvegarde/restauration et rétention métier nécessitent une autre campagne.

B4 : isolation des retrievals RAG avant LLM, conversations et toutes les tables
de checkpoints. L'intégration HTTP exigera auth/session/CSRF, tests navigateur,
quotas et exploitation ; ces preuves de stockage ne rendent pas le SaaS public prêt.
