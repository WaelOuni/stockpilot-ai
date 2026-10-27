---
name: stockpilot-frontend-ux
description: "Améliorer les parcours Vue 3/TypeScript de StockPilot AI : composants, formulaires, accessibilité, responsive, français/anglais et thèmes. Utiliser pour un problème UX ou UI concret ; pas pour changer les permissions serveur, la transaction ORDER ou définir l'offre commerciale."
---

# Frontend et UX StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md). Identifier écran, utilisateur, état d'erreur
et parcours : [B2B](../../../frontend/src/b2b/) ou interface historique.
Relire [guide utilisateur B2B](../../../docs/b2b-user-guide.md) et
[expérience produit](../../../docs/b2b-product-experience.md) selon la demande.
Les permissions visibles ne constituent jamais un contrôle d'accès serveur.

## Workflow
1. Reproduire la tâche et définir un résultat observable : navigation, saisie,
   compréhension d'un statut ou récupération d'erreur. Conserver le composant et
   les conventions présents avant de proposer un nouveau système graphique.
2. Réutiliser [composants](../../../frontend/src/components/),
   [tokens globaux](../../../frontend/src/style.css) et
   [styles B2B](../../../frontend/src/b2b/experience.css).
   Tester clavier, focus initial/retour de dialogue, Escape, labels,
   annonces d'erreur, contraste, petit écran et zoom. Une capture ne prouve pas
   à elle seule l'accessibilité.
3. Pour FR/EN et clair/sombre, utiliser [preferences.ts](../../../frontend/src/b2b/preferences.ts)
   et [translations.ts](../../../frontend/src/b2b/translations.ts).
   Traduire les textes d'interface, pas les descriptions métier ou conversations.
   Vérifier les deux langues et thèmes, les dates et prix exacts du contrat actif.
4. Préserver [client BFF](../../../frontend/src/b2b/client.ts),
   [contrats](../../../frontend/src/b2b/contracts.ts) et
   [session](../../../frontend/src/b2b/session.ts) : cookie/CSRF, réponse périmée,
   expiration, changement de compte et invalidation inter-onglets.
   Aucun token OIDC ou donnée métier ajouté au localStorage ; préférences seules.
5. Dans formulaires/imports, garder aperçu puis confirmation, invalider l'aperçu
   après changement, empêcher double soumission et replay automatique.
   Une attente annulée côté navigateur n'annule pas la mutation serveur.
   Montrer l'état incertain et la réconciliation existante, sans inventer un succès.
   Ne pas rendre du texte fournisseur avec v-html.

## Tests et acceptation
Exécuter Vitest, typecheck, lint et build selon AGENTS ; étendre les tests
du composant ou [product-experience.test.ts](../../../frontend/src/b2b/product-experience.test.ts)
pour le comportement modifié, y compris l'erreur.
Consulter [frontend README](../../../frontend/README.md) et
[testing](../../../docs/testing.md) avant Playwright.
Le harness Advice synthétique couvre le parcours historique, pas une session
B2B Keycloak réelle. Pour une validation B2B réelle, utiliser le harnais documenté
et ses ports isolés ; ne pas pointer un test sur une démo existante.
Pour un plan fictif, fournir seulement la recette prévue.

## Livrable et arrêt
Fournir changement de parcours, fichiers, preuves UI accessibles et limites
(browser/viewport/langue/thème réellement vérifiés).
Suspendre la partie qui nécessite un contrat backend absent ou un changement
de sécurité ; proposer le besoin précis à Backend/Security sans l'implémenter
implicitement. Ne pas élargir une amélioration locale en refonte.

## Exemple
« Rends le dialogue de comparaison B2B utilisable au clavier et sur mobile,
en français/anglais et dans les deux thèmes. »
