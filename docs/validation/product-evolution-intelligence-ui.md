# StockPilot Intelligence — interface de pilotage

Base indépendante : `main` fd7e212f3f9688f70aa7a121f7022b3d4ee72aa2.
Ce lot nécessite le contrat serveur du lot Analytics pour afficher le module.
Il ne modifie ni modèle, ni prompt, ni transaction ORDER. Aucun déploiement.

## Diagnostic et décision

L'application stable possède déjà un espace BFF, des préférences FR/EN et thème,
un client qui invalide les réponses périmées, un dialogue clavier et une recherche
SKU. Elle ne possède ni route Intelligence ni permission analytique. Ces briques
sont réutilisées ; aucun deuxième client d'identité, token navigateur, framework
graphique ou bibliothèque de composants n'est ajouté.

Le lien et la route exigent le rôle MANAGER ou ORGANIZATION_ADMIN et
`analytics:read`. Le lot Analytics applique séparément la même règle au serveur.
Le tenant vient de la session et ne figure jamais dans les paramètres de filtre.
Un serveur ancien qui n'annonce pas cette permission ne propose pas le module.

## Parcours et design

Une page dédiée propose organisation courante, période 30/90/180/365 jours,
horizon 7/30/60/90 jours, catégorie exacte, SKU choisi dans les suggestions,
statut, seuil indicatif et remise personnalisée. Le chargement efface l'ancienne
analyse, annule la requête précédente et expose un état d'erreur avec réessai.
Les réponses sont validées avant rendu, y compris identité, nombres, cohérence
du stock, pagination, horizon, bornes et absence d'action automatique.

Les cartes globales distinguent disponible, réservé, rupture et faible stock.
La valorisation est explicitement au tarif courant, par devise, sans conversion
implicite. Les nombres monétaires restent des entiers en unités mineures et sont
formatés avec BigInt, y compris les marges négatives et montants extrêmes.

Les graphiques et tableaux de la page sont identifiés comme portant sur les
20 produits affichés. Une journée manquante coupe la courbe et reste indisponible,
elle ne devient pas une vente nulle. Les valeurs tabulaires rendent les courbes
accessibles sans dépendre de la couleur. Un historique DEMO porte un bandeau
explicite. Les ajustements administratifs sont distincts des ventes.

Les recommandations affichent leur motif : rupture actuelle, seuil, besoin
estimé, absence de vente observée ou ajustement à examiner. Le classement des
ventes et la couverture moyenne restent ceux de la page. L'analyse ABC porte
sur le capital actuel au coût connu de la page, avec une seule devise et tous les
coûts disponibles ; ce n'est pas une ABC de consommation annuelle.

Le détail sépare observations, prévisions, hypothèses fournisseur et simulations.
Il affiche méthode, erreur MAE/RMSE, baseline naïve, fenêtres et dates de mesure,
plage empirique lorsqu'elle existe, couverture, paramètres et provenance.
Les simulations 5/10/15 % et personnalisée n'annoncent ni élasticité ni bénéfice
futur. Aucune réservation, commande ou promotion n'est exécutée.

Les explications sont déterministes et issues du contrat serveur. Ollama n'est
pas appelé ; aucune qualité de génération LLM réelle n'est revendiquée. L'export
JSON est volontaire, limité à la réponse autorisée en mémoire et sa pagination.
Aucun résultat métier n'est enregistré dans le stockage navigateur.

## Accessibilité et preuves

Les composants existants CatalogueCombobox et DetailsPanel assurent suggestions
au clavier, libellés, focus initial, piège de focus, ESC et retour au déclencheur.
Les contrôles ont une cible de 44 px ; les tableaux défilent dans leur région
sans déborder la page. Les thèmes clair/sombre et langues FR/EN sont conservés.

- RED : la même visite sur main ne trouve pas la route ni le titre Intelligence.
  Capture locale `tmp/ui-baseline/intelligence-before.png`.
- GREEN : `frontend/ux-e2e/intelligence.spec.ts`, huit combinaisons FR/EN ×
  clair/sombre × 390/1280 px, plus accès interdit et erreur/réessai/vide.
- Captures après : `frontend/test-results/intelligence-*/intelligence-*.png`.
  La fixture est une réponse du vrai endpoint Analytics sur Mongo jetable :
  `frontend/ux-e2e/fixtures/intelligence.json`, étiquetée DEMO_SYNTHETIC.
  Les tests de rendu interceptent le BFF ; ils ne prouvent pas à eux seuls OIDC.
- `frontend/src/b2b/intelligence.test.ts` : contrats malformés, nombres non finis,
  incohérence de stock, action automatique, incertitude non autorisée, montants
  exacts, marge négative, coûts absents et devises incompatibles.

Régressions : **650 tests frontend**, typecheck/lint/build, **94 tests Playwright**,
**5 925 tests backend**, 370 sous-tests, **823 intégrations MongoDB/PostgreSQL**,
**122/122 evals déterministes**. Une invocation Playwright a d'abord utilisé un
nom de fichier de configuration inexistant ; la commande corrigée a exécuté
la suite complète, sans modification de ses assertions.

Campagne réelle Keycloak 26.8.1 : **33 contrôles réussis**, rapport local
`tmp/v1372-yt2xrw9h/report.json`, nettoyage et ressources protégées inchangées.
Composition testée : backend Analytics `6c43bb45c23e6f6ee741eea21c03ec748427e7bc`
sélectionné par PYTHONPATH dans le harness de ce worktree, sans fusion de branches.
La CI vérifie séparément la branche et son backend stable de base.
`B2B_TEST_INTELLIGENCE=1` active une vérification supplémentaire du vrai endpoint,
du tenant, de l'absence de vente inventée, du clavier et du rendu mobile dans
`frontend/intelligence-browser.mjs`. Elle est exécutée avec le backend du lot
Analytics et le frontend de ce lot. Aucun fournisseur OIDC ni appel API n'est
intercepté dans cette campagne. La fixture riche de rendu reste synthétique ;
aucune donnée de production n'a été utilisée.

## Limites et qualification

Les observations commerciales qualifiées et profils fournisseur proviennent du
modèle additif du lot Analytics. Les commandes réservées ne sont jamais converties
en ventes. Sans ces sources, le dashboard fournit les stocks courants et indique
les analyses indisponibles. Il n'existe pas encore de connecteur d'ingestion.
Pas de vieillissement de lots, saisonnalité annuelle ou rentabilité promotionnelle
inventés. Le rapport backend documente les formules et le backtesting.

GO pour revue de développement avec le lot Analytics. NO-GO pour livraison sans
qualification séparée de la migration 7, revue requise et autorisation explicite.
La v0.2.8 déployée reste intacte. Aucun merge, tag ou release n'est effectué.
