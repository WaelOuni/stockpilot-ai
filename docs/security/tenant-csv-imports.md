# CSV B2B — formats internes et politique d'application

Voir [l'ADR stock/imports](tenant-stock-operations.md) et les
[tests exécutés](../validation/v13.7.3b3-stock-imports.md).
Aucune nouvelle route d'upload/apply n'est exposée ; ce guide décrit un port interne.
Les imports historiques et leur fichier sku,name,category,price restent inchangés.

## Fichiers acceptés

UTF-8/BOM facultatif ; virgule ou point-virgule ; quoting CSV strict.
Header exact sans colonne d'organisation, rôle, permission ou commande.
Les noms de colonnes peuvent être ordonnés différemment, pas dupliqués.
Les valeurs sont traitées comme textes/nombres littéraux, jamais comme opérateurs
MongoDB, formules exécutables ou commandes. Un futur export tableur devra traiter
séparément l'échappement des formules ; aucun export ajouté ici.

CREATE_ONLY, stock initial explicite et réservé nul :
```csv
sku,name,description,category,price_minor,currency,quantity
DRILL-NEW,Perceuse,Exemple synthétique,drill,4900,EUR,10
```

METADATA_ONLY, ne touche aucun stock :
```csv
sku,name,description,category,price_minor,currency
DRILL-001,Perceuse renommée,Exemple synthétique,drill,5100,EUR
```

STOCK_ONLY, ne touche pas les métadonnées ni reserved :
```csv
sku,quantity
DRILL-001,8
SAW-001,2
```

Les catégories sont les libellés littéraux B1 bornés, pas une conversion implicite
de l'enum historique. Prix en unités mineures entières 0..2^63-1, devise trois
lettres ASCII majuscules ; aucune conversion FX/TVA ou interprétation flottante.
Quantité physique entière 0..1 000 000 000. Pas de signe, fraction, booléen,
notation exponentielle ou espaces de bord. SKU exact et unique dans le fichier.
Nom/catégorie non vides et sans espaces de bord ; description peut être vide.

Limites : 256 Kio, 100 lignes, champs au plus 8 192 caractères, diagnostics bornés
à 50 sans copie des valeurs invalides. Name <=512, catégorie/SKU <=128.
Les gros lots sont refusés, pas répartis silencieusement en transactions partielles.

## Preview et application

Le preview conserve un plan normalisé/snapshot dans sa seule organisation, une
empreinte de fichier et des diagnostics génériques. Il ne modifie pas les produits.
Le digest est lié à l'acteur, à la session serveur, aux règles et à une durée de
30 min ; il n'accorde aucune permission. Aucun CSV brut conservé dans le registre.

Seul ORGANIZATION_ADMIN avec import:execute peut prévisualiser/appliquer/lire les
imports. L'application exige son identité actuelle, la liaison de session, un
operation_id, import_id, preview_digest et confirmed strictement True.
Un administrateur d'une autre organisation n'accède à rien de ce dossier.

Le lot est entièrement atomique. Produit manquant/existant à tort, révision
changée ou physique inférieur au réservé : aucun produit ni mouvement appliqué.
Un rejeu terminal même clé/contenu retourne le résultat, même après expiration.
Autre fichier/preview sous même clé : conflit. Nouvelle clé sur preview consommé :
rejet. Un état pending appelle une réconciliation avec la commande originale,
jamais un nouvel identifiant supposant que le premier essai n'a rien fait.

Un changement de stock, réservation ou annulation après preview nécessite un nouvel
aperçu, même si les nouvelles valeurs pourraient encore être acceptables.
Le filtre de mutation impose aussi quantity >= reserved au moment du commit.
Les erreurs techniques ne sont jamais converties en refus métier ou succès.
Avant admission durable, une panne Mongo peut empêcher de créer le dossier/résultat ;
elle est signalée comme indisponibilité.

## Historique et audit

Les imports INVALID restent consultables avec diagnostics ; après apply rejeté
leur état REJECTED garde ces diagnostics. SUCCEEDED/REJECTED sont des résultats
terminaux ; PENDING n'est pas terminal. Les mouvements physiques et les résultats
sont immuables par l'API interne, sans TTL, avec acteur/organisation/opération.
Une politique de rétention future ne doit pas autoriser de double application.

Aucun mouvement de réservation ORDER ajouté ; METADATA_ONLY est tracé par son
dossier avant/cible et sa réception, pas par un faux mouvement de stock.
Sauvegarde/restauration, quotas publics et parcours navigateur BFF restent à valider.
