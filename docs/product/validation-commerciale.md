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

## Questionnaire de découverte — 12 questions

Noter date, rôle (sans identité privée dans Git), tâche récente et réponses avec
l'accord de l'interlocuteur. Ne pas commencer par une promesse de gain. Utiliser
les questions 1–10 avant la démonstration, puis 11–12 après si elle a lieu.

1. Racontez la dernière demande client où trouver la bonne référence a été difficile.
2. Quels outils et quelles personnes avez-vous consultés, et quelles informations avez-vous ressaisies ?
3. Où se trouvent les références, prix et stocks qui font foi ? Qui les met à jour ?
4. Combien de recherches comparables faites-vous par jour, et combien de temps prend une demande récente, attente et vérification comprises ?
5. Que faites-vous lorsqu'une référence est ambiguë, absente ou en rupture ?
6. Quelles erreurs de référence, de prix, de stock ou de quantité avez-vous rencontrées récemment ? Quelles conséquences ?
7. Comment préparez-vous, validez-vous et annulez-vous une commande ? Qui peut réserver le stock ?
8. Qu'avez-vous déjà essayé avec vos fichiers ou votre logiciel actuel ? Qu'est-ce qui fonctionne bien et doit être conservé ?
9. Quelles contraintes de confidentialité, d'accès, de matériel et de sauvegarde empêcheraient un essai sur une copie isolée ?
10. Qui serait référent pour un essai de 2–4 semaines, et combien de temps l'équipe pourrait-elle y consacrer ?
11. Après la démonstration, quelle tâche voudriez-vous comparer à votre méthode actuelle ? Qu'est-ce qui vous ferait refuser le pilote ?
12. Qui décide du budget, quelle enveloppe serait envisageable et quelles preuves faudrait-il pour payer ? Préférez-vous poursuivre, attendre ou arrêter ?

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

## Grille avant/après à remplir — aucune mesure client disponible

Une ligne par tâche et utilisateur pseudonymisé, même catalogue/source et mêmes
conditions. Alterner l'ordre méthode actuelle/StockPilot ; noter l'ordre, la
formation et l'aide reçue. Le temps inclut attente modèle, vérification et reprises.
Les propositions d'échantillon et de seuils ci-dessus restent à convenir.
Conserver les fichiers de séance privés hors Git et ne partager que des agrégats
anonymisés autorisés.

| Ligne de mesure | Valeur à remplir |
|---|---|
| Date / rôle / code utilisateur / ordre des méthodes | À renseigner |
| Tâche / catalogue et version / référence attendue / source officielle | À renseigner |
| Temps méthode actuelle, en secondes | Non mesuré |
| Temps StockPilot, en secondes | Non mesuré |
| Référence, prix et stock exacts : actuelle puis StockPilot | Oui / non / non vérifiable, séparément |
| Erreurs et gravité : actuelle puis StockPilot | Nombre, nature, correction nécessaire |
| Issue : actuelle puis StockPilot | Réussite / échec / abandon / panne ; raison et temps écoulé |
| Satisfaction : actuelle puis StockPilot | Note 1–5 après chaque tâche, même question : « Cette méthode vous a-t-elle permis de finir facilement ? » |
| Aide et support : actuelle puis StockPilot | Minutes, motif, intervenant ; distinguer formation et incident |
| Budget et intention de payer après essai | Montant/devise/période déclaré, décideur, conditions ; oui / non / à revoir |
| Commentaire contradictoire / incident | À renseigner, sans données personnelles inutiles |

Synthèse de bilan :

| Indicateur | Méthode actuelle | StockPilot | Calcul / décision |
|---|---|---|---|
| Tâches tentées / réussies / échouées / abandonnées | Non mesuré | Non mesuré | Tout compter, détailler les pannes |
| Temps médian et cas lent | Non mesuré | Non mesuré | Sur paires de tâches terminées : (avant−après)/avant × 100 ; avant = 0 ⇒ non calculable ; publier aussi les échecs exclus de ce calcul |
| Exactitude vérifiable | Non mesuré | Non mesuré | Tâches exactes / tâches vérifiables, avec dénominateur et non-vérifiables séparés |
| Erreurs par type et gravité | Non mesuré | Non mesuré | Aucun incident d'identité ou de réservation toléré pour poursuivre sans analyse |
| Satisfaction médiane / répondants | Non mesuré | Non mesuré | Même échelle 1–5, commentaires et non-réponses conservés |
| Coût de support sur la période | Non mesuré | Non mesuré | Heures × coût horaire convenu + frais réels ; formation/installation séparées pour éviter double compte |
| Intention de payer / décideurs consultés | Non mesuré | Non mesuré | Déclaratif distinct d'un accord écrit, d'un pilote payé et d'un renouvellement |
| Décision et désaccords | À renseigner | À renseigner | Poursuivre / réduire / arrêter, responsable et date |

Ne pas transformer un temps plus court en économie de salaire, vente ou marge
acquise. Comparer coût et qualité, conserver les résultats négatifs et relier
toute proposition au [périmètre pilote](offre-pilote.md).

## Objections et réponses à vérifier

| Objection | Réponse honnête | Preuve / décision attendue |
|---|---|---|
| « Excel suffit. » | C'est possible ; garder l'outil si la tâche est déjà rapide et fiable | Comparaison chronométrée, coût du changement inclus |
| « Odoo le fait déjà. » | Odoo couvre un périmètre plus large ; aucun avantage universel revendiqué | Comparer une tâche et un coût complet identiques ; renoncer si absence de gain |
| « L'IA invente. » | Advice relit les faits produit et refuse les sorties non vérifiables ; la compréhension reste probabiliste | Cas manquant/ambigu, panne et injection ; journal d'incidents, pas promesse absolue |
| « Mes données restent-elles chez moi ? » | Une installation et une inférence locales sont possibles ; vérifier les flux réellement configurés | Carte de flux, absence d'export non autorisé, contrôle des accès et sauvegardes |
| « Peut-elle vendre deux fois le même stock ? » | Confirmation explicite et transaction Mongo sont prévues ; aucune réservation en conseil/préparation | Test concurrent et rejeu sur l'installation pilote ; incidents bloquants analysés |
| « Je veux plusieurs agences/entreprises et du SSO. » | Keycloak/BFF, permissions et stores isolés existent en interne ; le parcours HTTP/Vue unifié n'est pas livré | [Gates de la démo B2B](../b2b-demo.md) ; aucune ouverture des données globales |
| « Je veux facturation, codes-barres et ERP. » | Ce périmètre n'est pas démontré dans StockPilot | Qualifier intégration ou autre solution, sans promesse de livraison |
| « Peut-on importer nos fichiers Excel ? » | Parcours historique : CSV conforme, création seule, stock zéro ; les modes internes B3 ne sont pas exposés par cette UI | Aperçu, petit lot, reçu ; pas de synchronisation automatique |
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
