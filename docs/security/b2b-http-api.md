# API B2B HTTP — V13.8.1

## Périmètre et activation

Entrypoint séparé : `stockpilot.bff:create_app --factory`. Ne charge ni
`stockpilot.main`, ni les repositories globaux, ni le graphe historique.
`BFF_MODE=disabled` par défaut ; `b2b_auth_test` conserve auth/session uniquement.
Le nouveau mode **b2b_business_test** compose les ports B1/B2/B3 sur la même base
que les identités/sessions. Il exige une base explicitement jetable
`stockpilot_b2b_test_<32 caractères hexadécimaux>`, outre les paramètres OIDC
complets. Ce nom n'est pas une preuve d'isolation : credentials dédiés, replica set,
contrôle du serveur et provisioning opérateur sont indispensables.

Aucune migration ou création d'index au démarrage du nouveau mode. Provisionner
explicitement identités, index BFF et migrations Mongo0001/0002/0003 sur le seul
serveur jetable vérifié. Aucun changement de leurs checksums ou des données
historiques. Le mode production_saas de l'application historique reste fermé.
Ce mode de test n'autorise aucune ouverture Internet ou installation persistante.

## Chaîne de confiance

Cookie opaque → BffSessionService.current (expiration/révocation/identité/
organisation/membership) → TenantContext serveur → revalidation par
TenantAuthorizationService → permission → port spécialisé → Mongo scoped.
Le contexte n'est jamais désérialisé depuis JSON, un rôle Keycloak ou un header.
Les headers Authorization/X-Organization-Id/X-User-Id/X-Role/X-Permissions/
X-Customer-Id sont refusés sur les routes métier. Les corps ont extra=forbid.
L'organisation est celle de la session ; aucun endpoint de changement d'organisation
n'est livré. Le choix au login demeure un candidat vérifié au callback.

Identités, memberships, acheteurs et ressources Mongo partagent la même DB.
Les fences transactionnelles B2/B3 sont conservées. Une révocation de session
après la dernière relecture reste une fenêtre résiduelle : pas d'atomicité
Keycloak/BFF/Mongo/PostgreSQL. Pas de refresh ou back-channel logout nouveau.

## Routes du nouveau mode

Préfixe public sans réécriture : **/b2b/**. Routes auth existantes :
POST /b2b/auth/login, GET /callback, GET /session et /me, POST /logout,
GET /logged-out (les suffixes sont sous /b2b/auth).
/health reste une liveness, pas une preuve de readiness.

Toutes les routes ci-dessous sont sous /b2b/api, avec session obligatoire.
Toutes les mutations exigent Origin exacte et X-BFF-CSRF lié à la session,
avant lecture du corps. Les permissions ne remplacent jamais ownership/filtrage.

| Méthode | Chemin | Permission / règle |
|---|---|---|
| GET | /products | catalogue:read, pagination et filtres |
| GET | /products/by-sku?sku=... | catalogue:read, SKU exact y compris slash encodé |
| GET | /products/{product_id} | catalogue:read, ID natif scoped |
| GET | /products/{product_id}/stock | stock:read, physique/réservé/disponible |
| GET | /buyers | order:create, associations actives du seul utilisateur/org |
| GET | /orders | own=created_by ; order:read_organization élargit à l'org |
| POST | /orders/prepare | order:create, acheteur explicitement associé, aucune réservation |
| GET | /orders/operations?namespace=...&operation_id=... | acteur d'origine, droits de l'opération et grant actuels |
| GET | /orders/{order_id} | order:read_own ou permission d'organisation |
| POST | /orders/{order_id}/confirm | créateur, session/préparation/digest d'origine, confirmed=true |
| POST | /orders/{order_id}/cancel | order:cancel_own ou permission d'organisation |
| POST | /products/{product_id}/adjustments | stock:adjust, physique absolu + expected_revision + motif |
| GET | /stock/movements | stock:read, journal physique B3 non exhaustif ORDER |
| POST | /imports/preview?mode=... | import:execute, corps text/csv, aucun stock modifié |
| POST | /imports/apply | import:execute, acteur/session/preview/digest, confirmed=true |
| GET | /imports | import:execute, historique scoped |
| GET | /stock/operations?namespace=...&operation_id=... | permission adjust/import et acteur d'origine |

Namespaces ORDER : prepare/confirm/cancel ; stock : adjust/import.
Les clés d'opération sont conservées exactement. Utiliser les routes query
canoniques pour toutes les clés, notamment "."/".." : aucune normalisation de
segments de chemin. Les variantes path /products/by-sku/{sku},
/orders/operations/{namespace}/{operation_id} et /stock/operations/{namespace}/{operation_id}
existent aussi, avec slash encodé, pour les clés sans segments normalisés.
Aucune route assistant, RAG, checkpoint, dashboard global ou gestion de membres.
Aucun proxy générique ni accès aux anciennes routes /assistant, /products,
 /orders ou /backoffice depuis ce BFF.

## DTO et pagination

Requêtes JSON typées et bornées : quantités entières strictes (pas booléen, texte,
fraction), confirmed doit être le booléen true. Identifiants 1–128 caractères,
pas d'identité utilisateur/org dans les corps. Le customer_id de préparation
est une sélection candidate ; le grant serveur est revérifié dans la transaction.
Une même identité acheteur partagée ne transforme pas les commandes d'autrui en own.

Produits : product_id, SKU, nom, description, catégorie, attributes, statut,
revision, **price_minor chaîne entière décimale**, currency explicite.
Exemple : `{"price_minor":"4900","currency":"EUR"}` ; aucune conversion float.
Stock : quantity physique, reserved, available, revision et statut.
Commande : prepared/reserved/cancelled, lignes aux prix exacts, created_by,
customer_id, dates et preparation_digest. Pas de cookie/session binding exposé.

Listes : limit 1–100, offset 0–10000 ; tri déterministe. Catalogue : total/has_more
cohérents par agrégation. Autres listes : items/limit/offset, sans total implicite.
Catalogue : category, attribute, currency, minimum_price_minor,
maximum_price_minor, status, available. Devise obligatoire pour un filtre de prix.
available est appliqué dans Mongo avant pagination ; aucun opérateur Mongo libre.
Les paramètres inconnus/dupliqués sont refusés sur les listes.

CSV : 256 Kio et 100 lignes au plus, modes create_only/metadata_only/stock_only.
Preview invalide : 200 avec status=invalid et diagnostics bornés ; aucune mutation
produit. Apply : lot entièrement atomique, quantity>=reserved et revision relue.
Un mouvement delta=0 est valide. Aucun ajustement administratif de reserved.

## Navigation navigateur V13.8.2

1. POST JSON /b2b/auth/login avec seulement organization_id, Origin exacte,
   Content-Type application/json et X-BFF-CSRF: login. Sans session initiale,
   origine exacte + en-tête non simple + absence de CORS permissif protègent
   l'amorçage ; aucun GET à effet de bord ajouté.
2. Réponse200 `{"authorization_url":"..."}` et cookie de transaction __Host-,
   Secure, HttpOnly, SameSite=Lax. URL produite par l'adaptateur OIDC configuré.
3. Vue effectue une navigation top-level vers cette URL. Aucun token localStorage.
4. Callback GET public exact /b2b/auth/callback : state/nonce/PKCE inchangés.
   Retour303 fixe vers /b2b/auth/session, sans return_url arbitraire.
   La future UX devra lire cette session et définir son retour fixe côté serveur.
5. GET session/me renvoie utilisateur/org/permissions/CSRF/expiration.
   Mutations suivantes : cookies same-origin et X-BFF-CSRF courant.
6. Logout local révoque puis renvoie provider_logout_url ; fermeture SSO séparée.

Réauthentification : cookie et CSRF changent. Purger les réponses/états d'ancienne
session dans Vue. Une première confirmation/apply ne transfère pas une préparation
liée à l'ancienne session. Ne pas recréer une opération après un résultat incertain :
réconcilier d'abord les clés existantes, puis décider explicitement de préparer à nouveau.

## Résultats, erreurs et réconciliation

| HTTP | Contrat |
|---|---|
| 200 | Lecture, préparation, preview ou mutation durable réussie |
| 202 | Opération B3 encore pending ; pas un succès métier |
| 400/422 | Headers, paramètres ou DTO invalides ; pas d'entrée brute renvoyée |
| 401 | Session absente/expirée/révoquée |
| 403 | Droits/membership/org/CSRF/Origin refusés |
| 404 | Ressource absente ou étrangère, indistinguables |
| 409 | Conflit d'idempotence/état ou résultat B3 rejected durable |
| 413 / 408 | Taille ou délai de lecture du corps dépassé, avant dispatch |
| 429 | Admission locale saturée, Retry-After |
| 503 | Dépendance indisponible ou OUTCOME_UNKNOWN avec référence d'opération |

Exemple d'issue incertaine :
`{"code":"OUTCOME_UNKNOWN","operation":{"namespace":"confirm","operation_id":"op-1"}}`.
Conserver la commande exacte et sa clé ; interroger le GET du namespace concerné.
Le résultat ORDER est le statut courant (une confirmation rejouée après annulation
reste cancelled). B3 distingue pending/succeeded/rejected.
Un404 ne prouve **jamais** l'absence de commit : ni nouvelle clé compensatoire,
ni annonce d'annulation. Rejouer seulement la même commande autorisée.
Même clé/contenu différent :409. Aucun TTL ne supprime la preuve d'idempotence.

Réponses no-store, erreurs sans credentials/traces, X-Request-ID aléatoire serveur.
Les messages CSV et résultats B3 gardent leurs codes métier assainis.

## Ressources et proxy

Par worker : BFF_MAX_INFLIGHT=8, BFF_REQUESTS_PER_MINUTE=600 par défaut.
Même admission pour auth et métier ; borne mémoire constante, pas de table
non bornée par utilisateur. Limites locales au processus, **pas quotas distribués**.
Offloading AnyIO borné, sans abandonner un thread transactionnel après déconnexion.
Mongo runtime : sélection/connexion/socket5s, attente pool1s, pool lié à l'admission ;
retries/deadlines transactionnels B2/B3 inchangés.
JSON64Kio, CSV256Kio, BFF_BODY_TIMEOUT_SECONDS=5 avant dispatch seulement.
L'expiration du corps ne couvre pas une transaction déjà en cours.

Lancer Uvicorn avec **--no-proxy-headers** pour conserver le peer TCP original.
TLS direct : HTTPS/Host exacts obligatoires, headers forwarded refusés par défaut.
Option explicite BFF_TRUSTED_PROXY_IPS : liste JSON d'IP exactes, aucune wildcard.
Seuls ces peers peuvent fournir X-Forwarded-Proto=https et X-Forwarded-Host
égal au Host public configuré (port compris). Forwarded ambigu/multiple et autres
headers de routage sont refusés ; X-Forwarded-For ne fournit jamais une identité.
Le proxy devra écraser les headers du client et rendre l'upstream inaccessible
directement. Aucune redirection n'est calculée depuis un header.

La configuration Nginx historique /api ne doit pas être réutilisée telle quelle :
prévoir /b2b/→BFF sans retirer le préfixe, préserver Host public/port/schéma.
La recette du proxy réel relève de la démo jetable ultérieure ; aucune configuration
déployée modifiée ici. Sessions fournisseur, sauvegardes/restauration, quotas
multi-worker et ouverture Internet restent des gates distinctes.

Voir [validation V13.8.1](../validation/v13.8.1-secure-b2b-http.md),
[BFF OIDC](keycloak-bff.md), [démo canonique](../b2b-demo.md).
