# Catalogue B2B : recherche persistée et découverte

Base vérifiée le 1 novembre 2026 : `main` distant et version annoncée déployée
à `2df542f756a6eece4ca917024cc8fff15edd39d8` (v0.2.1). Branche
`fix/b2b-assistant-catalogue-discovery`, créée dans le checkout existant propre,
sur demande explicite ; aucun clone/worktree, merge, tag ou déploiement.

## Cause et correction

Les quatre familles de la grammaire historique (`DRILL`, etc.) étaient directement
recherchées dans le champ B2B libre `category`. Des documents `perceuses` ne pouvaient
pas correspondre. La reproduction initiale sur Mongo jetable donne **11 échecs sur
12** : huit recherches positives et trois demandes de stock faible. Le vrai résultat
vide sous 150 EUR était déjà correct.

`tenant_catalogue_discovery.py` associe les familles lexicales aux **valeurs présentes
dans l'organisation**, lues par agrégation Mongo distincte. Les variantes françaises,
accents et anciennes catégories restent compatibles, sans écrire/reclasser un produit.
Les autres catégories réellement présentes peuvent être demandées littéralement avec
une formulation de recherche (ex. « je cherche Éclairage »). Un suffixe de modèle
inconnu reste une référence à résoudre ; il n'est pas effacé comme un critère générique.
Les noms et SKU ciblés passent toujours par le résolveur existant et une relecture exacte.

Les critères historiques restent lisibles dans les checkpoints. Un champ facultatif
`catalogue_categories` contient uniquement les identités canoniques sélectionnées ;
il est réconcilié avec le catalogue courant avant réutilisation. Le nombre demandé
reste un nombre de résultats (1–5), jamais une quantité ORDER. Un article « une »
conserve le contrat historique (3 résultats par défaut, nombre précédent en suivi).
Les plafonds en EUR deviennent des bornes entières en centimes : `< 150` donne
`maximum_price_minor=14999`, `maximum 150` donne `15000`. Aucun prix produit ne passe
par un flottant. Le texte compte les résultats réels et décrit les critères ; pour
un ensemble vide, des lectures bornées vérifient si le prix ou la disponibilité
exclut les produits qui satisfont les autres critères.

`LOW_STOCK` est une lecture dédiée, indépendante du classement proposé par le modèle.
Disponible = physique − réservé ; `< 5`, `≤ 5`, rupture = 0. Le seuil implicite est
explicitement annoncé (`< 5`). Le texte contient total, critère, instant d'observation,
caractère partiel et absence de réservation. Cinq cartes au plus montrent nom/SKU,
prix, physique/réservé/disponible. Produits actifs, tri disponible puis SKU. Catégorie
inconnue ou critère non reconnu : clarification, jamais élargissement silencieux.

`search_stock` produit total et cartes depuis la même agrégation Mongo. Le filtre
`organization_id` précède calcul, tri et facet ; `catalogue:read` et `stock:read` sont
revalidés avant/après lecture. Aucun write catalogue, stock, commande ou opération
n'est ajouté à ces lectures. Les mutations ORDER et leurs prompts/modèles sont inchangés.
Une conversation et ses checkpoints continuent naturellement d'être persistés.

## API et interface

- `GET /b2b/api/products/categories` : catégories actives canoniques de l'organisation,
  bornées à 1000 (dépassement refusé, jamais de troncature silencieuse).
- `GET /b2b/api/products/suggestions?prefix=BD&limit=10` : préfixe SKU littéral,
  sensible à la casse, 2–128 caractères ; 20 suggestions au plus. Requête ancrée,
  index existant `b2b_org_status_sku`, délai Mongo 2 s, organisation avant recherche.
  Aucune recherche floue, aucun transfert de catalogue complet, aucune migration/index
  automatique. La sélection est ensuite vérifiée par le endpoint exact existant.
- Les routes conservent la frontière BFF (session serveur, permissions, preuve de
  session des lectures Vue). La compatibilité GET historique sans en-tête CSRF reste
  inchangée ; un en-tête fourni incorrect est refusé. Les mutations exigent toujours
  Origin et preuve CSRF.
- Catalogue : 12 produits par page serveur, total/pages/plage, pages bornées et ellipses,
  première/dernière, `aria-current`, bornes et limite offset 10000, retour du focus en
  tête des résultats. Aucun chargement global des 1250 produits.
- Combobox catégorie libre, suggestions sans sensibilité aux accents/casse, valeurs
  canoniques conservées ; « Toutes les catégories » et effacement. Combobox SKU avec
  nom/catégorie, debounce 250 ms et annulation/version des réponses.
- Modifier un filtre retire immédiatement l'ancienne liste et remet l'offset à zéro.
  Une réponse ancienne ne peut réinstaller ses produits ou stocks. Le SKU exact a
  priorité, explicitée dans l'interface ; les autres filtres sont alors désactivés.
- Badge de rupture et absence de lien « Préparer » si le stock observé est nul.
- Menu : chevron SVG, rotation selon ouverture, focus/hover et ergonomie mobile ;
  identité/organisation/rôle, langue, thème, changement de compte, déconnexion.
  Escape, clic extérieur et navigation native au clavier conservés.

## Données DEMO : préparation, pas exécution sur la VM

`scripts/b2b_demo_stock_examples.py` génère des CSV CREATE_ONLY reproductibles par
lots de 100 pour une **nouvelle installation** : 1250 Northline (25 zéros + 20 faibles)
et 250 AtelierPro (10 zéros + 10 faibles). Les références `*-NEW-*` et la quantité
sont synthétiques. Le script ne contacte aucun service et refuse un dossier de sortie
existant. Il ne doit pas être utilisé comme seed de la VM existante.

Pour l'installation existante, `build_plan(catalogue, request, context, profile)`
utilise les ports autorisés : inventaire paginé, comptage des zéros/faibles existants,
sélection déterministe des seuls produits actifs **sans réservation** avec disponible
≥ 5. Le plan retient SKU/ID, physique/réservé avant, révision, motif DEMO et commande
`AdjustTenantStock` à clé stable. Pas d'update direct ni de méthode d'application
automatique. Un manque de candidats ou une taille incohérente refuse le plan.

L'intégration synthétique ajoute 1500 produits et conserve une commande réservée de
contrôle : 63 ajustements suffisent (2 faibles préexistants), chacun rejoué avec la
même clé. Cibles 25+20 / 10+10 atteintes, journaux uniques, nombre de produits constant,
réservation/commande de contrôle inchangées, aucun changement du tenant voisin.
Le nouveau plan est vide une fois les cibles atteintes.

Après merge et CI verte seulement :

1. Vérifier la VM **StockPilot-B2B-v021**, son identité, révision, images/digests et les
   organisations. Ne pas utiliser les anciens opérateurs/identités d'une autre VM.
2. Réaliser une sauvegarde actuelle appariée Mongo/PG/Keycloak avec le workflow qualifié
   de cette installation ; restaurer indépendamment et rapprocher les données avant
   toute mutation. La restauration antérieure ne remplace pas cette preuve actuelle.
3. Préparer release patch via le workflow existant, images privées immuables et rollback
   qualifié vers les digests v0.2.1 conservés. Aucun changement de schéma dans cette PR.
4. Déployer uniquement la révision fusionnée autorisée, puis construire et conserver
   en privé les deux plans depuis les contextes admin explicitement autorisés.
5. Après relecture des SKU et de la sauvegarde : soumettre les commandes au service
   `stock.adjust(request, context, AdjustTenantStock(**row["command"]))` existant, ou
   au BFF `POST /products/{product_id}/adjustments` avec les mêmes champs hors product_id.
   Réutiliser la clé/contenu en cas de reprise ; **arrêter** sur pending/rejected/erreur.
   La révision protège les réservations concurrentes ; ne jamais la remplacer pour
   forcer un plan devenu obsolète. Réconcilier l'opération avant de recalculer.
6. Vérifier les totaux filtrés, invariants `reserved ≤ quantity`, commandes/conversations
   historiques et les scénarios exacts avec les comptes DEMO ; sauvegarder à nouveau.

Aucune de ces étapes opérationnelles n'a été exécutée sur la VM par cette PR.

## HTTPS de la VM : constat en lecture seule

Le 1 novembre, `curl.exe --head` avec validation active renvoie
`SEC_E_UNTRUSTED_ROOT`. Le diagnostic SslStream, qui **refuse** la connexion invalide,
voit `RemoteCertificateChainErrors / PartialChain`, un seul élément de chaîne,
CN `stockpilot-b2b-v021.mshome.net`, émetteur `StockPilot v021 Private Demo CA`.
Pas d'erreur de nom observée ; certificat présenté valide jusqu'au 1 novembre 2027.

La correction à qualifier est la disponibilité/confiance de l'émetteur : récupérer
la CA publique de **cette VM** par le canal opérateur authentifié, vérifier son
empreinte et sa provenance, puis installer cette CA dans le magasin de confiance
approprié Windows/Chrome. Si cet émetteur est intermédiaire, vérifier aussi la chaîne
servie. Ce constat ne justifie ni de faire confiance au certificat feuille seul,
ni de réutiliser la CA d'une ancienne VM. Refaire ensuite un contrôle HTTPS normal.
Aucun certificat installé, aucune validation TLS désactivée, aucune exposition publique.
La lecture `Get-VM` est refusée par les droits Hyper-V actuels ; état actif non attesté
par cet outil. Le serveur HTTPS répond au handshake, ce qui n'atteste pas toute la VM.

## Validation exécutée

Les commandes utilisent exclusivement le checkout existant, son venv (origine du
package vérifiée) et Node 22.19.0. Services CI : projet dédié
`stockpilot-discovery-tests`, montages tmpfs, cibles vérifiées par `check_targets.py`.
Les mots de passe/cookies et logs des campagnes restent hors Git. Le scénario
`integration_optional` de cycle de vie DEMO, qui exige ses propres services, n’a pas
été exécuté ni compté comme réussi. Les campagnes Keycloak réelles sont distinctes.

| Contrôle | Résultat |
| --- | --- |
| Régression initiale Mongo, avant correction | 11 échecs, 1 succès |
| Backend `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` | 3536 passed, 370 subtests ; 563 deselected, non comptés comme réussis |
| Intégrations `-m "integration and not integration_optional" tests` | 562 passed, 3467 deselected, 288 s ; inclut la qualification DEMO et les dernières régressions |
| Régressions découverte Mongo | 22 passed dans la suite finale : prompts, quatre catégories, bornes, isolation, révocation, suggestions, catégorie libre, stock partiel |
| Evals `.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched` | 122 cas, 0 échec ; pas un score de modèle |
| `npm test`, `npm run typecheck`, `npm run lint`, `npm run build` | 585 tests / 37 fichiers ; quatre commandes réussies |
| `npx playwright test --config=playwright.b2b-ux.config.ts` | 30 scénarios (26 existants + 4 nouveaux FR/EN clair/sombre) ; comparaisons 2/4/10 et sessions comprises |
| Ollama réel, commande documentée dans `docs/testing.md` | 2 tests, 12 phrases au total ; qwen2.5:7b réel, Mongo+PG synthétiques ; prix/count/seuils corrects, zéro commande/opération stock |
| Lint Python ciblé `ruff check --select E9,F`, format des nouveaux modules | Réussi ; pas de gate mypy configuré dans ce dépôt |
| `pip check`, `git diff --check` | Réussis |

Modèles observés : qwen2.5:7b digest
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`,
nomic-embed-text digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`.
Les cinq recherches catalogue indépendantes observées prennent environ 1 s chacune
sur ce poste ; ce n'est pas un SLA. Les anciens warnings de fixture JWT courte et
de sérialisation LangGraph sont visibles et non masqués.

Les essais intermédiaires ont découvert et corrigé : confusion seuil/budget en
présence de « disponible », consultation inutile du catalogue pour une question
RAG, lien page 1 pour total nul, et sélecteurs Playwright ambigus. Les fixtures ont
été corrigées pour respecter le contrat GET, les conversations indépendantes et
le comptage des produits déjà faibles. Aucun test existant supprimé/affaibli.

La campagne réelle `B2B_TEST_PORT_SET=isolated V1383_OLLAMA_URL=http://host.docker.internal:11434 .venv/bin/python scripts/tests/validate_bff_keycloak.py --assistant`
a réussi : **42 contrôles**, dont 18 Chromium métier et 8 assistant/Ollama (15
formulations naturelles, RAG scoped sous injection, préparation/confirmation,
idempotence, annulation, historique et redémarrage BFF). Une panne Ollama injectée
sur un port fermé possédé échoue en 503 puis la reprise du tour original réussit.
Rapport local privé : `tmp/v1372-pf4m0h3m/report.json`.

Les essais intermédiaires ont refusé des ports occupés, puis révélé des sélecteurs
historiques antérieurs aux dialogues et au menu actuel. Le scénario a été adapté :
fermeture explicite des dialogues après les lectures, ouverture du menu, sélection
du libellé actuel. La vérification du stockage navigateur autorise uniquement la
clé de préférences avec exactement langue/thème publics ; aucune identité ou aucun
jeton n'est autorisé. Aucune assertion métier n'a été retirée. Chaque tentative a
conservé son rapport et nettoyé uniquement ses ressources ; containers protégés et
volumes inchangés dans le rapport réussi.
Pas de revue indépendante par un second agent. La CI GitHub doit être lue sur la HEAD
finale de la PR ; les tests n'autorisent ni merge ni déploiement.

La campagne `B2B_TEST_PORT_SET=isolated .venv/bin/python scripts/tests/validate_bff_keycloak.py --renewal`
a aussi réussi : **26 contrôles**, dont 8 scénarios Chromium de continuité et
changement de compte. Expiration réellement attendue pendant 32 secondes, ancien
cookie rejoué refusé en 401, révocation du refresh Keycloak, expiration idle injectée
avant refresh, panne fournisseur pendant renouvellement : toutes échouent fermées.
Aucune prétention de recette chronométrée de 30 minutes. Le test de déconnexion attend
maintenant le 401 serveur après l'effacement local immédiat (attente bornée, aucune
assertion supprimée). Rapport privé : `tmp/v1372-uwtijhiy/report.json` ; nettoyage
réussi, containers protégés et volumes inchangés.

## Décision de livraison

**GO pour la revue de la PR vers main**, sous réserve des contrôles GitHub sur sa
HEAD finale. **NO-GO pour un déploiement immédiat** : merge utilisateur, CI verte,
release qualifiée, confiance TLS vérifiée, sauvegarde actuelle et restauration isolée
restent les préconditions. Les stocks DEMO et la recette des comptes sur la VM
existante restent à exécuter après ces étapes. Aucune donnée de cette VM n'a été
modifiée ; les 1500 produits et les historiques annoncés n'ont pas été réinitialisés
ni utilisés comme fixtures mutables. Leur intégrité complète sur la VM reste à
rapprocher lors de la sauvegarde/recette opérationnelle.
