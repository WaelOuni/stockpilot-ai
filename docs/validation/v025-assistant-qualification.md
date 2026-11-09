# Assistant v0.2.5 — qualification et corrections ciblées

La [deuxième passe](v025-assistant-second-pass.md) complète ce diagnostic initial :
navigation textuelle, comparaisons factuelles, contexte et preuves directes des
anciens SKIP. Les résultats historiques ci-dessous restent datés ; la matrice et
les artefacts CI liés à la HEAD finale de #84 font autorité pour la recette actuelle.

Travail du 9 novembre 2026, base GitHub main vérifiée à
`f8ab2f5db5e089b182235f64ac8d17586def1c1b`. Branche
`codex/v025-assistant-qualification`, worktree dédié. Les autres checkouts,
déploiements, secrets, volumes, migrations et modèles restent hors de ce lot.
Ce rapport décrit le code candidat, pas un déploiement de v0.2.5.

## Diagnostic et résultat

Les deux phrases utilisateur ont échoué avant correction :

| Phrase | Cause reproduite | Résultat corrigé |
|---|---|---|
| je voulais savoir le stock de produit qui bientot épuisé | Le détecteur lexical retourne false | LOW_STOCK, disponible <5, catalogue actif du tenant |
| je cherche une perceuse disponible avec un prix moins de 100 euros | `reference='perceuse disponible avec un prix'`, mode produit | Liste de perceuses, disponible >0, prix EUR ≤9999 centimes |

Six tests rouges initiaux prouvent aussi la dépendance indue au classifieur.
Les intentions explicites de découverte utilisent désormais le chemin
déterministe avant l’appel au modèle ; un ORDER_STATUS/ORDER hostile ne peut
plus remplacer cette intention. Les requêtes ORDER explicites conservent leur
chemin existant et leurs preuves. Aucun prompt, modèle, résolveur fuzzy partagé,
permission, migration ou protocole BFF n’a changé.

La grammaire B2B traite les reformulations bornées, zéro/exclusion des ruptures,
contraintes stock et budget combinées, demandes explicites du catalogue complet,
catégories et prix les moins chers. Les noms cités et SKU restent des identités
littérales. Les contraintes inconnues ne sont pas effacées pour élargir la liste.
Les seuils monétaires sont bornés ; une conversion incapable de préserver les
centimes est refusée au lieu d’arrondir silencieusement.

Les raffinements de stock et de catégorie conservent la requête dans le checkpoint
déjà privé de la conversation. La suppression du budget et le retour explicite
au catalogue complet sont distincts ; sans contexte, une anaphore/affinage demande
une clarification. Les cartes historiques restent des observations immuables.
Les pages courantes sont revalidées et ne promettent pas un snapshot sous écritures
concurrentes. Aucun tri ni pagination ne s’effectue sur une liste tronquée côté UI.

## Validation et limites

La [matrice des 301 demandes](v025-assistant-corpus.md) donne les décisions
individuelles, leurs dénominateurs, les familles et les limites. Les variantes,
séquences, tests de stockage et tests UX ont des compteurs séparés.
La [commande dédiée](../../evals/assistant_v025/README.md) exporte les critères
structurés et renvoie une erreur avec `--check` si la qualification est incomplète.

| Commande / preuve | Résultat local |
|---|---|
| Pytest complet de main avant modification | 3942 passed, 641 deselected, 370 subtests ; avertissement JWT synthétique préexistant |
| Six nouveaux tests de reproduction avant correction | 6 failed |
| Pytest complet du candidat | 5376 passed, 683 deselected, 370 subtests ; avertissement JWT synthétique préexistant |
| Suite ciblée, variantes, séquences et invariants | Succès ; 1200 variantes uniques, 100 séquences, 6 corruptions détectées |
| `scripts/run_evals.py --mode deterministic --suite enriched` | 122 cas, 0 échec ; qualité réelle d’Ollama non mesurée |
| Frontend test/typecheck/lint/build | Exécutés ; l’UX est livrée et testée dans sa PR indépendante |
| Intégrations locales Mongo/PG | Non exécutées : moteur Docker Desktop indisponible ; aucun démarrage de démo pour contourner ce blocage |
| Nouveaux tests Mongo/PG | 42 collectés ; exécution requise dans la CI isolée |
| CI obligatoire | Les cinq résultats sur le SHA exact sont attachés à la PR ; une exécution voisine ne vaut pas preuve |
| Migrations 0001–0006, prompts/modèles ORDER, `git diff --check` | Inchangés / contrôle sans erreur |
| Ollama réel et revue indépendante | Non exécutés ; aucune conclusion probabiliste ou second avis inventé |

La suite locale exclut les tests integration par configuration. Ces exclusions
ne sont pas des réussites. La CI exécute les services dédiés de compose.ci et leurs
gardes de cible. Le job e2e historique désactivé n’est jamais compté comme vert.

## Risques et décision

| Risque / limite | Traitement |
|---|---|
| Formulations dépassant la grammaire bornée | Clarification conservatrice ; écarts conservés dans la matrice |
| Navigation/pagination demandée par texte G08 | Fonctionnement des contrôles existants couvert séparément ; aucune promesse de navigation textuelle complète |
| Recommandations subjectives/comparaisons contextuelles avancées | Pas de notes, prévision ou qualité inventée ; qualification encore incomplète |
| RAG et résilience complète | Les garanties des stores et tests existants restent distinctes du grounding d’un modèle réel et du rejeu exact de chaque phrase |
| Révocation concurrente / transaction Mongo-PG | Garanties existantes conservées ; pas d’atomicité distribuée ajoutée |
| Charge du modèle | Les chemins déterministes évitent sa classification ; P50/P95 du harnais n’est pas une latence de déploiement |

**Fusion : GO limité aux corrections ciblées seulement après revue humaine et
les cinq checks exacts. NO-GO pour annoncer la qualification complète des 301
demandes tant que FAIL/SKIP subsistent.**

**Release/déploiement : NO-GO.** Aucune release, tag, fusion ou opération sur une
installation n’est réalisée ou autorisée par ces tests. Les besoins encore ouverts
sont explicitement visibles, sans réécrire les attentes du cahier de recette.
