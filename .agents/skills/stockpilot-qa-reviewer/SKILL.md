---
name: stockpilot-qa-reviewer
description: "Construire ou exécuter une validation StockPilot AI et revoir les preuves de régression avant PR : pytest, Vitest, Playwright, intégrations, ORDER/ADVICE et GO/NO-GO. Utiliser pour une recette ou revue demandée ; pas pour implémenter une fonctionnalité ou conclure sur Ollama à partir de mocks."
---

# QA et revue StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md) et [testing](../../../docs/testing.md).
Fixer SHA, diff, parcours, critères d'acceptation et environnement réellement testé.
Installer les dépendances propres au worktree ; vérifier l'origine du package
Python importé. Aucun test ne doit charger une configuration privée de démo.

## Workflow
1. Construire une matrice impact → risque → preuve. Prioriser les comportements
   observables et les invariants, pas les tests qui recopient l'implémentation.
   Pour un plan fictif, produire cette matrice sans changer l'application.
2. Exécuter les gates canoniques d'AGENTS. Lire [pyproject.toml](../../../pyproject.toml) :
   pytest exclut integration par défaut. Ne jamais compter les tests désélectionnés
   comme réussis. [CI](../../../.github/workflows/ci.yml) précise les gates distants.
3. Si persistance affectée, vérifier les services jetables avec
   [check_targets.py](../../../scripts/ci/check_targets.py) et les fixtures
   [integration_targets.py](../../../tests/integration_targets.py) avant la sélection
   integration documentée. Un nom de base de test ne garantit pas un serveur sûr.
4. Préserver les régressions ORDER : quantité lexicale/négative/fractionnelle,
   identité exacte, preuve/confirmation/désarmement, replay, atomicité, ownership
   et checkpoint. Pour ADVICE : faits vérifiés, ambiguïtés, bornes, contexte,
   classifieur hostile et absence de mutation. Ajouter pannes et issues inconnues
   si la modification peut toucher ces chemins.
5. Choisir Playwright selon [frontend README](../../../frontend/README.md) :
   harness Advice synthétique sans services métier, ou parcours B2B/LLM réel
   explicitement isolé. Les tests Vitest ne prouvent pas cookies, SSO ou rendu
   navigateur. Pour B2B, inclure accès direct, changement de compte et refus serveur.
6. Distinguer [eval déterministe](../../../docs/ai-evals.md) et mesure réelle :
   hashes/dénominateurs/modèles comparables, aucune baseline réécrite pour rendre vert.
7. Si une revue indépendante est disponible et autorisée, fournir au reviewer le
   diff et les contrats bruts sans lui imposer la conclusion. Sinon signaler
   l'absence de revue indépendante ; ne pas inventer un second avis.

## Sécurité et preuves
Ne pas lancer un ancien script E2E sur un service trouvé ouvert ni supprimer un
test au motif de son nom historique. Un échec préexistant doit être reproduit et
attribué, pas reclassé arbitrairement en hors périmètre. Garder logs bruts privés,
résultats assainis et preuves liées au SHA effectivement testé.

## Livrable et arrêt
Livrer tableau commandes/résultats (pass, fail, skip, non exécuté), couverture des
critères, findings prioritaires, limites et verdict GO/NO-GO pour la portée demandée.
Un gate requis manquant ou une fuite/transaction non sûre interdit le GO correspondant.
Suspendre les tests dont la cible est incertaine ; continuer les contrôles offline.
La revue d'une PR n'autorise ni corrections applicatives ni merge implicites.

## Exemple
« Prépare puis exécute la recette d'une évolution B2B, avec non-régression
ORDER/ADVICE et un verdict fondé sur les preuves du dernier commit. »
