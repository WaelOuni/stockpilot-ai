# Validation de marché, objections et risques

Plan préparé le 17 octobre 2026. Aucun entretien prospect, engagement commercial
ou paiement n'a été réalisé dans cette mission. Les nombres ci-dessous sont des
objectifs d'expérimentation à approuver, pas des résultats.

## Expérience proposée

Commencer par 8–12 entretiens avec des grossistes/distributeurs/PME à Lille et dans
les Hauts-de-France, dont au moins trois utilisant déjà un logiciel de stock.
Recrutement, consentement et messages externes restent à la charge du responsable
humain ; aucun contact n'est déclenché par ce document. Ne pas sélectionner seulement
des connaissances favorables au produit. Reporter une exploration en Tunisie après
les premières conclusions françaises et une vérification des besoins locaux.

Demander des exemples récents avant de montrer la solution :

1. « Racontez votre dernière recherche d'une alternative disponible pour un client. »
2. « Quels outils, personnes, vérifications et ressaisies avez-vous utilisés ? »
3. « Où sont le SKU, le stock faisant autorité, le prix, la devise et la TVA ? »
4. « Combien de demandes comparables recevez-vous ? Combien de minutes chacune ? »
5. « Que se passe-t-il lorsque le stock change pendant une prise de commande ? »
6. « Qu'avez-vous déjà essayé, et pourquoi le problème persiste-t-il ? »
7. « Quelles contraintes d'accès, de données et de support bloqueraient un pilote ? »
8. Après démonstration : « Sur quelle tâche l'utiliseriez-vous réellement ? Qui
   décide et finance ? Quel engagement concret seriez-vous prêt à examiner ? »

Ne pas assimiler enthousiasme, clic ou préférence déclarée à une volonté de payer.
Une lettre d'intention avec périmètre, décideur, budget et conditions est un signal
plus fort ; un pilote payé et utilisé est un autre niveau de preuve.

## Mesurer la valeur de l'assistant

Comparer sur une copie contrôlée du même catalogue trois tâches : référence précise,
alternatives sous budget et préparation/confirmation d'une commande. Tester le flux
habituel puis StockPilot, en alternant l'ordre entre utilisateurs pour limiter
l'effet d'apprentissage. Viser cinq utilisateurs et dix tâches chacun si accessibles ;
documenter l'échantillon réel et les échecs, sans déduire une généralisation statistique.

| Indicateur | Méthode | Seuil proposé pour poursuivre |
|---|---|---|
| Temps utile par demande | Début de recherche → résultat exploitable ; médiane et cas lents | Au moins 20 % de réduction médiane sur tâches répétitives, hypothèse à convenir |
| Exactitude | SKU/prix/stock du résultat confrontés à la source de référence au même instant | Zéro erreur d'identité ou de réservation dans l'échantillon ; arrêt et analyse sinon |
| Clarification | Ambiguïtés volontairement incluses, puis observation de la résolution | Aucune sélection arbitraire ; utilisateur comprend la demande de précision |
| Compréhension commande | Demander quand le stock est réservé, avant/après confirmation | Tous les participants testés distinguent proposition et confirmation |
| Adoption | Utilisation réelle hebdomadaire, abandons et raisons, avec accord | Au moins deux utilisateurs reviennent sur un besoin répété, hypothèse |
| Coût de service | Temps d'onboarding, maintenance, incidents, support ; coûts infrastructure | Compatible avec un prix discuté et une contribution positive documentée |
| Signal commercial | Décideur et conditions explicites ; pas uniquement « c'est intéressant » | Au moins un accord de pilote avec budget et critères de sortie |

Exemple de calcul de valeur, **sans résultat observé** : 20 demandes/jour × 2 minutes
économisées × 20 jours = 800 minutes, soit 13,3 h/mois. À une valeur interne choisie
de 25 €/h, cela représente environ 333 €/mois avant coût d'adoption. Ce n'est ni une
économie de masse salariale garantie ni une justification automatique d'un prix.
Mesurer aussi le temps de vérification, l'attente LLM et les erreurs évitées ; ne pas
valoriser chaque réponse comme une vente supplémentaire.

## Objections et réponses à vérifier

| Objection | Réponse honnête | Preuve / décision attendue |
|---|---|---|
| « Excel suffit. » | C'est possible ; garder l'outil si la tâche est déjà rapide et fiable | Comparaison chronométrée, coût du changement inclus |
| « Odoo le fait déjà. » | Odoo couvre un périmètre plus large ; aucun avantage universel revendiqué | Comparer une tâche et un coût complet identiques ; renoncer si absence de gain |
| « L'IA invente. » | Advice relit les faits produit et refuse les sorties non vérifiables ; la compréhension reste probabiliste | Cas manquant/ambigu, panne et injection ; journal d'incidents, pas promesse absolue |
| « Mes données restent-elles chez moi ? » | Une installation et une inférence locales sont possibles ; vérifier les flux réellement configurés | Carte de flux, absence d'export non autorisé, contrôle des accès et sauvegardes |
| « Peut-elle vendre deux fois le même stock ? » | Confirmation explicite et transaction Mongo sont prévues ; aucune réservation en conseil/préparation | Test concurrent et rejeu sur l'installation pilote ; incidents bloquants analysés |
| « Je veux plusieurs agences/entreprises et du SSO. » | Multi-tenant, rôles d'organisation et OIDC ne sont pas livrés | Besoin préalable à V14 ; ne pas mélanger les données dans la candidate |
| « Je veux facturation, codes-barres et ERP. » | Ce périmètre n'est pas démontré dans StockPilot | Qualifier intégration ou autre solution, sans promesse de livraison |
| « Peut-on importer nos fichiers Excel ? » | Export CSV conforme, création seule, stock zéro puis rapprochement autorisé | Aperçu, petit lot, reçu ; pas de synchronisation automatique |
| « Le service doit fonctionner sans interruption. » | La candidate locale n'est pas hautement disponible et `/health` n'est pas une preuve de readiness | Plan d'exploitation, restauration et objectif de service négocié avant vente |
| « Pourquoi payer ce prix ? » | Le prix couvre installation/support/infrastructure ; la valeur reste à démontrer | Résultats du pilote et coûts réels, plutôt que prix présenté comme validé |

## Décisions et prochaine action humaine

Poursuivre un pilote isolé si le problème se répète, que le référentiel de stock et
le responsable sont clairs, qu'un gain mesurable existe et qu'un décideur accepte
le périmètre. Suspendre si la donnée est inexploitable, si une mutation incorrecte
survient, si l'accès requis dépasse les garanties actuelles ou si le support rend
l'offre déficitaire. Conserver les objections contradictoires dans la synthèse.

Pour obtenir un premier client payant : un décideur avec problème confirmé, une
installation isolée acceptée, un catalogue contrôlé, des accès adaptés, une
restauration prouvée, un support défini, un contrat validé et un budget cohérent.
Un SaaS multi-tenant n'est pas nécessaire pour explorer ce pilote ; son activation
reste interdite tant que l'isolation complète n'est pas démontrée.

Prochaine action humaine prioritaire : choisir un prospect volontaire et convenir
d'un entretien centré sur une tâche réelle, puis décider si une démonstration
synthétique mérite un pilote. Ne pas lancer une campagne ni annoncer une offre
SaaS publique à partir de la seule réussite des tests techniques.
