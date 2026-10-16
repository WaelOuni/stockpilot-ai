# V8 — conseil fondé sur le catalogue

V7 publiée sur `dev` à `75d94f1`. V8 est intégrée sur cette base pour publication
sur `dev`. Le commit parent est `25176ce` (merge GitHub de main vers dev), avec
pour parents `75d94f1` et `ea13755` et un arbre Git strictement identique à
`75d94f1` ; cet écart externe ne change pas le code validé. Ce merge est conservé
sans réécriture de l'historique.

## Parcours et sources

En production, PRODUCT_ADVICE utilise `CatalogueAdviceUseCase` via LangGraph.
Le classifieur LLM comprend la demande ; les demandes de conseil explicites
reconnues peuvent aussi être routées sans Ollama. Une demande de conseil
explicite ne devient pas ORDER à partir d'une réponse de modèle incorrecte.
Les parcours ORDER/STOCK_CHECK nommés, leurs transactions et idempotence
restent inchangés. La confirmation existante passe toujours avant classification.

Les critères acceptés proviennent du texte utilisateur, pas des prix, noms ou
budgets proposés par le LLM. Catégories : perceuses, visseuses, scies, ponceuses.
Plafond numérique positif (deux décimales maximum), strict pour « moins de »,
inclusif pour « budget/maximum ». Disponibilité demandée : disponible/en stock
ou indisponible/en rupture. Nombre : 1 à 5, trois par défaut. Une demande sans
critères, un budget implicite inconnu, plusieurs catégories ou plafonds,
un intervalle/minimum, une autre devise ou un format ambigu demande précision.
Une catégorie, un plafond ou une disponibilité suffit à lancer une recherche.

« Je cherche une perceuse à moins de 150 €. » filtre catégorie/prix.
« Quelles visseuses sont disponibles ? » filtre catégorie/stock.
« Propose-moi trois produits dans mon budget. » demande le plafond si inconnu ;
après un conseil dans le même contexte client/thread, il réutilise seulement
la demande utilisateur précédente, jamais le budget inventé par le classifieur.
Une précision contradictoire demande clarification plutôt que d'élargir le filtre.

Mongo filtre avant la limite et trie par prix, SKU, puis `_id`. Les SKU
sélectionnés sont relus exactement pour refuser les identités ambiguës et
revalider les contraintes. Une erreur catalogue ne provoque aucun repli vers
des références RAG/LLM inventées. Catalogue vide ou aucun candidat : réponse
explicite, aucune carte. Aucun index, migration, ingestion ou seed ajouté.
Les prix/stocks sont des instantanés, sans réservation ; ORDER les revérifie.
La nouvelle requête peut faire un COLLSCAN/SORT : pas de promesse de capacité.

## Explications et interface

Le produit ne contient que nom, SKU, catégorie, prix numérique et stock.
Aucune devise, puissance, tension, technologie, compatibilité matériau ou
qualité d'usage n'est déduite. Le message signale cette limite, même si
l'utilisateur mentionne EUR ou demande une perceuse pour un matériau donné.

Une seule génération LLM par réponse ordonne trois raisons construites à
partir de faits vérifiés. Le schéma n'autorise que les codes catégorie/prix/stock.
Les textes libres ou autres codes sont refusés ; une présentation déterministe
reste possible quand Ollama tombe en panne. Ce format volontairement borné
évite de rendre des caractéristiques inventées, contrairement à un prompt seul.
Le parcours V8 catalogue ne nécessite pas PostgreSQL/RAG ni embeddings ;
le runtime applicatif normal conserve son checkpointer existant.

Le chat réutilise ProductInfo, les cartes et RouterLink : nom/SKU/prix/stock,
disponibilité, explication et lien vers la fiche. Les données structurées sont
validées côté client ; HTML est échappé, les liens sont internes et SKU encodés.
Les messages conservent leur instantané ; un changement d'intent ne réutilise
pas des recommandations anciennes. Une confirmation n'affiche pas de carte conseil.

## Validation et reproduction sans seed

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
cd frontend
npm test
npm run build
npm run lint
```

Les intégrations réelles sont exclues par défaut. Les tests couvrent filtres
Mongo avant limite, plafond strict/inclusif, disponibilité, tie-break SKU,
absence/ambiguïté, catalogue indisponible, hallucinations, panne Ollama,
continuité avec preuves utilisateur, isolation client/thread et anciennes cartes.
Les suites ORDER/STOCK_CHECK existantes servent à la non-régression.

Le harness Playwright habituel réinitialise/seed les bases : ne pas l'utiliser
pour cette validation. Le harness V8 dédié utilise uniquement le catalogue
**déjà existant** de `stockpilot_e2e` ; absence de données = échec, jamais seed.

```sh
MONGO_DATABASE=stockpilot_e2e PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m scripts.verify_advice_browser
```

`E2E_MONGO_URI` peut sélectionner le serveur mais doit déclarer rs0 et ne peut
nommer une autre base. Le primaire réel et la base effective sont contrôlés.
L'application de test monte les vrais routers/use cases/adaptateurs catalogue
et le graphe LangGraph avec checkpoints **en mémoire**. Elle n'importe pas
`stockpilot.main` : aucun setup PostgreSQL, index startup ou migration. Ce n'est
pas une validation de la persistance PostgreSQL du runtime normal, déjà V7.
Les use cases de mutation sont interdits et un middleware refuse tous les POST
hors conseil explicite. JWT éphémère, environnement Vite isolé ; pas de traces,
screenshots ou vidéos. Les ports 8002/5174 doivent être libres ; seuls les
processus possédés sont arrêtés. Tous les documents/indexes E2E sont comparés
avant/après. Aucune connexion à PostgreSQL ou à `stockpilot` n'est ouverte.

Trois requêtes représentatives au maximum dans un run réussi : candidats,
clarification, absence. La formulation n'appelle le modèle qu'une fois pour
les candidats. Une panne du modèle est signalée séparément du succès du
catalogue/browser ; aucune campagne real AI eval n'est exécutée.

## Résultats V8 (11 octobre 2026)

- Contrôle de publication : 12 fichiers modifiés et 9 nouveaux, aucun fichier
  de configuration sensible ou artefact local inclus ; `git diff --check` réussi.
  Réexécution avant commit : 241 tests backend ciblés et 364 tests frontend
  réussis, build et lint réussis. Le navigateur réel n'est pas relancé lors de
  ce contrôle ; les résultats isolés ci-dessous proviennent de la validation V8.
- Backend complet : 1 406 réussis, 22 intégrations désélectionnées. Après la
  dernière neutralisation des métadonnées LLM, régression ciblée du graphe,
  contexte, ownership et anciens parcours : 241 tests réussis.
- Frontend : 364 tests / 20 fichiers, build vue-tsc/Vite et lint réussis.
- Navigateur dédié : un scénario complet réussi, trois demandes représentatives
  avec le vrai Ollama configuré via le helper local WSL ; explication structurée
  validée. Le même scénario a été recontrôlé après neutralisation des métadonnées
  LLM : deux runs réussis, soit six demandes représentatives au total. Aucun seed/reset/migration/ingestion ni campagne d'évaluation.
- Un diagnostic initial sur l'URL par défaut a montré Ollama inaccessible ; le
  premier conseil dans le navigateur a alors fonctionné en repli déterministe.
  Deux essais du harness ont ensuite exposé la reconnexion JWT manquante après
  navigation complète : correction du test, pas de la session client en mémoire.
- Comparaison avant/après de tous les documents/indexes E2E identique ; les ports
  possédés 8002/5174 ont été libérés. PostgreSQL n'a pas été ouvert par le harness.

Si Ollama est hébergé sur Windows/WSL, sa configuration privée existante peut
être chargée par `source scripts/dev-env.sh >/dev/null` avant le harness ; ne
jamais afficher/coller les valeurs privées. `MONGO_DATABASE=stockpilot_e2e`
reste explicitement requis et aucun service normal n'est lancé.

La grammaire de contraintes reste volontairement bornée et documentée. La
sélection numérique ne prouve pas une devise ou une aptitude technique ; les
changements de stock entre lecture et commande restent possibles. Le harness
browser est une validation conseil isolée, pas un reset/parcours ORDER live.
