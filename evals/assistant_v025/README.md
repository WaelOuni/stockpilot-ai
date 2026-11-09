# Qualification assistant v0.2.5

`corpus.json` conserve les **301** objets ID/message/expected du cahier de
recette fourni le 9 novembre 2026. Les formulations et attentes ne sont pas
réécrites pour rendre le résultat vert. Les 24 exigences UX sont livrées dans
la PR indépendante `codex/v025-assistant-response-ux`.

La campagne dédiée ne modifie ni les goldens ni la baseline de la suite
historique enrichie. Depuis la racine :

```sh
.venv/bin/python -m evals.assistant_v025.campaign \
  --output /tmp/stockpilot-v025-corpus.json \
  --markdown docs/validation/v025-assistant-corpus.md --check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_assistant_v025_campaign.py tests/test_assistant_v025_routing.py
```

`--check` sort avec le code 1 tant qu’un seul FAIL **ou SKIP** subsiste.
Sans cette option, la commande est un export de diagnostic, pas une gate.
Le JSON contient input original, langue, rôle, tenant, conversation préalable,
dataset, oracle structuré, droits, provenance, mutation autorisée, effets et
HTTP (N/A pour un test de service), décision et observation.

## Preuves séparées

- Adaptateur déterministe sans réseau : 292 produits synthétiques org-a/org-b,
  stocks 0/1/4/5/14/15/50 disponibles avec réservations, prix 99,99/100/100,01 EUR,
  autre devise, produit inactif, homonymes, modèles 18V/20V/Max 18 et descriptions
  contenant une instruction inerte. Les SKU DEMO sont **des copies synthétiques**.
- 1 200 variantes distinctes, graine 2505 ; 100 séquences distinctes avec quatre
  rôles et deux tenants déclarés. Les oracles calculent les ensembles attendus
  depuis les faits de fixture, sans réutiliser le parseur applicatif.
- Relations d’ensembles, pagination >100 sans doublons à stock inchangé,
  observation courante différente de l’historique après changement de stock.
- Six corruptions de sortie injectées sont détectées : tenant, prix, disponible,
  nom, capacité de confirmation et appel au port de mutation. Ce n’est pas une
  mesure générale de couverture par mutation de code.
- `tests/test_assistant_v025_integration.py` : 42 tests supplémentaires utilisant
  exclusivement les fixtures Mongo/PG jetables attestées. Les demandes G10 et
  G11 passent par le graphe et le vrai moteur ORDER ; préparation, preuve,
  identité, quantité, replay et absence de réservation sont contrôlés. Les
  rôles sont réellement provisionnés/revalidés dans cette couche.

Le double déterministe ne prouve ni l’authentification réelle, ni une transaction,
ni le grounding d’Ollama. Les scénarios d’autres couches restent SKIP dans
l’export local même si des régressions voisines existent. La CI et les tests
correspondants sont consignés séparément, sans transformer ces SKIP en PASS.

Les familles de navigation textuelle G08, certaines recommandations sans critère
objectif, explications de comparaison et formulations à clauses supplémentaires
restent à qualifier. Une clarification conservatrice n’est pas présentée comme
une capacité de navigation ou de recherche satisfaite.

La [deuxième passe](../../docs/validation/v025-assistant-second-pass.md) complète
ces limites initiales : 209 PASS, 1 FAIL et 91 SKIP dans le harnais de service.
`layer_evidence.json` relie chacun des 91 anciens SKIP à des tests directs ou à
une limite. `python -m evals.assistant_v025.evidence` consolide leurs JUnit et
artefacts uniquement à SHA/hash du corpus identiques ; les observations de
fonctionnalités absentes restent FAIL même si leurs tests de sûreté passent.

Voir la [matrice complète](../../docs/validation/v025-assistant-corpus.md) et le
[rapport](../../docs/validation/v025-assistant-qualification.md).

La [passe finale](../../docs/validation/v025-assistant-final-pass.md) reprend les
11 FAIL et 11 SKIP de la HEAD `c77e910`. Le résultat consolidé final, ses preuves
et la CI exacte sont liés dans #84. Les chiffres des rapports précédents sont
historiques ; le tableau de service seul ne qualifie ni ORDER ni Ollama.

La [correction de citation](../../docs/validation/v025-rag-citation-fix.md) lie
G15-06 aux sources structurées du tour précédent, revalidées dans les stores.
G08-12 conserve son FAIL original ; une dérogation de recette est proposée
explicitement à décision humaine, sans modification de l'oracle ni du score.

Pour une instance Ollama isolée déjà créée (port non standard, corpus synthétique
uniquement), depuis un checkout propre :

```sh
.venv/bin/python -m evals.assistant_v025.real_rag \
  --url http://127.0.0.1:11587 --isolated-synthetic-only \
  --output /tmp/v025-real-rag.json
.venv/bin/python -m evals.assistant_v025.evidence \
  --sha "$(git rev-parse HEAD)" \
  --proof /tmp/v025-qualified.json --proof /tmp/v025-browser-qualified.json \
  --model-proof /tmp/v025-real-rag.json \
  --output /tmp/v025-final.json --markdown /tmp/v025-final.md
```

`--windows-curl` utilise le transport loopback Windows depuis WSL lorsque le
serveur isolé appartient à Windows. Le harnais ne crée aucun serveur et ne lit
aucun environnement applicatif. Les artefacts restent hors du dépôt.
