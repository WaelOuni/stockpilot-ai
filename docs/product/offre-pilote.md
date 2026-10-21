# Proposition de pilote accompagné — document non contractuel

V15.1, 19 octobre 2026. **Base de discussion uniquement**, sans devis accepté,
commande, abonnement, achat ni autorisation de déploiement. Aucun gain client
ou prix validé par le marché. Le [guide d'onboarding](onboarding-pilote.md)
décrit le parcours technique historique ; cette fiche fixe le cadre à discuter.
Le futur parcours B2B suit le [guide canonique](../b2b-demo.md), sans activation
automatique ni promesse de disponibilité.

## Périmètre proposé et durée

Une entreprise, un site volontaire, un référent métier et 2 à 5 utilisateurs
accompagnés, sous réserve de validation des accès réellement nécessaires.
Un lot initial limité, par exemple 50 à 200 références d'outillage autorisées,
avec noms et références uniques. Les limites sont des hypothèses à adapter.
Installation et bases propres à l'essai ; aucune utilisation de v017, V13.3 ou de la démo historique 5175/8003,
aucune mutualisation de données entre entreprises.

Trois tâches : retrouver une référence, proposer des alternatives sous budget,
préparer puis confirmer une commande fictive sur copie. Le stock du système
habituel reste la source officielle. Aucun traitement de vente réelle ou double
stock de production sans décision séparée et rapprochement défini.
Pas d'ERP, SSO, paiement, facturation ou développement sur mesure inclus.

| Période proposée | Travail et sortie attendue |
|---|---|
| Avant départ | Accord sur tâches, catalogue, accès, sauvegarde/restauration démontrée, règles de données, support et budget ; refus de démarrer si un prérequis manque |
| Semaine 1 | Installation dédiée autorisée, contrôle de 10 références, formation, mesure de la méthode actuelle puis essais accompagnés |
| Semaine 2 | Répétition des mêmes tâches, incidents et temps de support notés ; bilan si essai de 2 semaines |
| Semaines 3–4, si convenues | Échantillon complémentaire sur le même périmètre ; bilan contradictoire et décision finale, sans renouvellement automatique |

## Prérequis et responsabilités à compléter ensemble

| Responsable | Engagement à convenir avant le départ |
|---|---|
| Référent client | Choisir les tâches, autoriser une copie de catalogue, identifier la source de stock/prix et vérifier les références ; rendre les utilisateurs disponibles |
| Responsable StockPilot | Vérifier adéquation fonctionnelle et capacité matérielle, expliquer limites et accès ; livrer formation et suivi dans le périmètre accepté |
| Opérateur désigné | Préparer installation isolée, accès privés, sauvegardes et restauration testée, reprise et journal d'incidents ; pas de données sensibles dans le suivi partagé |
| Ensemble | Choisir lieu d'installation, matériel, personnes autorisées, durée de conservation/suppression, responsabilités de données et conditions d'arrêt ; vérifier licences et cadre contractuel avant données réelles |

Le support est proposé sur rendez-vous, avec un interlocuteur et des horaires
écrits avant démarrage ; aucune permanence 24/7 ni délai garanti implicite.
L'interface historique ne fournit pas les rôles d'équipe B2B ni la révocation
individuelle immédiate des JWT clients. Les fondations internes V13.7 ne prouvent
pas une révocation atomique des sessions et de toutes les opérations inter-stores. Si ces fonctions sont indispensables, le pilote est
bloqué jusqu'à solution approuvée, pas contourné.

## Réussite, arrêt et sortie

À approuver avant mesure, dans la [grille avant/après](validation-commerciale.md) :

- Identité, prix et stock exacts pour les références contrôlées ; aucune réservation
  avant confirmation, aucune mutation incorrecte ou fuite de données.
- Utilisateurs capables de distinguer conseil, préparation et confirmation.
- Temps complet et erreurs comparés à la méthode actuelle sur les mêmes tâches ;
  seuil d'amélioration **proposé de 20 % sur la médiane**, à accepter ou modifier.
  Ce seuil est un objectif, jamais un résultat promis.
- Satisfaction, besoin récurrent, temps de support et budget jugés compatibles
  avec une poursuite par le référent et le décideur.
- Une panne, un abandon ou une tâche échouée restent comptés ; aucune sélection
  des seuls bons résultats.

Incident d'identité, réservation erronée ou exposition de données : suspendre
les mutations et analyser avant reprise. Arrêter aussi si la source de stock est
ambiguë, les accès insuffisants ou le support non soutenable.
Au bilan : poursuivre sous nouvel accord, réduire le périmètre ou arrêter.
Rapprocher les commandes fictives, restituer les éléments convenus, retirer les
accès selon leurs mécanismes disponibles et appliquer la conservation/suppression
convenue ; ne pas promettre un bouton d'export ou une révocation non développés.

## Hypothèses de budget à discuter, pas une offre acceptée

Réutiliser le scénario A des [coûts internes](couts-et-pricing.md) :
matériel et inférence locaux du client, sans achat cloud décidé ici.
Hypothèse de mise en service **900 €** (12 h estimées à 50 € = 600 € de coût de
travail, plus 300 € avant frais fixes), puis **299 € pour un mois d'accompagnement**.
Pour 2–4 semaines, enveloppe de discussion **1 199 €**, sans renouvellement
automatique ; facturation, éventuel prorata, fiscalité et inclusions à valider.
Ces montants sont des hypothèses commerciales, pas une devise du catalogue produit.

L'hypothèse mensuelle interne comprend 1 h de maintenance et 1,5 h de support.
Confirmer ce qui relève des 12 h initiales, des heures de suivi et des éventuels
dépassements avant tout devis ; aucun travail supplémentaire engagé automatiquement.
Matériel, électricité, temps utilisateur, incidents exceptionnels et coûts
contractuels ne sont pas couverts par un prix présenté comme « tout compris ».
Les estimations fournisseurs historiques ne valent pas tarifs actuels vérifiés.

Décisions humaines : prospect et décideur, tâches/données, accès, hébergement,
responsables, durée, critères, support, enveloppe et contrat. Aucun pilote réel
n'est lancé par la publication de cette proposition.
