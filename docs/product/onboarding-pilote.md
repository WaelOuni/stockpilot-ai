# Onboarding d'un pilote sur installation isolée

Proposition du 17 octobre 2026. Un pilote exige un accord humain sur son périmètre,
ses données et son exploitation. Cette procédure n'active aucun mode SaaS et ne
vaut pas autorisation de migrer des données persistantes.

## Conditions d'entrée

Choisir une seule entreprise, un référent métier et un opérateur technique. Commencer
avec un catalogue d'outillage représentatif et une copie autorisée sans données
personnelles inutiles. Désigner explicitement le référentiel faisant autorité :
StockPilot n'a pas de synchronisation ERP ou Excel continue. Deux stocks modifiables
en parallèle sans rapprochement seraient une source d'erreurs.

Avant toute donnée réelle, convenir des accès, personnes autorisées, conservation,
suppression et restitution, sauvegardes, assistance et procédure d'incident. Vérifier
les obligations contractuelles, de protection des données et de licences avec les
responsables compétents. La [frontière SaaS](../security/saas-auth-architecture.md)
reste fermée ; `customer_id` ne sépare pas plusieurs entreprises.

Le premier essai peut se limiter à une démonstration supervisée sur copie synthétique.
Une ouverture publique ou une utilisation comme stock de production nécessite une
revue et une décision distinctes : la candidate locale ne prouve pas cette aptitude.

## Parcours d'entrée et preuves

| Étape | Responsable | Action / condition de sortie |
|---|---|---|
| 1. Cadrer | Référent métier + responsable StockPilot | Définir 3 tâches, les utilisateurs, un volume de demandes et une durée de 2–4 semaines ; mesurer la situation initiale |
| 2. Préparer | Opérateur | Installation dédiée, projet Compose, réseaux/volumes/bases/secrets propres ; identifier le commit et les modèles ; aucun lien avec DEV, v017 ou une autre entreprise |
| 3. Qualifier l'exploitation | Opérateur | HTTPS, JWT et sessions, index requis, santé des dépendances, sauvegarde puis restauration sur une autre instance jetable ; procédure de récupération des checkpoints |
| 4. Contrôler le catalogue | Référent métier | SKU exacts et uniques, catégories supportées, prix finis/non négatifs, stock entier ; vérifier 10 références et cas limites contre la source |
| 5. Prévisualiser/importer | Administrateur supervisé | CSV conforme, aperçu sans écriture, confirmation explicite d'un lot ; reçu conservé ; produits nouveaux à stock zéro |
| 6. Charger le stock | Opérateur + métier | Rapprochement signé des stocks initiaux ; ajustements autorisés avec motifs et identifiants d'opération ; pas de stock ajouté au CSV |
| 7. Attribuer les accès | Opérateur | Émettre les JWT depuis l'autorité de confiance hors navigateur ; fournir les accès par canal privé ; tester expiration, révocation et séparation administrateur/client |
| 8. Former | Référent | Recherche, nom ambigu, comparaison, budget, préparation puis confirmation, lecture de statut ; aucune mutation incertaine resoumise aveuglément |
| 9. Accepter | Métier + technique | Exécuter la grille ci-dessous, signer les limites, autoriser ou refuser l'essai réel |
| 10. Suivre/sortir | Référent + opérateur | Revue hebdomadaire des incidents et indicateurs ; export/restauration convenus ; retirer les accès en fin de pilote |

Il n'existe pas aujourd'hui de création d'utilisateurs libre-service ni de rôles
d'organisation. Le client reçoit un JWT contrôlé ; le back-office possède une
configuration d'administrateur de démonstration. Si le pilote exige comptes
nominatifs, révocation individuelle immédiate ou plusieurs rôles d'équipe, traiter
ces exigences comme des prérequis manquants, pas comme une fonction disponible.

## Import sans surprise

Utiliser l'exemple téléchargeable de `/admin/products/import` et le
[contrat CSV](../database.md#create_only-product-csv-imports-v6). Colonnes exactes :
`sku,name,category,price`. UTF-8 ; séparateur virgule ou point-virgule ; catégories
`DRILL`, `SCREWDRIVER`, `SAW`, `SANDER`, `OTHER`. Le fichier ne doit pas contenir
de colonne stock. Par défaut : 2 MiB et 500 produits au maximum par lot.

Les SKU existants sont refusés : ce mécanisme est CREATE_ONLY et ne remplace pas un
outil de synchronisation. Un catalogue plus grand nécessite plusieurs lots contrôlés
avec rapprochement et reçus, pas un contournement des limites. Les index de SKU,
de mouvements et de reçus doivent être provisionnés explicitement sur la nouvelle
installation par l'opérateur ; leur présence dans une DEMO ne les provisionne pas
dans une autre.

Une erreur réseau après confirmation peut masquer un succès serveur. Conserver
l'identifiant et rapprocher le reçu par GET ; ne pas recommencer sous un nouvel
identifiant. Un 404 de reçu ne prouve pas une absence de commit. Même règle de
prudence pour les ajustements et les commandes.

## Grille d'acceptation à remplir

Tous les résultats ci-dessous sont **NOT_TESTED pour le futur pilote** tant que
ses propres preuves ne sont pas consignées.

| Contrôle | Critère |
|---|---|
| Données | 10 SKU échantillonnés, prix et stocks exacts ; ambiguïtés connues identifiées |
| Advice | 3 correspondances sous plafond strict lorsqu'elles existent ; aucune réservation ; produit manquant non remplacé |
| Conversation | Budget remplacé ; nouveau thread et autre client sans référence héritée |
| Commande | Préparation sans réservation ; confirmation unique ; rejeu idempotent ; annulation autorisée et restitution exacte |
| Imports | Aperçu sans mutation ; lot confirmé créé à zéro ; doublons refusés ; reçu vérifiable |
| Authentification | Expirations, protections de session/CSRF et accès interdits réellement testés |
| Continuité | Restauration isolée réussie ; contexte après redémarrage vérifié ; procédure de reprise PostgreSQL connue |
| Usage | Utilisateur capable de distinguer conseil, proposition, confirmation et état courant |

Pour la candidate V13.3, un redémarrage PostgreSQL hors du lanceur peut laisser le
client de checkpoints fermé : réexécuter son lanceur de démarrage selon le runbook,
puis vérifier une requête de contexte. `/health` seul prouve la vie du processus,
pas l'état de toutes ses dépendances. Cette limite d'exploitation doit être résolue
ou acceptée explicitement pour un pilote ; elle n'est pas une garantie de haute
disponibilité.

Un incident d'identité, de réservation incorrecte ou de fuite de données suspend les
mutations du pilote, conserve les preuves et déclenche une revue avant reprise.
Le support doit avoir un interlocuteur, des horaires et un canal convenus ; aucun
support 24/7 ou SLA n'est promis par ces documents.
