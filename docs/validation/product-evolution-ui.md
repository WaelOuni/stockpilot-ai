# Product evolution — UI/UX (PR B)

Base examinée : `origin/main` fd7e212, 17 novembre 2026. Worktree dédié,
branche `codex/evolution-ui-ux`. La branche `dev` et les déploiements DEMO ne
participent pas à cette modification. Aucun changement backend ou ORDER.

## Audit et choix

| Surface | Existant conservé | Amélioration / limite |
| --- | --- | --- |
| Accueil / organisation / connexion | Session BFF, sélection explicite, préférences thème/langue | Les changements d'authentification sont isolés dans la PR A |
| Catalogue / produits | Filtres serveur, catégories, pagination, montants exacts, dialogue produit | Tokens, champs, tableaux et états communs harmonisés |
| Stocks | Quantité physique, réservée et disponible, permissions serveur | Trois indicateurs distincts ; seuil faible explicite et indicatif, aucune prévision |
| Commandes / historique | Pagination, archivage personnel, confirmation et idempotence existants | Actions SVG locales, tooltip clavier, labels mobile, lecture en cartes mobile |
| Détail commande | `DetailsPanel` natif avec focus trap, ESC et restitution du focus | Hiérarchie, lignes, montants par devise, réservation, événements réels, chargement immédiat, erreur/réessai |
| Assistant / conversations | Résultats structurés, citations, états pending/completed/failed et historique déjà présents | Lisibilité via styles partagés ; aucune modification du modèle ou des commandes |
| Administration / aide / contact | Menus par permission, confirmations, notifications et formulaires existants | Contrôles partagés de 44 px, focus visible, modes clair/sombre, FR/EN |
| Modales / notifications | Dialogue natif, toast et confirmations éprouvés | En-tête du détail fixe, régions défilantes identifiées, animations réduites respectées |

Pas de seconde bibliothèque de composants. `AppIcon` est étendu ; `RowAction`
et `OrderFacts` factorisent les deux besoins locaux. Les tokens s'appuient sur
les thèmes existants. Le tableau détaillé reste défilable sur petit écran avec
une indication explicite ; la page elle-même ne déborde pas.

Les montants des lignes sont `quantité × prix unitaire` en BigInt, additionnés
séparément par devise. Aucune conversion de devise. Il s'agit des montants de
lignes disponibles, pas d'un total facturé incluant taxes, livraison ou remises.
Les noms de produits et la dernière mise à jour ne sont pas conservés dans le
contrat historique : l'interface le dit. La timeline utilise uniquement les
dates created/reserved/cancelled présentes. Elle ne reconstitue pas un journal
complet absent. Archiver reste une préférence personnelle, sans annulation.

## Preuves et commandes exécutées

- RED : le nouveau scénario français/mobile sur l'application main non modifiée
  échoue sur l'absence des montants structurés. Log `/tmp/stockpilot-ui-before.log`.
  Captures locales `tmp/ui-baseline/before-orders-fr-light-390.png` et
  `tmp/ui-baseline/before-order-detail-fr-light-390.png`.
- GREEN : `npx playwright test --config playwright.b2b-ux.config.ts` : **94 passed**.
  Huit combinaisons FR/EN, clair/sombre, 390/1280 px pour le nouveau parcours,
  plus erreur/réessai et rôle en lecture seule. Focus, ESC, trap, retour au
  déclencheur et absence de mutation au clic annuler vérifiés.
- Dernière retouche du texte d'aide : `npx playwright test --config
  playwright.b2b-ux.config.ts ux-e2e/order-design.spec.ts` : **10 passed**.
- `npm test` : **645 passed** ; `npm run typecheck`, `npm run lint`,
  `npm run build` réussis. Deux avertissements de mise en forme apparus sur la
  dernière aide mobile ont été corrigés puis lint relancé sans avertissement.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` :
  **5925 passed, 824 deselected**, 370 subtests ; avertissement HMAC court dans
  la fixture historique `test_demo_launcher`.
- `.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched` :
  **122/122**. Cela ne mesure pas la qualité d'Ollama réel.
- Intégration MongoDB/PostgreSQL : **823 passed, 5856 deselected**, 665,98 s.
  Lanceur local `tmp/run_integration.py`, projet jetable
  `stockpilot-evolution-ci-20261117`, tmpfs, ports loopback, gardes de cible
  vérifiées. PostgreSQL utilise 59928 car 55432 est réservé par l'hôte Windows ;
  aucun garde du dépôt n'a été affaibli.
- `B2B_TEST_PORT_SET=isolated ... scripts/tests/validate_bff_keycloak.py --frontend` :
  **32 contrôles réels réussis**, Keycloak 26.8.1, HTTPS Chromium, transactions
  Mongo, annulation/réservation/idempotence/CSRF/isolation/expiration/logout.
  Rapport `tmp/v1372-qq80s0au/report.json` : passed/cleanup/protected_containers_unchanged/
  volumes_unchanged tous true.
- `git diff --check` requis avant le commit.

Les captures GREEN sont produites dans `frontend/test-results/after-orders-*`
et `after-order-detail-*`, vérifiées visuellement en français/mobile/clair et
anglais/desktop/sombre. Les captures/logs restent locaux, hors git.

Le harness navigateur attend désormais exactement la préférence publique
`assistantSound:false`, déjà écrite par main. Le même échec a été reproduit sur
l'application main non modifiée ; l'interdiction de stockage de tokens/données
métier reste vérifiée. Aucun oracle de transaction n'est modifié.

## Portée de la décision

GO pour revue en Draft après CI ; NO-GO fusion/déploiement sans revue requise.
Revue indépendante non encore réalisée. Les tests ne valent pas audit WCAG
exhaustif ni recette utilisateurs. Aucune migration, suppression de données,
modification des droits serveur, publication ou mutation de la VM v0.2.8.
