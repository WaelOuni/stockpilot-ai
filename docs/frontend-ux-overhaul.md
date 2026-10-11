# StockPilot AI — livraison V8 UI/UX

## Résultat

READY FOR VISUAL REVIEW. Travail non commité sur `feat/frontend-ux-overhaul`, depuis `1d601c90c22a177a02b46cc32210b5d304655b77` (V8 PRODUCT_ADVICE publiée). Aucun commit, push, reset ou rebase effectué. L’arbre initial était propre. Le merge externe `25176ce` est le merge GitHub main → dev, parents `75d94f1` et `ea13755`, sans différence de contenu avec `75d94f1` ; son historique est conservé.

## Audit initial et direction graphique

Inspection réelle des routes existantes, du contexte projet, du README frontend et du runbook. Quatorze écrans capturés à 1440, 768 et 390 px : catalogue, fiche produit, assistant, commandes client et détail, connexion admin, dashboard, commandes admin et détail, produits admin, création, modification, stock/historique et import. Le panneau JWT client existant sert d’authentification client ; aucune nouvelle route inventée.

Défauts constatés : navigation admin débordante à 768 px sur huit écrans, titres et header disproportionnés, champs et selects disparates, boutons/liens collés, filtres peu structurés, recherche admin absente, pagination sans total réel, tableaux difficiles sur mobile et texte de connexion admin trompeur. L’aperçu CSV mobile excessivement long a aussi été corrigé pendant la revue finale.

Direction retenue : fond clair, indigo et bleu marine, typographie système lisible, bordures discrètes, ombres légères, rayons cohérents et grille d’espacement de 4 px. Les tokens sont centralisés dans `frontend/src/style.css`, avec contrôles de 44 px et layouts adaptés aux trois largeurs. Aucun framework UI ajouté.

## Composants et pages

- `AppHeader` : header de 72 px (64 sur mobile), navigation active, menu accessible, Escape et restitution du focus, fermeture au changement de route ; skip link vers le contenu.
- `AdminSidebar` et `AppIcon` : navigation admin structurée, icônes SVG cohérentes, bascule responsive.
- `BasePagination` : première/précédente/numéros/suivante/dernière, plages et total exact, pages totales, états désactivés, état vide et taille de page catalogue. Fallback compatible avec anciens serveurs sans total, sans inventer de pages.
- `ProductFilters` : recherche nom/SKU, catégorie, disponibilité et remise à zéro, contrôles partagés client/admin.
- `DataTable` : région nommée accessible au clavier, vrais tableaux avec caption ; cartes étiquetées sur mobile, aperçu CSV à hauteur bornée et défilement accessible.
- Les cartes produit, états erreur/chargement, formulaires, confirmations inline et timelines existants sont réutilisés. Aucun ensemble de composants de base inutilisés ajouté.

Marges de pages, paddings, espacements de formulaires, boutons, titres, grilles et tableaux harmonisés. Les formulaires gardent leurs validations métier/native, associent les erreurs et conservent les blocages pendant soumission. La connexion admin utilise les vrais contrôles d’authentification et de CSRF.

Dashboard : KPI, commandes récentes, mouvements récents, bouton Actualiser et états harmonisés ; métriques Mongo conservées et vérifiées indépendamment par le probe E2E.

Assistant : messages, saisie, recommandations, erreurs et chargement harmonisés. Les recommandations catalogue réelles sont vérifiées à trois largeurs. Aucun workflow LangGraph ORDER, STOCK_CHECK ou PRODUCT_ADVICE modifié.

## Recherche et pagination serveur

Recherche produit réellement prise en charge : sous-chaîne littérale insensible à la casse sur nom et SKU, combinable avec catégorie et disponibilité. Recherche commandes ajoutée uniquement sur les champs réellement interrogés : identifiant commande, nom produit et identifiant client. Les recherches sont échappées côté Mongo.

Debounce de 400 ms, annulation/invalidation des requêtes dépassées et filtres/pagination URL sur les listes qui utilisent l’URL. Retour/avance et remise à zéro de l’offset sont testés. Les données obsolètes sont retirées pendant une nouvelle requête.

Extension additive `include_total=true` pour produits, commandes client/admin et mouvements. Un `$facet` calcule items bornés et compte exact après le même `$match`, sans charger toutes les données dans le frontend. Tri stable avec identifiant en dernier critère ; portée client appliquée avant le comptage. `has_more` et réponses historiques conservés ; `customer_id: null` historique préservé. Les pages devenues hors limites sont recalées après rafraîchissement ou changement du total.

Les données restent vivantes entre deux navigations : il n’existe pas de snapshot multi-pages. Aucun index ou migration ajouté ; la recherche littérale et le comptage exact peuvent nécessiter un travail de performance sur de grands volumes.

## Validation

- Vitest complet : **371 tests réussis**, 22 fichiers.
- Typecheck `vue-tsc --noEmit`, build Vite et lint ESLint : réussis, sans avertissement final.
- Backend complet : **1 415 réussis, 22 désélectionnés** ; neuf nouveaux tests de totaux, pipeline borné, filtres littéraux, isolation client et compatibilité.
- Playwright historique : **7 parcours réussis ensemble**, sur `stockpilot_e2e`, sans reset : catalogue, commande IA, cycle admin, produits, stock, dashboard, import.
- Playwright visuel : **4 parcours réussis** : pagination réelle avec lot CSV unique de 23 produits, premières/dernières pages et numéros, tailles, totaux, filtres combinés/SKU/aucun résultat, recherche commandes, URL retour/avance ; clavier/menu et réponses réseau dépassées ; recommandations IA réelles ; tous les écrans aux trois largeurs.
- Aucun débordement horizontal global sur les 42 captures finales de base ; contrôles supplémentaires sur recommandations et aperçu CSV. Aucun pageerror, erreur console ou réseau serveur inattendu dans le parcours des écrans. L’erreur 503 du test d’état erreur est injectée volontairement.
- `git diff --check` et vérification des nouveaux fichiers : réussis ; index Git vide.

Navigation clavier, focus visible, labels, aria du menu/pagination, région tableau et erreurs de formulaires vérifiés sur les parcours ciblés. Il ne s’agit pas d’une certification exhaustive WCAG 2.2 AA ni d’une validation sur tous les navigateurs : Chromium est utilisé.

## Captures et revue

Comparateur local : [artifacts/ui-ux/index.html](../artifacts/ui-ux/index.html).

42 captures AVANT et 50 APRÈS : 42 écrans de base, 3 recommandations, 3 aperçus CSV, état chargement et erreur mobile. Les fichiers sont dans `artifacts/ui-ux/before` et `artifacts/ui-ux/after`, avec `checks.json`. Captures inspectées visuellement sur des largeurs représentatives pour les quatorze écrans, complétées par les contrôles automatisés sur toutes les largeurs. Les données E2E ont évolué pendant les parcours : les compteurs avant/après ne représentent pas un dataset figé. Les artefacts sont ignorés par Git, disponibles localement pour la revue.

## Sécurité et reproduction

Aucune écriture sur `stockpilot`, les bases PostgreSQL normales ou la DEMO de référence. Aucun seed destructif, migration ou ingestion RAG exécuté. Les scénarios de mutation utilisent exclusivement `stockpilot_e2e` et les garde-fous existants ; authentification admin réelle, credentials éphémères jamais inclus dans les captures/rapport. Les données ajoutées pour pagination et les mutations métier restent dans E2E.

Depuis `frontend`, après chargement silencieux de l’environnement local :

```sh
npm test
npm run build
npm run lint
E2E_NO_RESET=1 npx playwright test
npx playwright test --config playwright.visual.config.ts
```

Le lancement visuel utilise toujours `--no-reset`. Pour une capture initiale en lecture seule : `VISUAL_STAGE=before npx playwright test --config playwright.visual.config.ts` (ne lance que les écrans). Le mode sans reset exige un environnement E2E déjà provisionné et disponible. Le lancement historique sans `E2E_NO_RESET=1` conserve son comportement de réinitialisation antérieur ; ne pas l’utiliser pour reproduire cette livraison.

## Limites résiduelles

Aucun défaut bloquant observé. Validation humaine graphique encore attendue ; pas de certification accessibilité exhaustive ou de mesure de performance à grande échelle. L’authentification client reste le panneau JWT existant en mémoire. Ollama, Mongo et PostgreSQL restent des dépendances nécessaires aux parcours intégrés ; aucune promesse de fonctionnement offline ni de recommandation technique absente du catalogue. Aucun changement d’architecture métier.

## Fichiers modifiés et nouveaux

- `.gitignore` — modifié
- `docs/frontend-ux-overhaul.md` — nouveau
- `frontend/README.md` — modifié
- `frontend/e2e/admin-dashboard.spec.ts` — modifié
- `frontend/e2e/admin-import.spec.ts` — modifié
- `frontend/e2e/admin-products.spec.ts` — modifié
- `frontend/e2e/admin.spec.ts` — modifié
- `frontend/e2e/catalogue-navigation.spec.ts` — modifié
- `frontend/e2e/order-flow.spec.ts` — modifié
- `frontend/e2e/support/backend_probe.py` — modifié
- `frontend/playwright.config.ts` — modifié
- `frontend/playwright.visual.config.ts` — nouveau
- `frontend/src/App.test.ts` — modifié
- `frontend/src/App.vue` — modifié
- `frontend/src/api/admin.test.ts` — modifié
- `frontend/src/api/admin.ts` — modifié
- `frontend/src/api/client.test.ts` — modifié
- `frontend/src/api/client.ts` — modifié
- `frontend/src/api/pagination.ts` — nouveau
- `frontend/src/components/AdminLogout.vue` — modifié
- `frontend/src/components/AdminSidebar.vue` — nouveau
- `frontend/src/components/AppHeader.test.ts` — nouveau
- `frontend/src/components/AppHeader.vue` — nouveau
- `frontend/src/components/AppIcon.vue` — nouveau
- `frontend/src/components/BasePagination.test.ts` — nouveau
- `frontend/src/components/BasePagination.vue` — nouveau
- `frontend/src/components/CustomerTokenPanel.vue` — modifié
- `frontend/src/components/DataTable.vue` — nouveau
- `frontend/src/components/ProductFilters.vue` — nouveau
- `frontend/src/features/orders/CustomerOrders.test.ts` — modifié
- `frontend/src/features/orders/CustomerOrders.vue` — modifié
- `frontend/src/pages/AdminLoginPage.vue` — modifié
- `frontend/src/pages/AdminOrdersPage.vue` — modifié
- `frontend/src/pages/AdminPages.test.ts` — modifié
- `frontend/src/pages/AdminProductFormPage.vue` — modifié
- `frontend/src/pages/AdminProductImportPage.vue` — modifié
- `frontend/src/pages/AdminProductStockPage.vue` — modifié
- `frontend/src/pages/AdminProducts.test.ts` — modifié
- `frontend/src/pages/AdminProductsPage.vue` — modifié
- `frontend/src/pages/OrderDetailPage.vue` — modifié
- `frontend/src/pages/OrdersPage.vue` — modifié
- `frontend/src/pages/ProductFilters.test.ts` — modifié
- `frontend/src/pages/ProductsPage.test.ts` — modifié
- `frontend/src/pages/ProductsPage.vue` — modifié
- `frontend/src/style.css` — modifié
- `frontend/src/types/api.ts` — modifié
- `frontend/src/utils/products.ts` — modifié
- `frontend/tsconfig.json` — modifié
- `frontend/visual-e2e/interaction.spec.ts` — nouveau
- `frontend/visual-e2e/screens.spec.ts` — nouveau
- `scripts/init_e2e_env.py` — modifié
- `src/stockpilot/application/dto/order_page.py` — nouveau
- `src/stockpilot/application/dto/product_page.py` — modifié
- `src/stockpilot/application/dto/stock_movement_page.py` — modifié
- `src/stockpilot/application/ports/order_repository.py` — modifié
- `src/stockpilot/application/ports/product_repository.py` — modifié
- `src/stockpilot/application/use_cases/adjust_product_stock.py` — modifié
- `src/stockpilot/application/use_cases/backoffice_orders.py` — modifié
- `src/stockpilot/application/use_cases/catalog_products.py` — modifié
- `src/stockpilot/application/use_cases/list_customer_orders.py` — modifié
- `src/stockpilot/infrastructure/persistence/mongodb/order_repository.py` — modifié
- `src/stockpilot/infrastructure/persistence/mongodb/pagination.py` — nouveau
- `src/stockpilot/infrastructure/persistence/mongodb/product_repository.py` — modifié
- `src/stockpilot/presentation/api/routes/backoffice_orders.py` — modifié
- `src/stockpilot/presentation/api/routes/backoffice_products.py` — modifié
- `src/stockpilot/presentation/api/routes/backoffice_stock.py` — modifié
- `src/stockpilot/presentation/api/routes/orders.py` — modifié
- `src/stockpilot/presentation/api/routes/products.py` — modifié
- `src/stockpilot/presentation/api/schemas/backoffice_order.py` — modifié
- `src/stockpilot/presentation/api/schemas/backoffice_stock_movement.py` — modifié
- `src/stockpilot/presentation/api/schemas/order.py` — modifié
- `src/stockpilot/presentation/api/schemas/product.py` — modifié
- `tests/test_list_customer_orders.py` — modifié
- `tests/test_pagination_totals.py` — nouveau

Total : 58 fichiers suivis modifiés et 16 nouveaux. Aucun fichier stagé.
