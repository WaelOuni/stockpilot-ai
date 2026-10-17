# V14 — Proposition d’architecture SaaS B2B

Statut : **proposition à discuter, aucune activation autorisée par ce document**.
Référence inspectée : `origin/main` à
`b503af9b2fa3c746e8be05eae562a39b66d3ddbe`, le 17 octobre 2026.
Cette évolution complète la [mini-RFC V13.2](saas-auth-architecture.md) ; elle
ne remplace pas les garanties du [parcours ORDER](../order-flow.md).

## Décision proposée et limites

Conserver `production_saas` et son alias `production` fermés. Les premiers pilotes
doivent être des installations isolées par entreprise, avec un opérateur identifié,
des sauvegardes vérifiées et un périmètre contractuel limité. Une démonstration
locale validée ne prouve ni l’isolation multi-tenant ni la disponibilité de service.

Pour une première version V14 hébergeant plusieurs organisations, proposer un
plan de contrôle commun et des **bases MongoDB et PostgreSQL dédiées par
organisation**, sur des serveurs éventuellement partagés. Cette option réutilise
les transactions MongoDB et le saver LangGraph sans transformer simultanément
leur stockage. Elle exige néanmoins une résolution de contexte fiable, des
identifiants de connexion à privilèges limités, une gestion bornée des connexions
et une validation de capacité. Elle n’isole pas les ressources CPU, mémoire ou
disque d’un serveur commun et ne protège pas contre sa compromission.

La base partagée avec `tenant_id` reste une évolution possible, à décider sur des
mesures de capacité et de coût. Aucun basculement automatique n’est proposé.
Le choix final du modèle d’hébergement, de l’identité et du calendrier de migration
requiert une validation humaine avant les PR d’implémentation.

## Constats prouvés dans la référence

| Surface | État observé | Conséquence V14 |
|---|---|---|
| Composition | `src/stockpilot/main.py` construit un ensemble de repositories pour une base Mongo, un store pgvector et un workflow | Ne pas modifier un singleton global pour sélectionner une organisation par requête |
| Identité client | `presentation/api/auth.py` valide le JWT local et fournit `customer_id` | Cette identité de propriétaire de commande ne prouve aucune appartenance à une organisation |
| Conversation | `presentation/api/routes/assistant.py` dérive la clé SHA-256 de `[customer_id, thread_id]` | Isolation client actuelle à préserver ; ajouter organisation et acteur sans reprendre les anciennes clés |
| Catalogue et stock | `infrastructure/persistence/mongodb/product_repository.py` lit une collection globale ; `domain/entities/product.py` porte `stock` dans le produit | Les recherches, SKU exacts et relectures de faits doivent utiliser exclusivement le store autorisé |
| Commandes | `mongodb/order_repository.py` indexe `order_id` et `operation_id` globalement dans la base ; ownership par `customer_id` | Préserver ownership, idempotence et réservation atomique dans le même périmètre |
| Imports/ajustements | `mongodb/product_import.py` et `stock_adjustment_transaction.py` ont des reçus d’opération dans la base courante | L’aperçu, le reçu et la transaction doivent partager le même contexte serveur |
| RAG | `postgres/pgvector_knowledge_repository.py` cherche sans filtre tenant ; migrations `000`/`001` imposent une unicité globale de `source` | Ne jamais connecter ce port à un corpus multi-organisation non cloisonné |
| Checkpoints | `postgres/checkpointer.py` utilise `AsyncPostgresSaver` et exécute son `setup()` | Protéger toutes les tables du saver, ses lectures, listes, écritures et suppressions ; un préfixe de thread ne constitue pas une ACL |
| Administrateur | `presentation/api/admin_auth.py` fournit un opérateur local ; `mongodb/admin_sessions.py` persiste ses sessions | Ce compte et le bearer technique global ne deviennent pas des administrateurs SaaS |
| SaaS | `presentation/api/saas_boundary.py` renvoie 503 hors santé en environnement production | Garder cette fermeture pendant toute la préparation |

Les chemins de cette table sont relatifs à `src/stockpilot/`, sauf migrations.
Cette inspection est une preuve de structure, pas un audit exhaustif de sécurité
ni une exécution des futurs tests V14. Aucun store existant n’a été migré.

## Modèle métier et identifiants

`customer_id != tenant_id`. L’organisation est le périmètre de données et de
facturation de StockPilot ; le client est un acheteur de cette organisation.
Deux distributeurs peuvent chacun avoir un client portant la même référence
externe. Un utilisateur peut travailler pour plusieurs organisations et acheter
pour un client sans devenir lui-même ce client.

| Entité | Identité et portée proposées | Contrat essentiel |
|---|---|---|
| Organization | `tenant_id` opaque, stable ; nom commercial, état, politique de rétention, référence de stores | Aucun nom de base ou secret fourni par le navigateur ; organisation suspendue refusée |
| User | `user_id` interne ; unicité `(oidc_issuer, oidc_subject)` | L’adresse e-mail est un attribut, pas une clé d’autorisation ou un lien automatique entre comptes |
| Membership | `(tenant_id, user_id)` ; rôle(s), état, version de révocation | Appartenance active contrôlée côté serveur ; invitations et transferts de propriété audités |
| Role | Ensemble explicite de permissions dans une organisation | Rôles métier distincts d’un opérateur de plateforme ; aucune implication « utilisateur connecté = administrateur » |
| Customer | `(tenant_id, customer_id)` ; référence métier et liens utilisateurs autorisés | Ownership de commande supplémentaire au tenant ; choix du client contrôlé pour chaque acteur |
| Product | `(tenant_id, sku)` ; identité documentaire interne stable | SKU exact, métadonnées vérifiées ; recherche approximative Advice bornée, règles ORDER préservées |
| Stock | `(tenant_id, product_document_id)` ; quantité et journal d’ajustements | Modèle conceptuel : conserver initialement `Product.stock`, sans nouvelle collection ni second compteur |
| Order | `(tenant_id, order_id)` ; client, acteur, identité produit, opération, événements | Réservation/restauration atomiques, confirmation explicite, rejeu strict du même payload |
| Conversation | `(tenant_id, conversation_id)` ; acteur, client représenté, règles d’accès | Privée à l’acteur par défaut ; partage d’équipe interdit tant qu’un contrat spécifique n’existe pas |
| Checkpoint | Conversation + séquence/version interne LangGraph | Mêmes autorisations que la conversation ; preuve de confirmation liée au contexte complet |
| Knowledge resource/chunk | `(tenant_id, resource_id, chunk_id)` ; source, révision, modèle/dimension d’embedding | Ingestion et recherche autorisées ; document supprimé ou révoqué exclu du contexte et des caches |

Ne pas ajouter les notions d’entrepôt, de réservation séparée ou de devise multiple
dans le même chantier. Le modèle de stock actuel reste la source de vérité.

### Permissions initiales proposées

| Rôle métier | Permissions proposées | Exclusions importantes |
|---|---|---|
| Propriétaire | Membres et configuration de son organisation, catalogue, stocks, commandes | Aucun accès aux autres organisations ni aux secrets de plateforme |
| Gestionnaire catalogue/stock | Métadonnées, import CREATE_ONLY, ajustements justifiés | Pas de gestion des membres ni d’élévation de rôle |
| Commercial | Catalogue/stock, préparation de commandes pour les clients autorisés, suivi | Agir pour un client demande une permission explicite ; confirmation toujours requise |
| Acheteur client | Catalogue autorisé, ses commandes, ses conversations | Pas de back-office, d’import ou d’ajustement |
| Lecteur | Catalogue et rapports autorisés en lecture | Aucune mutation |

La visibilité des prix par client, le droit d’annuler et le rattachement d’un
utilisateur à plusieurs clients sont des décisions produit à valider. Pour le
premier incrément, utiliser une règle restrictive et testée plutôt qu’une matrice
configurable complexe. Aucun support de plateforme ne reçoit implicitement un
accès aux données : prévoir une procédure distincte, temporaire et auditée.

## Contexte autorisé et frontière d’authentification

Proposer un DTO immuable `AuthorizedContext` fourni par la présentation à
l’application : `tenant_id`, `user_id`, `membership_version`, permissions et,
lorsque nécessaire, `customer_id` autorisé. Un contexte absent ou incomplet est
une erreur ; aucun tenant par défaut et aucune valeur issue du texte LLM.

1. Authentifier par OIDC Authorization Code + PKCE. Valider état anti-CSRF,
   `nonce`, signature, algorithmes permis, issuer, audience et échéances avec une
   bibliothèque maintenue ; gérer la rotation des clés sans accepter un issuer
   arbitraire fourni par le token. L’identité stable est `(iss, sub)` selon
   [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html).
2. Échanger le résultat contre une session applicative opaque côté serveur,
   cookie Secure/HttpOnly/`__Host-` et politique SameSite documentée pour le
   callback. Le callback OIDC peut demander une politique différente du cookie
   admin local strict ; ne pas désactiver CSRF ou Origin pour le contourner.
   Pas de bearer durable dans localStorage ; flux de logout et révocation testés.
3. Si un sélecteur d’organisation est présent, traiter son identifiant uniquement
   comme une demande. Vérifier l’appartenance active côté serveur et lier la
   session/conversation au contexte retenu. Le changement d’organisation invalide
   les références de produits, les aperçus d’import et les confirmations en cours.
4. Résoudre les stores depuis un registre serveur approuvé, jamais une URI ou
   un nom de base de la requête. Ce registre ne contient que des références aux
   secrets. Les secrets restent hors checkpoints, réponses et logs.
5. Distinguer 401 (session invalide), 403 (permission manquante) et 404
   indistinguable pour objet absent/étranger. Une dépendance HTTP ne remplace
   pas le scope dans les ports et dans les transactions.

Conserver les modes locaux et leurs contrats tant qu’ils restent supportés.
Le JWT client local, l’opérateur admin configuré et le token technique ne sont
pas acceptés dans le nouveau périmètre SaaS. Leur coexistence devra être explicite
dans la configuration et les tests ; aucune rétrocompatibilité permissive en
production. Le fournisseur OIDC n’est pas choisi par cette proposition.

## Options d’isolation et choix progressif

| Option | Sécurité et exploitation | Coûts/complexité pour le marché PME | Position proposée |
|---|---|---|---|
| Installation complète par entreprise | Processus, secrets et stores distincts ; erreur de routage limitée ; sauvegarde/restauration simple par entreprise | Plus de déploiements et de mises à jour, ressources parfois sous-utilisées | Pilote contrôlé avant SaaS, avec automatisation reproductible |
| Application commune, bases dédiées par organisation | Identifiants DB limités ; transactions et saver réutilisés ; erreur de registre/pool reste critique | Serveurs partageables, mais catalogue de connexions, migrations N bases et capacité à mesurer | Première cible V14 proposée, sous validation humaine et tests d’évasion |
| Collections/tables partagées avec `tenant_id` | Chaque query, index, agrégat et transaction doit être scoped ; blast radius d’un oubli plus élevé | Moins de pools et de schémas, restauration individuelle plus difficile ; coût de preuve de sécurité important | Option future si mesures et automatisation justifient la complexité |
| Serveurs dédiés par organisation | Frontière opérationnelle plus forte, politique de résidence et maintenance indépendante | Coût fixe plus élevé et opérations multiples | Besoin contractuel spécifique, sans l’imposer à toutes les PME |

« Base dédiée » ne signifie pas « cluster dédié ». Les autorisations du rôle
Mongo/PG de chaque organisation doivent empêcher l’accès aux autres bases ;
un rôle global privilégié dans l’API annulerait une partie du bénéfice. Le plan
de contrôle utilise des accès séparés des données métier. Le choix de deux bases
par organisation ne crée pas une transaction distribuée Mongo/PostgreSQL.

### MongoDB

Préférer des repositories construits avec un contexte vérifié et une référence de
store immuable. Ne pas réaffecter `collection` ou `_database` sur une instance
globale. Une cache de bundles peut être bornée, indexée par tenant + version de
configuration, avec fermeture contrôlée et refus de réutiliser un bundle révoqué.
Le nombre de connexions et les droits interbases doivent être testés avant de
choisir les limites. Une panne de résolution ne doit jamais réutiliser le dernier
tenant chargé.

Ajouter le tenant explicite aux DTOs persistés et preuves d’opération lors de
l’évolution V14, même avec des bases dédiées, et refuser les données dont le
marqueur de store et le tenant ne concordent pas. En base partagée éventuelle,
les clés uniques deviennent au minimum `(tenant_id, sku)`,
`(tenant_id, order_id)` et `(tenant_id, operation_id)` ; les prédicats de
réservation/restock, reçus d’import et pipelines dashboard portent le tenant.
Ce sont des contrats futurs, pas des index à créer dans les bases actuelles.
Un `_id` ou un SKU transmis par un autre tenant ne doit jamais permettre de
charger ou modifier son produit.

### PostgreSQL, checkpoints et pgvector

Pour la cible proposée, un rôle limité et une base par organisation contiennent
RAG et tables LangGraph ; les connexions de migration sont distinctes des
connexions runtime. Examiner le `setup()` actuel avant de retirer des droits DDL :
le futur provisionnement crée/versionne les tables hors requête et le runtime
vérifie seulement leur compatibilité.

Dériver un nouvel espace de conversation versionné depuis un encodage non ambigu
de `[tenant_id, user_id, customer_id, thread_id]`, puis vérifier ces mêmes valeurs
dans l’état et dans la preuve de confirmation. Un hash protège la stabilité de la
clé, pas l’autorisation. Aucune reprise automatique des clés `customer-thread-v1`.
Les anciens checkpoints peuvent contenir des confirmations armées : les conserver
dans leur installation historique ou démarrer une conversation neuve après un
cutover approuvé ; ne pas copier des preuves actives.

Une base de checkpoints partagée serait une décision séparée : il faudrait un
adapter scoped testé pour **toutes** les opérations du saver, les namespaces,
les listes et la suppression, et pas seulement `ainvoke`. Ne pas patcher les
tables internes de la dépendance sans contrat/version et tests de montée de
version. La sérialisation autorisée existante reste restrictive.

Pour un RAG partagé éventuel : colonne tenant NOT NULL, unicité de source dans le
tenant, filtres SQL explicites avant classement et défense RLS sous rôle non
propriétaire sans BYPASSRLS. Les propriétaires et superusers contournent normalement
RLS selon la [documentation PostgreSQL 16](https://www.postgresql.org/docs/16/ddl-rowsecurity.html) ;
une politique déclarée ne suffit donc pas à prouver l’isolation. Tester le contexte
de connexion lors du pooling, les transactions, les erreurs et la remise au pool.
Les résultats et caches d’embedding incluent tenant + révision + modèle. Le RAG
ne fournit jamais un prix/stock à la place de la vérification Mongo.

### Imports, tâches et sessions

Les aperçus CSV sont liés à tenant, acteur, session, contenu et opération ; exécuter
et relire un reçu requiert ce même contexte autorisé. Conserver CREATE_ONLY,
limites de taille/cardinalité, confirmation, CSRF et idempotence. Les jobs différés
transportent un identifiant de contexte signé ou une référence serveur, puis
revérifient l’autorisation à l’exécution ; ils ne chargent pas un tenant global.

Les sessions et memberships appartiennent au plan de contrôle. Révocation,
suspension d’organisation et changement de rôle doivent affecter toutes les
instances avec une politique de cohérence/expiration explicite. Une confirmation
préparée avant révocation doit être refusée. Préserver la séparation des sessions
opérateur locales, des sessions SaaS et d’un éventuel accès support.

### Quotas et observabilité

Garder les limites HTTP/LLM V13.2 et ajouter admission par tenant ET acteur,
concurrence bornée et budget d’usage sur une coordination partagée. Le limiteur
local ne prouve pas un quota distribué. Mesurer les refus et la consommation sans
laisser une organisation monopoliser Ollama ou bloquer les autres. Définir la
politique en cas de panne du compteur : fail-closed pour l’admission payante ou
risquée ; aucune commande confirmée perdue dans un rejeu mal géré.

Logs et traces : identifiant tenant opaque, acteur pseudonymisé, request/operation
IDs et décisions d’autorisation ; jamais JWT, cookies, contenu client libre ou
secrets. Restreindre l’accès aux logs et exports. Éviter les labels métriques non
bornés par client/SKU ; garder les dimensions détaillées dans des traces protégées.
Définir rétention, effacement et alertes par organisation sans prétendre que
l’observabilité actuelle constitue une comptabilité de facturation.

## Migration, sauvegarde et rollback proposés

1. Inventaire en lecture seule sur une copie autorisée : données, index,
   identifiants, reçus d’opération, checkpoints, corpus et dépendances.
2. Mapping explicite et approuvé de chaque donnée vers l’entreprise propriétaire.
   Les lignes sans propriétaire ou ambiguës bloquent le lot. Aucun tenant par
   défaut et aucune inférence depuis `customer_id` seul.
3. Définir puis appliquer le gel des écritures et drainer les opérations en vol
   avant tout snapshot ; aucune transaction distribuée ne garantit à elle seule
   la cohérence entre Mongo et PostgreSQL. Un mécanisme sans gel exige une preuve
   distincte de cohérence et de réconciliation avant utilisation.
4. Sous ce gel, produire un snapshot apparié Mongo + PostgreSQL, répéter sa
   restauration et conserver le journal de correspondances. Les sauvegardes ont
   des droits distincts du runtime. Provisionner de nouvelles bases ; copier hors
   ligne sans modifier les sources ; valider comptes, empreintes, index et
   invariants stock/ordre avant toute décision de cutover.
5. Démarrer de nouveaux threads et désarmer les anciennes préparations selon la
   procédure approuvée. Conserver les identités d’opération pour éviter les doublons
   métier ; vérifier l’historique de réservation et d’annulation.
6. Bascule humaine, surveillance puis procédure de rollback testée. Après de
   nouvelles écritures, restaurer une ancienne sauvegarde peut perdre des commandes :
   geler et réconcilier explicitement ; ne jamais fusionner deux historiques de
   stock automatiquement. Aucun dual-write Mongo/PG n’est introduit ici.

L’effacement d’une organisation inclut bases métier, checkpoints, corpus, exports,
caches et la politique de purge des sauvegardes. Les obligations légales de
conservation et de données personnelles nécessitent une validation adaptée au
pilote ; ce document n’est pas un avis juridique ni une certification de conformité.

## Plan de PR futures et critères de passage

Chaque PR cible `main`, avec revue indépendante, tests isolés et compatibilité des
modes locaux démontrée. Les chemins futurs sont des propositions. Aucune PR de
cette liste n’est implémentée par le présent document. Les changements ORDER et
d’authentification nécessitent une autorisation explicite de leur contrat.

| PR | Objectif, prérequis et fichiers principaux | Contrats / migrations | Tests de sécurité et acceptation | Risques / gate |
|---|---|---|---|---|
| V14.01 — contrats | Après validation du modèle : `application/dto/authorized_context.py` (nouveau), domaine organisation/membership, `application/ports/` | Contexte requis, permissions explicites, identité acheteur séparée ; aucun store existant modifié | Tests de contexte absent, membership inactif, sélection client interdite ; imports sans contexte impossibles dans le nouveau chemin | Ne pas fabriquer de tenant par défaut ; revue humaine du modèle |
| V14.02 — preuve d’isolation | Après .01 : fixtures dédiées `tests/`, prototypes hors runtime, benchmark de bundles | Deux organisations avec mêmes SKU/customer/thread/operation ; aucun contrat HTTP activé | Fuzzing d’identifiants, concurrence A/B, mauvais rôle DB, mauvaise sélection de store ; modèle de pooling mesuré | Arbitrage documenté bases dédiées/partagées avant implémentation |
| V14.03 — identité et plan de contrôle | Après .02 + choix humain OIDC : `presentation/api/auth.py`, nouveau module identité/session, `config/settings.py`, futur registre memberships | Session serveur OIDC, révocation/version, rôles ; nouvelles collections explicitement provisionnées ; modes locaux conservés | Faux issuer/aud/sub, clé tournée, nonce/state/PKCE erronés, CSRF, session expirée, membership révoqué sur deux instances | Contrat auth majeur : PR ouverte pour validation humaine ; SaaS reste fermé |
| V14.04 — stores et lectures | Après .03 : `main.py`, `presentation/api/dependencies.py`, ports produit/commande/knowledge et adapters Mongo/PG | Bundles immuables bornés ; registre serveur, tenant obligatoire ; provisionnement de nouvelles bases par commande distincte | A/B mêmes SKU/source ; catalogue, recherche, Advice, dashboard, pagination, agrégats et RAG ; refus du store manquant ou d’un marqueur discordant ; rôle incapable de lire autre DB | Ne pas muter les singletons ; lecture seule ne vaut pas autorisation de mutation |
| V14.05 — écritures transactionnelles | Après .04 : `application/order_idempotency.py`, ports transaction, adapters `order_transaction.py`, `stock_adjustment_transaction.py`, `product_import.py` et index | Tenant inclus dans payload/proof/replay ; droits client conservés ; nouveaux index provisionnés hors startup ; aucun backfill automatique | Deux tenants mêmes opérations ; réservation concurrente, commit incertain, rejeu/annulation, CSV autre session/tenant, ajustement, exact SKU/_id, quantités invalides | Intégrité ORDER critique ; revue spécialisée et autorisation humaine avant merge |
| V14.06 — conversations | Après .05 : route assistant, `workflows/state.py`, `order_confirmation.py`, `resolve_context.py`, `postgres/checkpointer.py` | Nouvelle clé versionnée tenant/acteur/client/thread, proof bound ; saver par store ; conversations privées | Reprise après restart, A/B même thread, changement d’organisation, révocation entre préparation/confirmation, état ancien désarmé, requêtes concurrentes ; toutes opérations saver scoped | Aucun remplacement silencieux du schéma LangGraph ; pas de migration des preuves actives |
| V14.07 — UI et administration | Après .06 : `frontend/src/` identité/contexte, API admin et import | Sélecteur vérifié, écrans selon permissions, purge du contexte UI au changement ; aucun privilège transmis par UI | E2E isolé deux organisations/deux utilisateurs, tentative d’URL directe, cartes/commande/preview périmés refusés, logout/refresh | UX ne doit jamais être la seule barrière ; contrat API documenté |
| V14.08 — exploitation et quotas | Après .07 : `input_protection.py`, admission LLM, observabilité, runbooks, provisioning/migrations explicites | Quotas coordonnés, secrets/pools/rotation, sauvegarde-restauration, rétention et suppression ; tests de charge | Deux instances, noisy neighbor, panne compteur/DB/LLM, rotation clés/DB, fuite dans logs/export, restauration par tenant | Mesurer RPO/RTO et capacité avant de promettre un SLA |
| V14.09 — répétition de migration | Après .08 + mapping approuvé : scripts dédiés et `docs/validation/` | Dry-run sur copies autorisées, snapshots appariés, procédure humaine de cutover/rollback | Comptages/empreintes, opérations en vol, stocks et reçus réconciliés, preuve d’accès croisé impossible | Aucune migration de DEV/DEMO/clients sans autorisation spécifique |
| V14.10 — décision d’activation | Après toutes preuves : modification séparée de `saas_boundary.py` et configuration de déploiement | Retrait de la fermeture seulement pour la configuration explicitement prête ; aucun bypass général | Tests HTTP/HTTPS/Host/Origin/CSP/CSRF, authz exhaustif, SaaS incomplet toujours 503, rapport indépendant sans P0/P1 ouvert | **Validation humaine explicite avant merge et déploiement ; aucune activation automatique** |

## Matrice minimale de preuve de non-fuite

Chaque test utilise des stores explicitement jetables et deux tenants A/B dont les
identifiants métier sont identiques, mais dont les valeurs confidentielles diffèrent.

| Axe | Assertion requise |
|---|---|
| Catalogue/stock | Liste, fiche, résolution, tri, budget, alternatives et relecture finale n’observent que A |
| Commandes | Propriétaire, identité exacte, validation lexicale de quantité, preuve persistée/désarmée, rejeu et annulation restent exacts dans A ; B inchangé |
| Conversations | Même customer/thread chez A/B, autre utilisateur chez A, changement d’organisation et nouveau thread ne réutilisent aucune référence étrangère |
| Imports/ajustements | Preview/reçu/operation_id de B inexploitable depuis A, y compris après reconnexion, concurrence et reprise |
| RAG | Recherche, ingestion, suppression, source identique et cache ne mélangent pas les corpus ; pas de fallback catalogue inventé |
| Auth/session | Rôle insuffisant, suspension, expiry et révocation entre préparation et confirmation bloquent sans mutation |
| Infra | Mauvais store, credentials d’un autre tenant, bundle expiré, erreur de pool et indisponibilité refusent plutôt que réutiliser un contexte précédent |
| Opérations | Export, dashboard, métriques/logs et sauvegarde/restauration ne donnent pas un accès transversal implicite |

Réutiliser les garanties existantes de `test_customer_ownership.py`,
`test_customer_jwt.py`, `test_saas_boundary.py`, `test_catalogue_advice.py`,
`test_product_csv_import.py`, `test_stock_adjustments.py`, ainsi que les suites
Mongo transaction et PostgreSQL checkpoints. Les compléter avec les contextes
tenant ; leur succès historique ne valide pas les futures fonctionnalités V14.

## Décisions humaines attendues

1. Valider le premier cas métier : personnel interne uniquement ou portail acheteur ;
   responsabilité de création/confirmation des commandes et visibilité des clients.
2. Valider le pilote isolé, l’hébergement et les engagements opérationnels ; accepter
   ou amender la proposition de bases dédiées pour la première V14.
3. Choisir un fournisseur/serveur OIDC après évaluation fonctionnelle et de coûts,
   sans dépendance commerciale décidée par ce document.
4. Approuver les rôles, invitations/révocations, accès support, rétention,
   sauvegardes et responsabilités d’exploitation.
5. Autoriser séparément les contrats auth/ORDER, migrations et décision d’activation
   SaaS, sur la base des preuves de tests et d’une revue indépendante.

La prochaine étape proposée est une revue de ces cinq décisions, puis V14.01 et
V14.02 sans ouverture du mode SaaS. Aucun changement des DEMO ou des données
persistantes ne découle de la publication de cette proposition.
