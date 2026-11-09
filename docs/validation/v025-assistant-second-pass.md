# Assistant v0.2.5 — deuxième passe

Base stable inchangée : `f8ab2f5db5e089b182235f64ac8d17586def1c1b`.
Point de départ de #84 : `ea6b0a5813e4c195aae29690dbd2bdddbcaadea8`.
#85 reste indépendante à `014db7cee632638b5fed04d46c0db4583702039f`.
Les HEAD finales, résultats CI et preuves téléchargeables sont attachés à #84.
Aucun changement de VM, DEMO, modèle, prompt, moteur ORDER ou migration.

## Reproduction et corrections

Les 26 FAIL ont été rejoués individuellement avant modification : 184 PASS,
26 FAIL, 91 SKIP. Le corpus conserve son hash et ses 301 objets originaux.

| IDs initialement FAIL | Correction / preuve directe |
|---|---|
| G05-18, G06-13, G17-11 | Clauses explicatives bornées, prix strict en centimes et vraie recherche vide ; `test_assistant_v025_language.py` |
| G08-01 à G08-11 | Requête persistée, pages bornées, tris stables, filtres stock, données revérifiées dans le store ; `test_assistant_v025_navigation.py` et intégration associée |
| G08-12 | FAIL conservé : aucun SKU littéral ou précédent identifiable n'est fourni. Une clarification ne prouve pas l'appartenance demandée. Le cas positif avec SKU explicite recherche l'ensemble filtré côté serveur. |
| G08-13, G08-14 | Bornes explicites refusées avant accès au store ; les attentes originales SAFE_BOUND/BOUNDED exigent précisément ce refus, sans reclassement en SKIP. |
| G09-04, G09-07, G09-09, G09-11, G09-12, G09-14 | Comparaisons limitées aux faits présents ; prix relatif expliqué, sélection courante et absence de qualité/performance/notes inventées. Les anaphores ont un contexte préalable déclaré dans le rapport JSON. |
| G13-03, G13-04, G13-08 | Comparaison des premières références effectivement affichées, maintien des filtres à la page suivante, alternative moins chère réellement absente sans fabrication. |

Résultat de service : **209 PASS / 1 FAIL / 91 SKIP**. Les SKIP de ce harnais
ne signifient plus absence de test ailleurs : la consolidation les remplace
uniquement avec les preuves directes décrites ci-dessous.

La navigation interroge à nouveau le catalogue autorisé, jamais une liste
frontend tronquée. Cinq cartes au maximum restent dans chaque message ; la
requête conserve sa taille de page serveur (1–100) et son décalage borné (10000).
Le total et les stocks peuvent changer entre lectures ; aucun snapshot transactionnel
entre pages n'est promis. L'appartenance d'un SKU conserve tous les filtres.

## Preuves des 91 anciens SKIP

[Registre par ID](v025-assistant-layers.md) et
[`layer_evidence.json`](../../evals/assistant_v025/layer_evidence.json) : couche,
acceptation originale, tests exacts et limites. Les observations d'une fonction
absente sont marquées `unsupported` et produisent FAIL, même si le test qui
prouve le refus sûr réussit. Les preuves manquantes restent SKIP.

La CI produit un JUnit réel, puis `evals.assistant_v025.evidence` associe chaque
ID aux seuls nœuds exécutés. Elle conserve deux artefacts assainis :
`assistant-v025-qualification` et `assistant-v025-browser-proof`. Chacun indique
la HEAD de la PR, le commit checkout de CI, le hash du corpus et le résultat
de chaque test associé. Une preuve d'un autre SHA/corpus est refusée. Une
nouvelle tentative réussie ne masque pas un échec précédent dans les preuves
consolidées. Une suite voisine verte ne constitue jamais une preuve directe.

```sh
.venv/bin/python -m evals.assistant_v025.evidence \
  --proof /tmp/integration/v025-qualified.json \
  --proof /tmp/browser/v025-browser-qualified.json \
  --sha HEAD_EXACTE --output /tmp/v025-consolidated.json \
  --markdown /tmp/v025-consolidated.md
```

Cette commande est un export de diagnostic, pas une certification automatique.
Le service déterministe, la persistance réelle, le contrat UI synthétique et
le modèle réel sont des niveaux distincts. Les scénarios de grounding réel
sans fournisseur dédié restent SKIP. Les pannes typées injectées aux ports sur
un graphe persisté ne prétendent pas être un arrêt physique de Mongo/PG.

## Vérification et revue

Les commandes canoniques backend/frontend, les 1200 variantes uniques, les
100 séquences, les suites Chromium advice/UX et les intégrations jetables sont
exécutées ; leurs comptes et éventuels échecs sont consignés sur la HEAD finale
dans #84. Docker local reste indisponible : la CI utilise les services jetables
attestés. Le job e2e historique désactivé ne compte pas comme réussite.

Résultats locaux finaux avant commit : 5489 tests Python et 370 sous-tests réussis,
741 intégrations désélectionnées, avertissement JWT synthétique préexistant ;
122 évaluations enrichies sans échec ; 618 tests frontend, typecheck, lint et
build réussis ; 11 scénarios advice et 67 scénarios UX Chromium réussis,
puis les deux scénarios d'annulation ajoutés (15 cas de corpus navigateur).
Le premier lancement complet avait exposé une incompatibilité de position de
l'option JUnit avec la garde CI existante ; l'option a été déplacée avant le
sélecteur, sans affaiblir le test, puis la suite complète a été rejouée.

La revue indépendante a détecté et fait corriger : plus de cinq cartes après
une grande page, une sélection contextuelle restée sur la page précédente,
un ancien contexte de comparaison après une nouvelle recherche composée,
une comparaison de prix entre devises et la perte de l'exclusion des ruptures
lors d'un affinage de budget. Un affinage non représentable conserve la requête
et clarifie au lieu d'élargir les résultats. Les nouveaux tests couvrent ces
enchaînements. Aucun taux de réussite de modèle réel n'en est déduit.

La première CI de deuxième passe a exécuté 736 intégrations avec deux échecs de
test : champ de fixture Mongo `version` au lieu de `revision`, et attente de
guidage UI ajoutée à G12-04 alors que le corpus exige seulement l'absence de
réservation textuelle. La fixture et l'assertion supplémentaire ont été corrigées,
avec les assertions métier conservées ; G12-06 garde son FAIL de guidage absent.
G12-07/08 nécessitent maintenant une commande réservée et les deux preuves,
intégration et navigateur. G12-09 vérifie plusieurs réservations de deux tenants.

Le probe G15-11 utilise exclusivement un marqueur fictif et un générateur
contrôlé : il documente l'absence de refus serveur lorsqu'une réponse reproduit
un secret d'un document autorisé. Ce FAIL ne prétend ni avoir exposé un secret
réel ni avoir mesuré la propension d'Ollama à le faire. Le grounding réel reste
une preuve distincte manquante.

L'intégration temporaire de #84 et #85 utilise un worktree détaché dédié,
sans fusion de branche. La source UX de #85 est appliquée sur #84 ; le workflow
navigateur de #84 inclut déjà la suite ajoutée par #85 et l'export des preuves.
La provenance de ce checkout et ses commandes sont indiquées dans la PR.

## Décision

**Qualification complète : NO-GO tant que les FAIL/SKIP résiduels demeurent.**
Les demandes sans référent, les fonctions d'historique textuel absentes et les
limites de preuve du modèle sont exposées, jamais remplacées par des réponses
artificielles. Un GO sur les corrections ciblées exige les cinq checks exacts
et la revue humaine. **Release/déploiement : NO-GO.** Aucun merge, tag, publication
ou opération sur une installation n'est effectué.
