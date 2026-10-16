# Correction de « Réponse de mouvement invalide »

READY FOR REVIEW.

## Cause et moment

La chaîne exacte provient de `validateStockMovement` dans `frontend/src/api/admin.ts`. Au montage de `AdminProductStockPage`, le watcher appelle `refresh`, puis `loadPage` charge en parallèle le produit et son historique. `listAdminStockMovements` valide chaque ligne reçue ; le même validateur exigeait un operation_id UUID v4 aussi bien pour l’historique que pour un résultat d’ajustement. Le rejet intervenait donc dès le GET initial, avant toute soumission, avec champs vides. Le code HTTP du serveur est 200 ; c’est une erreur de contrat frontend, pas une erreur de transport.

Le générateur DEMO utilise uuid5(NAMESPACE_URL, ...) pour produire des identifiants déterministes. Le schéma StockMovementResponse expose operation_id comme chaîne, sans imposer v4 à l’historique. L’adaptateur Mongo restitue cet identifiant sans conversion et normalise les dates UTC. La restriction v4 appartient aux nouvelles opérations et à leur endpoint de réconciliation.

## Vérification DEMO en lecture seule

Vrais routeurs FastAPI et adaptateurs Mongo sur stockpilot_demo, avec une session admin temporaire dans une application TestClient dédiée en lecture seule, sans lifespan de main. Aucun cookie, token, mot de passe ou URI journalisé. Ce diagnostic ne réutilise pas une session du serveur DEMO de l’utilisateur.

| Appel | Résultat observé | Contrat |
|---|---|---|
| GET /backoffice/products/DEMO-DRILL-0001/stock-movements?limit=20&offset=0&include_total=true | 200, six mouvements, total 6, has_more false | Historique : UUID v4 ou UUID v5 déterministe |
| GET /backoffice/stock-adjustments/{UUID-v5-historique} | 422 | Attendu : cet endpoint est réservé aux opérations v4 |
| GET /backoffice/stock-adjustments/{UUID-v4-de-test-existant} | 200 | Résultat d’opération v4 |
| POST /backoffice/products/{sku}/stock-adjustments | Aucun appel sur DEMO ou stockpilot | Requête v4, contrôles métier/idempotence inchangés ; tests sur doubles en mémoire |

Exemple réellement reçu :

```json
{
  "movement_id": "9671c4b5-c6da-5c08-b466-7e32ca3b0cf1",
  "operation_id": "33a09f28-0d1c-5304-85b8-2998a6c34f57",
  "product_sku": "DEMO-DRILL-0001",
  "delta": 1,
  "stock_before": 91,
  "stock_after": 92,
  "reason": "Synthetic demo inventory adjustment",
  "occurred_at": "2026-11-06T13:00:00Z",
  "source": "BACKOFFICE"
}
```

Pagination reçue : limit 20, offset 0, count 6, total 6, has_more false. Les champs du mouvement sont des chaînes ou entiers conformes ; aucun champ null dans les réponses observées. Les dates ISO UTC, y compris celles futures du dataset synthétique, sont valides. Le SKU correspond au produit demandé et l’arithmétique est cohérente.

Inventaire des 3 039 documents : 3 000 UUID v5 de référence et 39 UUID v4 de fixtures retenues. Aucun identifiant malformé ou défaut de source/SKU/arithmetic détecté dans cet audit. L’intégrité de la référence et des fixtures est identique avant/après ; audit Mongo : zéro commande persistante d’écriture. Aucune réparation de documents nécessaire.

## Correction minimale

Dans admin.ts, un motif UUID canonique v4/v5 est utilisé uniquement lors de la validation de l’historique. Les appels POST et GET par operation_id conservent le motif v4 strict. Tous les autres contrôles de ligne et pagination sont conservés : SKU, delta entier borné non nul, stock non négatif/cohérent, motif non vide normalisé, date interprétable et source BACKOFFICE.

Aucun changement de route, schéma, adaptateur, transaction, règle d’idempotence ou mécanisme de réconciliation. Aucun filtrage silencieux de ligne invalide.

## Régressions et validations

- Fixture frontend : réponse HTTP DEMO exacte de six mouvements, sauvegardée sans métadonnées internes ni secrets dans frontend/test-fixtures/demo-stock-history.json.
- API : historique v5 exact, historique vide, pagination avec total, données incompatibles (UUID, casse, source, SKU, delta, arithmetic, motif null, date null/invalide), maintien de v4 strict pour POST et réconciliation.
- Page : chargement avec formulaire vide, aucun POST ni GET de réconciliation déclenché ; réconciliation d’un résultat v4 ambigu malgré l’historique v5 existant. Les tests de réconciliation antérieurs restent présents.
- Backend : test des vrais routeurs/adaptateur sur doubles en mémoire, lecture d’un mouvement DEMO v5, refus 422 du v5 pour nouvelle opération/réconciliation, documents du double inchangés.
- Vitest complet : **387 tests réussis**, 22 fichiers.
- Typecheck, build Vite et lint : réussis.
- Backend ciblé : **104 tests réussis** (stock_adjustments, demo_dataset, demo_persistence, demo_read_verification, pagination_totals).
- Playwright : **un parcours réussi**, captures à 1440/768/390, pas de débordement, historique affiché sans erreur, formulaire vide et zéro POST métier. Authentification réelle E2E, puis replay de la réponse DEMO exacte ; il ne s’agit pas d’une navigation sur le serveur DEMO de l’utilisateur.
- git diff --check et whitespace des nouveaux fichiers : réussis.

Captures : artifacts/ui-ux/stock-contract/history-1440.png, history-768.png et history-390.png.

## Fichiers de cette correction et Git

- frontend/src/api/admin.ts
- frontend/src/api/adminStock.test.ts
- frontend/src/pages/AdminProductStockPage.test.ts
- frontend/test-fixtures/demo-stock-history.json (nouveau)
- frontend/visual-e2e/stock-history-contract.spec.ts (nouveau)
- tests/test_stock_adjustments.py
- docs/archive/stock-history-contract-fix.md (nouveau)

Branche feat/frontend-ux-overhaul ; HEAD inchangé 1d601c90c22a177a02b46cc32210b5d304655b77. Modifications V9 préexistantes préservées, index vide ; aucun commit/push/reset. Aucune écriture sur stockpilot, aucune modification de référence DEMO, aucun seed ni migration. Le navigateur utilise le lancement E2E sans reset ; aucun POST d’ajustement réel n’a été envoyé.
