# V13.2 — Matrice d’accès et frontière HTTP

Audit indépendant en lecture seule, 16 octobre 2026. Baseline : `6f0949676e33a21c640b2ccc2f5877368a4dd0cb` (avant changements V13.2). Aucun accès ni changement à la DEMO v017. Ce document décrit la baseline, avant les correctifs V13.2.

## Conclusion et limites

Les appels LLM directs `/chat` et `/classify`, ainsi que les lectures du catalogue/stock, sont publics par construction. C’est un contrat local documenté et testé, mais il ne convient pas à une exposition SaaS. Les commandes et conversations ont déjà une isolation par `customer_id` signé ; il serait incorrect d’annoncer leur absence d’authentification ou une fuite interclient démontrée. Aucun modèle d’organisation/tenant ne protège le catalogue, le RAG, les opérations administratives ou les lots d’import. Les sessions admin sont explicitement limitées à un processus.

La frontière HTTP suivie dans Git ne fournit pas de politique de hosts, de headers navigateur ou de HTTPS SaaS. L’absence de headers sur la DEMO n’est pas démontrée ici : elle n’a pas été interrogée et le rapport V13.1.1 indiquait seulement une observation filtrée incomplète. L’hébergement SaaS et son proxy TLS restent à définir.

## Contrats communs

- **P** : aucune authentification. Aucun utilisateur/tenant. Données globales.
- **C** : `require_customer_identity`, JWT HS256 avec signature et `exp` requis, `customer_id` texte non vide. Algorithme strict ; configuration absente/courte => 503, token invalide => 401. Aucun issuer/audience/sub/tenant ou mécanisme de révocation configuré. Header `X-Customer-Id` et identité JSON ne font pas autorité. `auth.py:16-73`.
- **T** : `require_backoffice_token`, secret Bearer technique partagé, comparaison constante, aucun utilisateur/rôle/tenant et aucune expiration. Secret absent => 503 ; invalide => 401. `auth.py:76-102`.
- **S** : session cookie opaque admin, utilisateur unique configuré, stockage mémoire ; TTL fixe, rotation au login, logout effectif dans le processus. Cookie `__Host-`, Secure, HttpOnly, SameSite strict, Path `/`. Exception HTTP uniquement en développement avec origine/host loopback ; pas de compte par défaut. `admin_auth.py:81-158,207-252`.
- **M** : S + refus du header Authorization + Origin exacte + token CSRF lié à la session. `admin_auth.py:160-175`.
- **R** : T ou S ; si Authorization est présent, validation T obligatoire, sans fallback cookie. `admin_auth.py:146-158`.
- **S!** : session uniquement, refus de tout Authorization. `admin_auth.py:138-143`.

La configuration manque actuellement de distinction TEST/LOCAL_DEMO/PRODUCTION_SAAS ; seuls `development|production` existent (`settings.py:12`). Le Dockerfile impose `production` mais conserve P pour `/chat`, `/classify`, `/products`. Chaque route de la matrice est exposée via `main.py:193-205`.

## Matrice exhaustive des opérations API

Dans « compatibilité », **local** signifie contrat existant à conserver pour un environnement explicitement local ; aucune modification de v017 n’est requise. **SaaS** décrit le contrat recommandé, pas une garantie actuellement implémentée. Les chemins frontend passent par le préfixe `/api` puis le proxy le retire. « global » signifie aucune frontière tenant dans le port/repository sous-jacent.

| Méthode et chemin | Auth / autorisation | Utilisateur / tenant actuels | Lectures et modifications | Risque interclient | Appel frontend | Tests principaux | Compatibilité et attente SaaS |
|---|---|---|---|---|---|---|---|
| GET `/health` | P | aucun | réponse fixe UP, aucune DB | aucun contenu métier | probes Docker/CI | `test_health.py` | public minimal dans tous les modes |
| POST `/chat` | P | aucun | génération Ollama ; aucun ordre | consommation LLM anonyme | aucun | `test_api_routes.py`, `test_backoffice_auth.py:183` | local existant ; SaaS fermer ou authentifier avec quota |
| POST `/classify` | P | aucun | classification/extraction Ollama | consommation LLM anonyme ; extraction technique | aucun | mêmes tests + classification | local existant ; SaaS désactiver l’exposition HTTP : l’assistant utilise déjà le use case interne |
| POST `/assistant` | C | customer signé ; aucun tenant | catalogue/stock, RAG, état/checkpoints ; préparation/confirmation et réservation atomique possibles | propriété commande et checkpoint déjà protégée ; catalogue/RAG globaux | `sendAssistantMessage`, `api/client.ts:5` | `test_customer_jwt.py`, `test_customer_ownership.py`, `test_order_*`, intégrations checkpoint | local C ; SaaS principal vérifié + tenant sur toute la chaîne |
| GET `/orders` | C + filtre propriétaire | customer signé | liste et recherche de ses commandes/événements | filtre repository customer ; pas de groupe tenant | `listCustomerOrders`, `client.ts:25` | `test_list_customer_orders.py`, `test_pagination_totals.py`, ownership | conserver ownership ; définir organisation/tenant |
| GET `/orders/{order_id}` | C + filtre propriétaire | customer signé | détail exact ; étranger/absent/unowned => 404 | protection présente, pas de BOLA démontrée | `getCustomerOrder`, `client.ts:58` | `test_get_customer_order.py`, JWT/ownership | conserver refus indistinguable |
| POST `/customer/orders/{order_id}/cancel` | C + propriétaire dans use case/repository | customer signé | annulation légale et restitution atomique éventuelle | filtre propriétaire dans mutation ; refus étranger | `cancelOrder`, `client.ts:64` | `test_customer_cancel_order.py`, Mongo transaction | conserver exact identity/idempotence/stock |
| PATCH `/orders/{order_id}/status` | T global | aucun utilisateur ni tenant | statut et événements, éventuelle annulation | opérateur global assumé ; inadapté à un admin client | aucun | `test_backoffice_auth.py`, `test_order_status_api.py` | technique local ; SaaS désactiver T, permissions explicites |
| POST `/orders/{order_id}/cancel` | T global | aucun utilisateur ni tenant | annulation/restock globaux | idem | aucun | mêmes + cancellation | idem |
| GET `/products` | P | aucun | catalogue, prix et stock globaux ; filtre/pagination | données de tous visibles si base partagée multi-tenant | `listProducts`, `client.ts:88`, `public:true` | `test_products_api.py`, filters/pagination | public volontaire local ; SaaS authentifier et partitionner |
| GET `/products/{sku}` | P | aucun | lookup exact SKU, prix/stock globaux | idem ; SKU global | `getProduct`, `client.ts:103`, `public:true` | `test_products_api.py` | idem |
| POST `/backoffice/session/login` | mot de passe vérifié + Origin exacte + header login CSRF | username admin configuré ; aucun tenant | crée/rotate session mémoire | accès admin global si credential acquis | `adminLogin`, `api/admin.ts:95` | `test_admin_sessions.py` | local existant ; SaaS OIDC/session persistante ou fermeture explicite |
| GET `/backoffice/session/me` | S | username admin ; aucun tenant | identité et token CSRF de sa session | pas de données métier ; scope global | `adminMe`, `admin.ts:90` | admin sessions | conserver no-store ; session partagée durable requise |
| POST `/backoffice/session/logout` | M | idem | supprime session mémoire + cookie | révocation seulement processus courant | `adminLogout`, `admin.ts:103` | admin sessions | révocation cohérente multi-instance |
| GET `/backoffice/orders` | R global | username admin ou token technique ; aucun tenant | commandes/clients/événements globaux | global volontaire, pas autorisation tenant | `listAdminOrders`, `admin.ts:109` | `test_backoffice_orders.py`, auth, pagination | SaaS fermer jusqu’à principal admin/tenant |
| GET `/backoffice/orders/{order_id}` | R global | idem | détail global | idem | `getAdminOrder`, `admin.ts:166` | backoffice orders/auth | idem |
| PATCH `/backoffice/orders/{order_id}/status` | M global | username admin ; aucun tenant | statut/événements globaux | idem | `updateAdminOrderStatus`, `admin.ts:183` | `test_backoffice_mutations.py` | idem ; conserver CSRF |
| POST `/backoffice/orders/{order_id}/cancel` | M global | idem | annulation/restock globaux | idem | `cancelAdminOrder`, `admin.ts:189` | backoffice mutations | idem |
| GET `/backoffice/dashboard` | S! global | idem | agrégats commandes/produits/stock, 5 derniers ordres/mouvements | global volontaire | `getAdminDashboard`, `admin.ts:128` | `test_backoffice_dashboard.py` | dashboard admin tenant ou contrôle opérateur distinct |
| GET `/backoffice/products` | R global | username admin ou token ; aucun tenant | catalogue/stock global | global | `listAdminProducts`, `admin.ts:210` | `test_backoffice_products.py` | SaaS scope tenant partout |
| GET `/backoffice/products/{sku}` | R global | idem | détail exact SKU global | global | `getAdminProduct`, `admin.ts:225` | backoffice products | idem |
| POST `/backoffice/products` | M global | username admin ; aucun tenant | crée métadonnées, stock initial 0 | écrit catalogue global | `createAdminProduct`, `admin.ts:231` | backoffice products + product index | idem ; index unique SKU existant à planifier pour tenants |
| PATCH `/backoffice/products/{sku}` | M global | idem | modifie name/category/price, préserve identité et stock | écrit catalogue global | `updateAdminProduct`, `admin.ts:241` | backoffice products | idem |
| POST `/backoffice/products/import/preview` | M global | idem ; liaison à csrf session | stream CSV borné, lectures conflits SKU, aperçu signé lié session/fichier | base globale ; liaison session déjà présente | `previewAdminProductCsv`, `admin.ts:351` | `test_product_csv_import.py`, Mongo import integration | conserver limites/confirmation, ajouter scope tenant avant SaaS |
| POST `/backoffice/products/import/execute` | M + liaison aperçu/fichier/session + confirmation CREATE_ONLY | idem | transaction produits/receipt, idempotence opération | base et operation_id globaux | `executeAdminProductCsv`, `admin.ts:377` | mêmes | idem ; aucune migration automatique |
| GET `/backoffice/products/import/batches/{operation_id}` | S! global | idem | résultat receipt global par opération | résultat pas limité au tenant/session d’origine | `getAdminProductCsvBatch`, `admin.ts:387` | mêmes | SaaS filtre tenant requis |
| POST `/backoffice/products/{sku}/stock-adjustments` | M global | idem | stock + mouvement transactionnels/idempotents | base globale | `adjustAdminProductStock`, `admin.ts:282` | `test_stock_adjustments.py`, Mongo integration | SaaS filtre tenant jusque transaction |
| GET `/backoffice/products/{sku}/stock-movements` | R global | username ou token ; aucun tenant | historique mouvements SKU global | base globale | `listAdminStockMovements`, `admin.ts:315` | mêmes | idem |
| GET `/backoffice/stock-adjustments/{operation_id}` | S! global | username admin ; aucun tenant | mouvement par UUIDv4 global | global | `getAdminStockAdjustment`, `admin.ts:301` | mêmes | idem |
| GET/HEAD `/openapi.json` | P | aucun | descriptions de tous contrats, aucun secret réel | reconnaissance des routes admin | aucun | `test_backoffice_auth.py:274` | défaut FastAPI (`main.py:104`) ; désactiver ou protéger en SaaS |
| GET/HEAD `/docs` | P | aucun | Swagger HTML/CDN, aucun métier | reconnaissance | aucun | contrôle runtime de cet audit | local utile ; désactiver en SaaS |
| GET/HEAD `/docs/oauth2-redirect` | P | aucun | callback Swagger statique | limité | aucun | non exécuté séparément | même politique docs |
| GET/HEAD `/redoc` | P | aucun | ReDoc HTML/CDN | reconnaissance | aucun | non exécuté séparément | même politique docs |

Il n’existe pas d’endpoint HTTP distinct pour préparer/confirmer une commande, consulter/modifier les conversations ou l’historique de chat, lancer une recherche vectorielle, ou lire un diagnostic DB détaillé : ces opérations passent par `/assistant` ou restent internes. L’historique exposé concerne commandes et mouvements. Aucun WebSocket, route montée additionnelle ou autre méthode API n’a été trouvé par recherche de tous les décorateurs/router/mount dans `src`.

## Identité et persistance

`assistant.py:21-41` construit une clé SHA-256 à partir de l’encodage non ambigu `[customer_id,thread_id]`. Une même chaîne thread ne mélange donc pas deux customers. Les checkpoints bruts historiques ne sont pas repris. La préparation retient le propriétaire ; les confirmations et replays vérifient celui-ci. Les garanties business doivent rester inchangées.

`order_repository.py:133-147` filtre les listes/détails customer ; l’annulation inclut le propriétaire dans le filtre transactionnel (`:169-175`). Les méthodes de liste admin et les ports produits restent globaux. `Order.customer_id` est optionnel pour lecture historique ; aucun `tenant_id` dans les modèles/collections/DTO/JWT/session ni le SQL RAG. Une conversion customer->tenant serait une décision de modèle, pas un simple renommage.

Frontend : JWT stocké uniquement dans un `ref` Vue (`utils/customerSession.ts:3-7`), jamais localStorage. `api/http.ts:18-33` omet les cookies et interdit les redirections ; Authorization ajouté aux routes non publiques. Les produits utilisent explicitement `public:true`. Admin transport `api/admin.ts:20-43` impose la même origine, `credentials:include`, `cache:no-store`, `redirect:error`, CSRF mémoire ; erreurs 401 invalident ce CSRF. Pas de refonte UI justifiée. Aucun appel frontend `/chat` ou `/classify` ; usages techniques/tests ne justifient pas l’exposition SaaS.

## Constats HTTP et déploiement priorisés

1. **P0 confirmé, contrat public incompatible SaaS** : `/chat`, `/classify` consomment Ollama sans identité ; `/products` révèle stock/prix global. Preuve : routes et tests existants + reproduction TestClient, use cases mockés, sans DB ni Ollama. Pas de démonstration d’exploitation publique de v017.
2. **P0 architecture confirmée** : absence de tenant pour produits/RAG/admin ; customer ownership existant à conserver. Le produit n’est pas multi-tenant à cette baseline.
3. **P1 confirmé** : `ChatRequest.message` et `AssistantRequest.thread_id` sans bornes (`schemas/chat.py:4-9`). Une chaîne JSON de 2 MiB est acceptée par `/chat` mocké, sans appel infrastructure. Import CSV a déjà une limite stream réelle 2 MiB défaut, maximum 16 MiB ; 500 lignes et validation métier. Ne pas supprimer ces protections.
4. **P1 confirmé** : sessions/rate login par processus `admin_auth.py:43-74`. Un deuxième worker ne connaît ni sessions ni révocations/tentatives ; redémarrage invalide toutes les sessions. Cookies/CSRF/rotation/TTL existants sont des mesures effectives, pas des lacunes.
5. **P1 confirmé dans les artefacts Git, impact déploiement à vérifier** : aucun TrustedHostMiddleware ; Host arbitraire obtient 200 sur health. `docker/web.conf.template:1-13` est catch-all `server_name _`; proxy transmet `$host`. À corriger avec allowlist explicite production, sans croire `X-Forwarded-Host` fourni par le client.
6. **P1 confirmé dans les artefacts Git** : aucun CSP/nosniff/referrer/frame policy dans le web template, aucun headers API hors Cache-Control/Pragma admin (`main.py:208-214`). `/orders` et `/assistant` n’imposent pas no-store côté serveur. Frontend no-store ne protège pas d’autres clients/proxies. Fix minimal indépendant justifié.
7. **Mesure présente** : absence de CORS middleware = aucune autorisation navigateur cross-origin ; préflight cross-origin retourne 405 sans ACAO. Ce n’est pas une configuration CORS permissive ni une preuve de CSRF. CSRF admin déjà protégé. SaaS same-origin suffit par défaut ; ajouter CORS uniquement si besoin explicite avec origines exactes.
8. **Hypothèse non vérifiée** : TLS proxy SaaS hors dépôt. Docker API écoute HTTP 0.0.0.0:8000 derrière proxy attendu. Dockerfile n’active pas reload/debug et tourne non-root. Nginx suivi écoute HTTP 8080, écrase `X-Forwarded-Proto` avec `$scheme` ; un edge TLS externe nécessite un contrat de chaîne explicite pour éviter de perdre HTTPS. `FORWARDED_ALLOW_IPS` n’est pas configuré dans ces artefacts ; ne pas recommander `*` sur un backend directement joignable.
9. **Architecture locale documentée, non vulnérabilité SaaS déployée** : docker-compose.yml est une stack de développement avec Mongo non authentifié, credentials PostgreSQL publics et ports 27017/5432 publiés hors loopback. `docs/getting-started.md:95-100` interdit Internet. Ne jamais reprendre ce compose comme production ; ne pas le confondre avec la DEMO v017 isolée.
10. **P2** : docs FastAPI publiques ; retirer de l’instance SaaS plutôt qu’ajouter un second schéma d’identité. HSTS doit appartenir au véritable edge TLS et rester opt-in une fois domaine/HTTPS/portée validés ; aucune activation localhost automatique. CSP sur SPA de build : scripts/styles self ; aucune directive `v-html`, inline style attribut ou ressource externe de production trouvée. Vite extrait les styles Vue au build ; vérifier l’artefact construit avant de fixer la politique.

## Reproductions et tests

- **PASS** : 167 tests ciblés baseline (`test_backoffice_auth`, `test_customer_jwt`, `test_products_api`, `test_admin_sessions`, `test_health`, `test_api_routes`, `test_publication_configuration`) en 12.61 s ; zéro skip. Python avec `PYTHONPATH=src`, `PYTHONDONTWRITEBYTECODE=1`, logs fichier désactivés, aucun lifespan réel.
- **PASS observations** : TestClient sans lifespan réel/use cases mockés : GET health 200 ; health avec Host inattendu 200 ; OPTIONS products Origin étrangère 405 sans ACAO ; docs/openapi 200 ; chat anonyme 200 ; chat 2 MiB 200 ; products anonyme 200 ; assistant sans config customer 503. Aucun header CSP/HSTS/nosniff/referrer/cache sur ces réponses publiques.
- **NOT EXECUTED** : tests réseau réels/proxy TLS, requêtes sur v017, accès/mutation de DB ou sessions réelles, navigateur CSP, intégrations complètes. Une première sonde d’énumération `app.routes` après les observations a échoué car la version FastAPI possède `_IncludedRouter`; l’inventaire fiable repose sur tous les décorateurs et les include_router, sans ignorer de route.

## Proposition minimale PR E et sources officielles

Nouvelle frontière HTTP séparée de l’auth : host allowlist, fail-closed des routes sensibles SaaS en configuration incomplète, exigence ASGI HTTPS, origines navigateur same-origin par défaut et CORS explicite si nécessaire ; headers API no-store/nosniff/referrer. Le serveur SPA possède CSP et protection framing. Le véritable edge possède HSTS/TLS et contrôle les forwarded headers ; aucune confiance dans leur contenu brut applicatif. `/health` reste une réponse publique minimale pour probes internes, sans diagnostic sensible. Aucune mutation de données, aucun déploiement, aucun changement ORDER.

- [FastAPI — Behind a proxy](https://fastapi.tiangolo.com/advanced/behind-a-proxy/) : les forwarded headers doivent être interprétés uniquement pour les proxies de confiance ; l’origine publique TLS et le préfixe `/api` dépendent du point d’entrée.
- [MDN — Content-Security-Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy) : CSP appartient aux réponses HTML ; l’appliquer aux réponses JSON ne protège pas le document frontend.
- Références locales du skill security-best-practices : FastAPI, Vue, JavaScript frontend. Les recommandations sont adaptées à l’architecture réellement observée, sans nouveau fournisseur d’identité ou Redis imposé.

## V13.7.1 correction

The current code applies require_customer_identity to /products, /products/{sku}, /chat, /classify, and /assistant. Earlier baseline paragraphs describing these routes as public are historical and must not be used as the current runtime contract. The routes still have no organization boundary; V13.7.1 adds no route and does not claim multi-tenant isolation.
