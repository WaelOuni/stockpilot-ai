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

Voir la [matrice complète](../../docs/validation/v025-assistant-corpus.md) et le
[rapport](../../docs/validation/v025-assistant-qualification.md).
