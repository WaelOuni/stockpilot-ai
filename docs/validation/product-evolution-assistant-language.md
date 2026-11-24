# Corrections des six formulations UAT catalogue et stock

Base indépendante : `main` fd7e212f3f9688f70aa7a121f7022b3d4ee72aa2.
Correction séparée de l'analytics : aucun prompt, modèle ou mécanisme ORDER changé.

## Diagnostic reproduit

Les critères existants interprétaient certaines questions génériques comme un
nom de produit, conservaient « bientôt » comme une référence résiduelle, et ne
reconnaissaient pas la demande exacte des cinq produits les moins chers. Le
stock faible qualitatif incluait aussi zéro, alors que l'UAT demande de distinguer
stock faible positif et rupture. Les nouvelles assertions sur les six phrases
ont produit 5 échecs et 4 succès sur main avant la correction.

| Formulation | Contrat corrigé |
| --- | --- |
| perceuse dispo avec moins de 100 euros | Catégorie perceuse, disponible >0, prix strictement <100 EUR |
| les produits qui vont s'épuiser bientôt | Stock faible actuel positif et avertissement explicite : historique et délai requis pour un risque futur |
| Montre-moi les produits qui sont presque en rupture de stock | 0 < disponible <5, seuil indicatif annoncé |
| Quels sont les 5 produits les moins chers de mon catalogue ? | Cinq résultats au maximum, prix puis SKU, dans une seule devise |
| Est-ce que vous avez des perceuses disponibles ? | Recherche générique perceuse, disponible >0 |
| Quels produits sont complètement en rupture de stock ? | Disponible =0 exactement |

Les transformations sont bornées aux formulations génériques reconnues. Les mots
d'identité inconnus, noms entre guillemets et SKU ne sont pas supprimés. Les
seuils numériques explicites conservent leur sens : « moins de 5 » inclut zéro.
Une recherche des moins chers avec plusieurs devises demande une précision ;
les devises d'une autre organisation n'influencent ni ce choix ni le classement.

## Architecture et sécurité

Le service de découverte conserve l'autorisation tenant avant/après les lectures.
Le port catalogue ajoute une lecture des devises actives, avec agrégation bornée
à deux valeurs distinctes, timeout et portée organisation. Aucun parcours
d'écriture ni appel à l'analytics n'est ajouté. Les six phrases restent sûres
même si la classification modèle proposée est ORDER : pas de préparation,
confirmation ou réservation issue de ces lectures.

Les tests Mongo utilisent des bases UUID exclusivement sur services jetables,
avec produits synthétiques des organisations A et B, disponibilité 0/4/5/8,
réservations et prix de part et d'autre de la borne stricte 100 EUR. Ils vérifient
les SKU exacts, le classement, la devise et l'absence de mutation métier.

## Oracles et régressions

Le corpus historique de 301 formulations reste inchangé. L'oracle de la campagne
qualitative est explicitement versionné `v025-boundaries-v2-low-positive` : les
seules attentes modifiées sont celles qui définissaient « faible » comme incluant
zéro. Les cas numériques et rupture exacte sont conservés. Les tests ajoutent
les assertions min=1, rupture=0 et risque futur indisponible : aucun test n'est
supprimé ni affaibli pour masquer une erreur. La première suite a exposé 18
attentes historiques correspondant à l'ancienne sémantique ; elles sont documentées
et la campagne déterministe complète est rejouée.

Le harness navigateur reçoit aussi la correction commune de sa fixture de
préférences publiques : `assistantSound:false` existe déjà dans l'application
main. Le test initial échouait sur ce champ manquant dans l'attendu ; les contrôles
de cookies, absence de tokens et absence de données métier persistées restent.

- Tests ciblés nouveaux et lexicaux : 70 succès ; nouveaux cas unitaires : 9.
- Campagne historique : 1 446 succès après qualification de la distinction faible/rupture.
- Backend complet : 5 934 succès, 830 désélectionnés, 370 sous-tests ; un avertissement
  préexistant concerne une clé HMAC de fixture courte, pas une clé d'application.
- Evals déterministes enrichies : 122/122.
- Mongo ciblé : 41 succès, dont les six formulations et les frontières multi-tenant.
- Intégrations complètes MongoDB/PostgreSQL : **829 passed**, 5 865 désélectionnés.
- Les captures de réponses avant et après viennent de la même fixture Mongo
  jetable, exécutée respectivement sur main et cette branche. Les six tests
  `frontend/ux-e2e/uat-language.spec.ts` les rendent dans le vrai frontend Chromium
  mobile avec envoi au clavier et absence d'action ORDER. Le transport BFF y est
  intercepté, sans prétendre que cela teste OIDC. Captures locales :
  `tmp/uat-before-captures/` et `frontend/test-results/uat-language-*/`.
- Frontend : **642 tests**, typecheck/lint/build ; Playwright : **90 succès**,
  dont les six phrases exactes. La capture main produit six échecs attendus avec
  les nouvelles attentes, puis les six cas passent sur les réponses corrigées.
- Keycloak 26.8.1 réel + HTTPS Vue/Chromium : **32 contrôles**, rapport local
  `tmp/v1372-ydaz6uvp/report.json`, nettoyage et ressources protégées inchangées.
  CI dans la description de Draft PR. Aucune capture ni sortie privée de test
  n'est commitée.

Les tests de langage sont déterministes et Mongo réel ; ils ne mesurent pas une
inférence Ollama réelle. Aucun nouveau score de précision modèle n'est annoncé.
Les prévisions de rupture et l'élasticité promotionnelle ne font pas partie de
cette correction. GO pour revue de développement ; livraison et revue indépendante
restent distinctes. Aucun merge, release ou déploiement n'est exécuté.
