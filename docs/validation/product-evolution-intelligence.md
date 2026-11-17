# StockPilot Intelligence — backend et modèle analytique (PR C)

Base : `origin/main` fd7e212 ; worktree dédié, 17 novembre 2026. Audit du code,
des schémas versionnés et de fixtures jetables ; aucune lecture ou mutation des
données de production. Aucun changement ORDER, modèle Ollama, PostgreSQL,
authentification Keycloak ou déploiement. La PR D consomme le contrat ci-dessous.

## Diagnostic des sources et qualité

| Source existante | Sémantique vérifiée | Utilisation autorisée |
| --- | --- | --- |
| `b2b_products` | Organisation, identifiant natif, SKU exact, catégorie, statut, prix entier/devise, quantité physique/réservée, révision | Snapshot de stock et valorisation au tarif courant |
| `b2b_orders` | prepared/reserved/cancelled ; création, réservation, annulation | Une réservation n'est pas une vente réalisée. Aucun backfill des ventes |
| `b2b_stock_movements` | Ajustements et imports administratifs, avant/après, auteur, source, date | Le signe d'un delta ne qualifie ni vente ni réception fournisseur |
| Coût, fournisseur, délai, minimum, entrées confirmées | Absents des produits historiques | Paramètres facultatifs dans une collection additive et sourcée |
| Ventes réalisées, retours, transferts, réceptions qualifiées, ruptures passées | Absents comme historique commercial exploitable | Faits journaliers explicitement qualifiés ; aucune inférence depuis ORDER |
| Lots, dates d'entrée des lots, prix historiques promotionnels, consommation annuelle moyenne | Absents | Vieillissement, rotation réelle, rentabilité promotionnelle et saisonnalité annuelle non calculés |

Sur une base historique sans nouveaux faits : KPI de stock disponibles,
`no_commercial_history`, chiffres de vente/prévision/marge inconnus à `null`.
Sur une base restée en schéma 6 : les fonctionnalités existantes restent
utilisables par le nouveau code ; Intelligence renvoie 503 jusqu'à la migration
explicite. Aucun DDL au démarrage ou pendant une lecture.

Le système n'intègre pas un connecteur de caisse/comptabilité. Un futur import
de données observées devra qualifier la vente réalisée, la clôture de chaque
journée, les ruptures, les corrections et les références sources. La simple
présence d'une référence déclarée ne prouve pas le contenu d'un système externe.
Les collections ne sont pas exposées à une route d'écriture publique.

## Dictionnaire additif — migration 0007

`b2b_demand_days` : unicité `(organization_id, product_id, day)` ; `product_id`
est la représentation canonique de l'ObjectId du produit de cette organisation.
Jour UTC ISO, une ligne par produit/jour, y compris les jours complets sans vente.

| Champ | Unité / contrat |
| --- | --- |
| `sold` | Unités de ventes réalisées brutes, entier positif ou zéro ; jamais réservations |
| `returned`, `cancelled`, `reserved` | Unités d'événements qualifiés distincts ; ni soustraction implicite ni conversion en ventes |
| `received`, `transferred` | Unités reçues/transférées qualifiées ; n'impliquent pas une vente |
| `adjusted` | Unités administratives signées ; indiquent une analyse possible, pas une anomalie prouvée |
| `complete` | Jour entièrement couvert par la source, booléen strict |
| `stockout` | Rupture susceptible de masquer la demande, booléen strict |
| `source_references` | 1–20 références bornées de provenance, à qualifier par le futur producteur de données |
| `dataset` | `observed` ou `DEMO_SYNTHETIC`, obligatoire ; mélanges refusés pour les prévisions |

Quantités journalières bornées à 10^9, ajustements à ±10^9. Dates invalides,
trous, doublons ou derniers jours manquants ne sont pas complétés par des ventes
nulles. Les journées manquantes d'une série affichée portent
`observed_products=0` ; les graphiques doivent laisser un trou.

`b2b_analytics_profiles` : unicité `(organization_id, product_id)` ; devise et
références sources obligatoires. Champs facultatifs : `unit_cost_minor` entier
en unités mineures, fournisseur, délai 0–90 jours, sécurité, minimum ≥1,
entrées réellement confirmées et `incoming_as_of` (jour UTC). Les entrées doivent
être attendues pendant le délai annoncé. Une confirmation datée d'un autre jour
empêche la recommandation chiffrée. Un coût dans une autre devise est inconnu
pour ce produit ; aucune conversion. Il s'agit d'un coût documenté courant,
pas d'une valorisation comptable des lots. Les champs absents restent absents.

Les validateurs Mongo sont stricts, les index incluent l'organisation, aucun
TTL et aucun historique existant modifié. Migrations 0001–0006 inchangées.
Pas de backfill ni de vente fictive injectée dans une application.

## Frontière API et architecture

`GET /b2b/api/intelligence` derrière la frontière BFF existante, en mode business.
Permission additive `analytics:read`, seulement MANAGER et ORGANIZATION_ADMIN
(l'ADMIN du produit). Les deux conditions rôle/permission et le contexte de
session sont revalidés avant lecture et avant retour. USER/READ_ONLY : 403 ;
absence de session : 401. Le navigateur n'envoie pas l'organisation. Headers
d'identité forgés : 400 ; paramètre organisation ou query ambiguë : 422 ; Origin
étranger/proof CSRF périmée : 403. Aucune méthode de mutation dans le port.

Filtres : période 30/90/180/365 jours, horizon 7/30/60/90, catégorie exacte,
produit natif exact, statut active/inactive, seuil faible explicite, remise
personnalisée 0–9000 points de base, limit 1–20 et offset 0–10000. Les produits
étrangers ressemblent à un résultat vide. Tri stable SKU/identifiant.

Une agrégation globale filtrée et trois lectures par lots dans une transaction
de lecture snapshot : produits, faits journaliers, profils. Pas de N+1. 365 jours
maximum par produit, 20 prévisions au maximum. Délai Mongo total de 8 s et
maxTimeMS de 3 s par requête ; admission/worker/timeout BFF existants conservés.
Pas de cache : l'échantillon mesuré ne justifie pas une matérialisation ni un
mécanisme d'invalidation supplémentaire. Chaque réponse porte sa date UTC.

Les KPI `summary` couvrent tous les produits filtrés. `page_summary`,
`page_series`, ventes, dormance, couverture et suggestions portent seulement sur
les produits de la page. Le frontend doit afficher cette distinction. Les
références sources visibles sont bornées à 20 par produit, leur nombre complet
est fourni. Les montants et stocks potentiellement grands voyagent en chaînes.

## Formules et limites des indicateurs

- Disponible = physique − réservé pour un produit actif, zéro si inactif.
  Rupture = actif et disponible=0 ; faible = actif et 0<disponible≤seuil.
- Valeur au tarif courant = Σ(physique × prix catalogue), séparée par devise.
  Ce n'est ni coût, ni revenu, ni bénéfice. Decimal128 évite l'overflow double ;
  une somme >10^30 unités mineures renvoie `null`, jamais un entier arrondi
  présenté comme exact. Les quantités agrégées hors borne exacte sont refusées.
- Coût de stock connu d'un produit = physique × coût unitaire documenté en
  même devise, calcul entier Python. Aucun coût manquant remplacé par zéro.
- Ventes de période = Σ sold si chaque jour est complet et la provenance
  homogène. Moyenne = ventes/nombre de jours. Retours présentés séparément.
- Dormant = stock physique positif, période complète sans rupture, aucune
  vente observée. Ce constat n'établit ni âge du stock ni fin de cycle.
- Ratio ventes/stock courant : proxy explicitement nommé, pas une rotation
  réelle (stock moyen historique absent). L'UI n'en fait pas une rotation annuelle.
- Couverture = disponible / moyenne quotidienne prévue si cette moyenne >0.
  Pas de date certaine de rupture. Moyenne de couverture sur produits calculables
  seulement, avec portée de page explicite.

## Prévisions, validation temporelle et incertitude

Méthodes candidates : dernière valeur naïve, moyenne mobile 28 jours,
moyenne 28 jours pondérée croissante, lissage exponentiel simple alpha=0,2,
Croston-SBA alpha=0,1 pour demande intermittente (≥40 % de zéros et ≥4 demandes
positives dans le premier entraînement), saisonnière naïve hebdomadaire avec
au moins 12 cycles d'entraînement. Pas de saisonnalité annuelle supposée.

Entraînement croissant et holdouts non chevauchants de la taille exacte de
l'horizon. Au moins 3 fenêtres : les deux premiers tiers (minimum 2) sélectionnent
le modèle par MAE des quantités cumulées ; les dernières évaluent le modèle
choisi et la référence naïve, sans resélection sur ces résultats. Minimum 28
jours d'entraînement, porté à 84 quand assez de données permettent les cycles
hebdomadaires. Les dates futures sont exclues avant tout calcul. L'erreur est
mesurée sur les totaux par horizon, pas une précision quotidienne revendiquée.

Exécution synthétique `tmp/forecast-backtesting.json` (aucune mesure commerciale) :

| Série DEMO | Horizon | Modèle retenu | MAE / naïve, unités | Fenêtres d'évaluation |
| --- | ---: | --- | ---: | ---: |
| Stable, 2/jour | 7 | naïve | 0 / 0 | 14 |
| Tendance par paliers | 30 | naïve | 5 / 5 | 3 |
| Intermittente, 8 tous les 10 jours | 7 | Croston-SBA | 3,339 / 13,714 | 14 |
| Intermittente | 30 | moyenne pondérée 28 | 0,828 / 24 | 3 |
| Cycle hebdomadaire | 30 | saisonnière naïve 7 | 0 / 189,667 | 3 |
| Cycle hebdomadaire | 90 | saisonnière naïve 7 | 0 / 689 | 1 |

Les quatre horizons et quatre séries sont dans le rapport. Une méthode plus
complexe ne remplace pas la naïve si elle ne gagne pas sur la sélection.
Avec ≥8 fenêtres d'évaluation, une plage empirique utilise l'erreur absolue
maximale observée autour de la prévision, borne basse à zéro. Elle est nommée
`historical_error_range`, sans probabilité garantie (`probability=null`). Sinon,
aucune incertitude numérique. Aucune précision en pourcentage annoncée.
Un historique incomplet, trop court, mélangé, ou censuré par une rupture produit
une raison explicite et aucune prévision numérique. Aucun remplissage silencieux.

Fondements : [méthodes simples et validation temporelle, FPP3](https://otexts.com/fpp3/toolbox.html),
[demande intermittente et limites pratiques, FPP3](https://otexts.com/fpp3/practical.html).

## Réapprovisionnement et simulations commerciales

Taux = prévision cumulée/horizon. Point = plafond(taux × délai + sécurité).
Cible = plafond(taux × max(délai,horizon) + sécurité). Besoin = max(0, cible −
disponible − entrées confirmées). Si besoin>0 : max(minimum fournisseur, besoin).
Le taux moyen est une hypothèse sur ce délai, pas une garantie de demande.
Tous les paramètres requis doivent être connus ; produit inactif : aucune
recommandation chiffrée d'achat. Aucun achat, mouvement ou ORDER déclenché.

Remises 5/10/15 % et scénario personnalisé, en points de base. Prix simulé =
arrondi demi-supérieur en unités mineures de prix × (1−remise). Écart unitaire =
prix simulé − coût connu, y compris une valeur négative. Ce n'est pas un bénéfice
net : taxes, frais, quantités futures, élasticité et coût de campagne inconnus.
Aucun gain de demande ni rentabilité supposés. Le moteur peut signaler l'absence
de vente observée pour examen commercial ; il n'invente ni période optimale de
promotion ni vieillissement de lots.

La simulation interne ne publie pas d'annonce. En contexte consommateur, le
prix antérieur utilisé pour une réduction ne se déduit pas du seul tarif courant :
voir [DGCCRF](https://www.economie.gouv.fr/dgccrf/les-fiches-pratiques/annonces-de-reduction-de-prix-ce-que-vous-devez-savoir).
L'applicabilité au parcours de commercialisation futur et les données de prix
de référence devront être qualifiées avant publication ; aucune certification
réglementaire B2B générale n'est revendiquée.

Explications déterministes : raisons et paramètres structurés calculés côté
serveur. Ollama n'est pas appelé pour paraphraser ces faits ni recalculer des
chiffres ; le module fonctionne sans fournisseur. Une génération libre n'apporte
pas ici de bénéfice justifiant une nouvelle surface de grounding.

## Migration, compatibilité et retour arrière

Procédure de développement uniquement : sur serveur jetable attesté et base
UUID admise, `migrate status`, `plan`, puis `apply` avec confirmation exacte,
`validate` et `require_version(7)`. Voir [runner existant](../mongodb-migrations.md).
Le plan ajoute exactement deux collections vides. Réapplication sans effet,
unicité/validateurs/index/ancienne histoire contrôlés ; aucun downgrade destructif.

Les anciens binaires ne reconnaissent pas un historique 7. Remettre une image
v0.2.8 ne constitue donc pas un rollback valide après migration. Une future
livraison exige sauvegarde appariée, restauration isolée, dry-run, comparaison
de données, stratégie de continuité des écritures, images/digests approuvés et
autorisation distincte. Les opérateurs historiques de schéma 6 restent stricts
et refusent 7 ; ils ne sont pas présentés comme un outil de restauration v7.

Les tests historiques de transition 4→6 et 5→6 utilisent maintenant leur registre
explicitement borné. Les checksums approuvés ne changent pas. Le test offline du
runner reconstruit exactement son empreinte historique en retirant seulement
les deux nouveaux littéraux de la liste autorisée ; toute autre modification
échoue. Le probe CI de rollback 4→6 conserve les six migrations exactes et ne
migre pas silencieusement vers 7. Un test prouve que l'opérateur lié à l'ancienne
installation refuse le nouveau registre sans écriture. L'opérateur n'a pas changé.

## Validation et preuves locales

- RED HTTP sur main non modifié : `/tmp/stockpilot-intelligence-red.log`, manager
  reçoit 404 au lieu du nouveau 200. Aucun endpoint analytics existant n'est remplacé.
- Tests formules/qualité/rôles/révocation : `tests/test_demand_forecast.py`,
  `tests/test_intelligence.py` (**35 passed**). Grandes sommes, multi-devises,
  coûts absents, retours/réservations distincts, trous, stockouts, dates futures,
  horizon 7/30/60/90, baseline et holdouts couverts.
- Intégration réelle : `tests/test_intelligence_mongo_integration.py`, incluant
  matrice rôles, injection tenant/Origin/CSRF, données étrangères, absence
  d'écritures métier, migration idempotente, 6→7 sans perte, exactitude monétaire
  extrême et snapshot sans historique.
- L'échantillon de performance couvre 20 produits × 365 jours, trois lectures
  HTTP : rapport `tmp/intelligence-performance.json`. Le jeu DEMO comprend
  stabilité, tendance, intermittence, cycle hebdomadaire, dormance et stockouts,
  avec mouvements distincts. Il est créé uniquement par la fixture UUID protégée.
- Régressions backend : **5 961 passed**, 845 désélectionnés, 370 sous-tests ;
  intégrations MongoDB/PostgreSQL : **844 passed**, 5 891 désélectionnés.
  Evals déterministes enrichies : **122/122**. Frontend : **642 tests**, typecheck,
  lint et build ; navigateur ADVICE synthétique : **11 passed**.
- Performance locale sur le jeu DEMO : **233,99 / 98,08 / 98,76 ms** pour trois
  réponses de 20 produits × 365 jours. Ce n'est pas un benchmark de production.
- Première exécution : 3 contrats unitaires puis 8 tests d'intégration historiques
  ont révélé les hypothèses « registre=6 » ; les suites complètes corrigées passent,
  sans suppression de tests. Un avertissement HMAC concerne une fixture existante.
- Résultats de la campagne réelle Keycloak, Playwright complet et CI : description
  de la Draft PR et rapports locaux. Le backend n'introduit pas d'écran ; la
  qualification visuelle avant/après relève du lot Intelligence UI séparé.

GO pour revue de développement, NO-GO livraison : aucune nouvelle version publiée,
aucune migration de production exécutée, revue indépendante non encore réalisée.
