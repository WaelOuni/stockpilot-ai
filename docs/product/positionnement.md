# Positionnement et présentation commerciale

Hypothèses de travail au 17 octobre 2026. La cible et la volonté de payer restent
à vérifier par [des entretiens et un pilote](validation-commerciale.md).

## Proposition de valeur à tester

« Retrouver les références disponibles dans votre catalogue, comparer leurs prix,
puis préparer une commande en français avec une confirmation explicite. »

Le problème envisagé : un commercial ou un responsable de comptoir cherche une
référence et ses alternatives dans un fichier, vérifie le stock ailleurs, puis
ressaisit la demande. StockPilot réunit ces étapes pour un catalogue limité.
Le bénéfice attendu est un temps de traitement réduit et une compréhension plus
claire de la commande ; aucun gain n'est encore mesuré chez un client.

Le premier segment à explorer est celui des petits grossistes, distributeurs et
PME d'outillage à Lille et dans les Hauts-de-France : interlocuteur accessible,
catalogue à SKU stables, demandes répétitives et un responsable de données identifié.
Cela constitue un choix d'expérimentation, pas une estimation de marché.
Une seconde exploration en Tunisie dépendra de retours locaux sur langue, devise,
hébergement, budget et support ; aucun besoin équivalent n'est présumé.

## Disponible et prévu

La table décrit le parcours historique Vue/JWT. Les composants internes B2B
Keycloak/BFF et stores isolés ont depuis été intégrés ; leur assemblage métier
HTTP/Vue reste à réaliser. Voir la [cible canonique](../b2b-demo.md).

| Besoin | État observable dans le code actuel | Limite à annoncer |
|---|---|---|
| Catalogue et disponibilité | Recherche, catégorie, prix, stock et fiche par SKU | Instantané ; conseil ne réserve rien |
| Conseil catalogue | Critères déterministes, produits relus en MongoDB | Domaine outillage et grammaire bornée ; pas toute requête métier |
| Produit nommé | Nom exact, SKU et recherche partielle bornée ; clarification si ambigu | Pas de recherche approximative générale |
| Comparaison | Référence + alternatives, prix et écarts, disponibilité | Pas de performances techniques sans attributs vérifiés |
| Suivi | Budget remplacé, catégorie/disponibilité conservées ; contexte client/thread | Pas de mémoire entre threads indépendants |
| Commande | Préparation, confirmation explicite, réservation atomique, statut et annulation autorisée | Ni paiement ni facture ; préparation ≠ réservation |
| Administration | Catalogue, ajustements tracés, commandes, tableau de bord | Configuration et index prérequis ; rôles d'organisation non disponibles |
| Import CSV | Aperçu puis création atomique de nouveaux SKU, stock initial zéro | 500 produits et 2 MiB par défaut ; pas de mise à jour de masse des SKU existants |
| Connexion | JWT client fourni par un émetteur de confiance ; session administrateur | Pas d'inscription, annuaire client ou SSO commercial prêt à l'emploi |
| SaaS multi-tenant | Stores B2B internes intégrés ; accès métier HTTP/Vue encore fermé | Pas une offre hébergée multi-entreprise opérationnelle |

Sources internes : [Advice](../catalogue-advice.md), [ORDER](../order-flow.md),
[CSV et index](../database.md#create_only-product-csv-imports-v6),
[routes et composants Vue](../../frontend/README.md). Une fonction présente dans
le code peut rester bloquée dans une installation non provisionnée : vérifier les
index et la configuration avant de la présenter comme utilisable.

Le modèle produit historique ne porte pas de devise, de TVA ni de spécifications techniques.
Les exemples en euros sont une convention du catalogue synthétique, pas une
capacité de conversion ni un modèle comptable validé.

## Alternatives existantes

| Option | Atouts à reconnaître | Quand explorer StockPilot |
|---|---|---|
| Excel / Microsoft 365 | Outil familier et flexible ; collaboration et fonctions IA existent selon l'offre | Lorsque des demandes répétitives exigent une recherche et une confirmation dans un flux transactionnel adapté |
| Odoo Inventory et sa suite | Gestion de stock étendue, réapprovisionnement, codes-barres et intégration à d'autres applications | Lorsque le besoin est plus étroit et qu'un pilote peut démontrer un avantage concret de la conversation sur le catalogue |
| Logiciel de stock déjà installé | Processus, intégrations et formation déjà amortis | Seulement si un problème précis demeure et si le coût d'un double référentiel est maîtrisé |

Excel propose notamment la collaboration en ligne et des offres avec Copilot : ne
pas le présenter comme un simple fichier dépourvu d'IA.
Source consultée le 17 octobre 2026 : [Microsoft Excel](https://www.microsoft.com/fr-fr/microsoft-365/excel).

Odoo présente une application gratuite avec utilisateurs illimités dans son offre
« One App Free » ; ses autres offres et conditions varient. Son catalogue comprend
des fonctions avancées de stock. StockPilot n'a pas démontré un avantage de coût
ou de couverture fonctionnelle face à cette offre.
Sources consultées le 17 octobre 2026 : [tarifs Odoo](https://www.odoo.com/pricing),
[fonctions Inventory](https://www.odoo.com/app/inventory-features).

L'hypothèse de différenciation est un assistant français limité à des faits du
catalogue et à un parcours de confirmation contrôlé, déployable en installation
isolée avec inférence locale. Sa simplicité, sa qualité et son coût complet doivent
être comparés sur les mêmes tâches ; ils ne sont pas acquis.

## Présentation orale courte

« StockPilot aide à retrouver une référence disponible et des alternatives dans
un catalogue. Vous pouvez poser une question en français, changer le budget et
comparer les prix vérifiés. Une demande de commande reste une proposition jusqu'à
votre confirmation. Nous proposons d'abord une démonstration sur des données
synthétiques, puis un pilote isolé avec des critères de succès convenus. Aujourd'hui,
ce n'est ni un ERP complet ni un SaaS multi-entreprise. »

Ne promettre ni « zéro hallucination » sur toute conversation, ni conformité
globale, économie chiffrée, disponibilité garantie ou remplacement complet d'un ERP.
Les protections sur les faits produits et les commandes sont testables ; leur
existence ne dispense pas d'une validation sur le catalogue et les usages du pilote.
