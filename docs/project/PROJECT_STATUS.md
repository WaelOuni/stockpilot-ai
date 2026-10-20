# StockPilot AI — état du projet

Snapshot documentaire : **20 octobre 2026**, après vérification GitHub et fetch de
`origin/main` à `85df5c42e9188352623f11a5c12e639fb2c98b9d`.
Ce SHA est une preuve datée, pas une référence permanente. Retrouver le courant :
`git fetch origin main && git rev-parse origin/main`. La branche par défaut est
`dev` ; ce programme suit **main**, sans intégrer le chantier V11 de dev.

## Produit et architecture actuels

Assistant local français pour catalogue d'outillage, conseil, disponibilité et
commandes confirmées. Application de développement/démonstration, pas un service
de commerce prêt pour la production publique.

Vue 3/TypeScript → FastAPI/Pydantic → LangGraph → cas d'usage et ports :
MongoDB (produits, stocks, commandes et transactions), PostgreSQL (checkpoints
persistants), pgvector (RAG documentaire), Ollama (classification/embeddings et
génération bornée). Python 3.12 ; Qwen `qwen2.5:7b` et `nomic-embed-text`
sont les modèles documentés. Voir [architecture](../architecture.md) et
[décisions](ARCHITECTURE_DECISIONS.md).

Dans le code fusionné : catalogue et fiches SKU, sélection Advice déterministe,
comparaison et suivis de critères, historique visuel Advice, vérification des faits
Mongo, stock, préparation/confirmation ORDER, statut et annulation autorisée.
Back-office local : sessions, catalogue, ajustements tracés, dashboard et imports
CSV CREATE_ONLY après aperçu, sous réserve de configuration/index provisionnés.
Voir [Advice](../catalogue-advice.md), [ORDER](../order-flow.md),
[données/imports](../database.md) et [produit](../product/positionnement.md).

## Trois états à ne pas confondre

| Périmètre | Preuve disponible | Portée / incertitude |
|---|---|---|
| Stable **v0.1.7**, installation dite **v017** | Tag GitHub vérifié, commit `9d9787f03c5b0448545a78cc780ebb5b04ac6af1` | Aucune nouvelle release publiée par ce chantier ; état vivant de l'installation non inspecté |
| DEMO candidate **V13.3** | [Validation initiale](../validation/v13.3-integrated-candidate.md) : application/image `b503af9b2fa3c746e8be05eae562a39b66d3ddbe` | Preuve historique du 17 octobre ; **commit actuellement déployé non confirmé**. Les worktrees de mise à jour V13.3.2/V13.4.1 ne prouvent pas un déploiement |
| Code intégré à **main** | PR #36–#39 fusionnées sur GitHub au snapshot | Une fusion ne reconstruit ni ne promeut automatiquement une installation ; #39 ne prouve pas sa présence dans la candidate |

Installations protégées : `/home/waelko/deployments/stockpilot-demo-v017/` et
`/home/waelko/deployments/stockpilot-candidate-v133/`. Aucun accès, test ou cycle de
vie sur ces installations sans autorisation explicite. Ne pas supprimer les
worktrees potentiellement liés aux scripts déployés.

## Derniers chantiers intégrés

| PR vers main | Livraison | Preuve détaillée |
|---|---|---|
| [#33](https://github.com/WaelOuni/stockpilot-ai/pull/33) | Outillage et validation candidate V13.3 | [Rapport candidat](../validation/v13.3-integrated-candidate.md) |
| [#34](https://github.com/WaelOuni/stockpilot-ai/pull/34), [#35](https://github.com/WaelOuni/stockpilot-ai/pull/35) | Explication préparation sans réservation ; isolation E2E renforcée | [Livraison consolidée](../reports/stockpilot-autonomous-delivery-report.md) |
| [#36](https://github.com/WaelOuni/stockpilot-ai/pull/36) | Proposition V14 et préparation commerciale | [V14](../security/v14-saas-design.md), [produit](../product/README.md) ; propositions, aucune activation |
| [#37](https://github.com/WaelOuni/stockpilot-ai/pull/37) | Préfixe « Demander conseil sur » | [V13.3.1](../validation/v13.3.1-advice-prefix-hotfix.md) |
| [#38](https://github.com/WaelOuni/stockpilot-ai/pull/38) | Historique Advice et présentation conversationnelle | [V13.4](../validation/v13.4-conversation-ux.md) |
| [#39](https://github.com/WaelOuni/stockpilot-ai/pull/39) | Recherches naturelles et budgets stricts | [V13.4.2](../validation/v13.4.2-natural-search.md) |

Preuves historiques #39 : 2 482 tests backend, 210 intégrations, 428 frontend,
122 évaluations déterministes, 11 scénarios navigateur synthétiques réussis ;
CI finale verte consignée dans la PR. Ce ne sont pas des tests rejoués par V13.5,
ni une mesure d'Ollama réel. Les rapports conservent désélections et limites.

## Consolidation vérifiée le 20 octobre 2026

- PR [#40](https://github.com/WaelOuni/stockpilot-ai/pull/40) (mémoire V13.5)
  et [#41](https://github.com/WaelOuni/stockpilot-ai/pull/41) fusionnées ; CI de main
  `85df5c4` réussie. La PR #42 (kit pilote) est encore ouverte à ce relevé.
- [V13.4.3](../validation/v13.4.3-real-user-journey.md) : parcours réel ciblé
  réussi sur `85df5c4`, 85 assertions, zéro erreur Vue ; preuves locales relues,
  campagne non rejouée par la consolidation. Ce n'est ni un test de charge ni une
  garantie de compréhension générale ou de devise/TVA.
- [V13.6](../reports/v13.6-local-consolidation.md) : audit, nettoyage limité et
  diagnostic stockage terminés. Ils n'ont promu aucune image ou installation.
- Trois démos séparées : v017 sur **5191**, candidate sur **5193**, historique
  sur **5175/8003**. La candidate exécute web/API `79368b1316b0` et relais API
  `c760d407713c` selon l'inspection Docker ; ses scripts restent montés depuis
  `/home/waelko/projects/stockpilot-v133-validation/`.
  Le checkout principal historique reste volontairement à `6f09496` : ne pas le
  synchroniser pour mettre GitHub à jour. Les lignes du tableau précédent sont
  des preuves historiques ; cette inspection ne vaut pas nouvelle validation live.
- Prochaine priorité décidée pour la préparation : **V13.7, audit d'authentification
  B2B** ; contrats d'identité/organisation, autorisations et isolation à vérifier
  avant implémentation. Préparer ensuite un environnement de test distant isolé.
  Kubernetes/K3s reste envisagé, sans installation ni choix d'architecture approuvé.

## SaaS, limites et décisions en attente

**Multi-tenant non livré** : `customer_id` désigne un propriétaire client, pas
`tenant_id`. Modèles et permissions B2B présents ; OIDC/BFF limité au mode de test.
Pas de stores métier multi-tenant opérationnels.
`production_saas` et l'alias `production` restent fail-closed.
[V13.2](../security/saas-auth-architecture.md) décrit la frontière actuelle ;
[V14](../security/v14-saas-design.md) propose des évolutions à approuver.

Limites connues : grammaire Advice bornée et extraction probabiliste ; petit corpus
historique ; aucune devise/TVA garantie dans le modèle ; historique Vue en mémoire.
PyMongo synchrone, concurrence de requêtes d'un même thread non sérialisée,
transactions Mongo et checkpoints PostgreSQL non distribués ; bases mono-nœud.
`/health` mesure la vie du processus, pas la disponibilité des dépendances ;
le rapport candidat décrit une reprise API nécessaire après certains redémarrages PG.

À décider : validation réelle des nouvelles formulations avec Ollama sur environnement
isolé autorisé, préparation de démonstration, preuve de restauration avant données
client, périmètre/hébergement/identité du pilote et V14, responsabilités et budget.
Le [durcissement HMAC des sessions](../security/admin-session-fingerprint-review.md)
reste proposé, avec impact sur configuration et sessions ; aucun incident pratique
DB-only démontré ni migration approuvée. Aucun client pilote ou gain commercial
mesuré n'est attesté dans les documents inspectés.

Priorités et critères : [roadmap](ROADMAP.md). Reprise : [handoff](AGENT_HANDOFF.md).
Les rapports anciens restent des snapshots ; en cas d'écart, conserver leur date et
ajouter la preuve récente plutôt que réécrire l'histoire.

## V13.7.1 — fondations B2B fusionnées

PR [#44](https://github.com/WaelOuni/stockpilot-ai/pull/44), base `3eba881` :
User, Organization, Membership et Principal immuables, résolution humaine par port
serveur de confiance, permissions et portée organisation/propriétaire vérifiées.
[Contrats et limites](../security/b2b-identity-foundations.md).
Keycloak auto-hébergé, PKCE S256 et BFF FastAPI sont le choix validé pour la suite.
Aucune route, persistance, session BFF ou isolation multi-tenant activée ici.
`customer_id`, JWT historique, sessions admin, ORDER et fermeture SaaS restent inchangés.
PR #44 fusionnée : commit de fusion `eecf8a40198d04084ff0c221cea2eec53d4b72b7`,
vérifié le 20 octobre 2026 ; distinct du HEAD de PR `97ca2d2`.
Les installations de démonstration ne sont pas mises à jour.

## V13.7.2 — OIDC/BFF fusionné, mode de test uniquement

[Guide et limites](../security/keycloak-bff.md) : entrypoint FastAPI séparé,
Keycloak 26.8.1, Authorization Code/PKCE S256, sessions MongoDB partagées,
cookies HTTPS et provisioning explicite. Mode désactivé par défaut ;
`b2b_auth_test` expose uniquement authentification/session, aucun endpoint métier.
Pas de refresh token conservé ; session plafonnée par l'expiration de l'ID token.
Ni isolation multi-tenant ni exposition Internet validées ; aucun déploiement.
La PR #42 (kit pilote) reste ouverte au relevé V13.7.2, sans intégration implicite.
PR [#45](https://github.com/WaelOuni/stockpilot-ai/pull/45) fusionnée à
`80da436e455c48afc5db15c8feaad950960e604f`, vérifiée le 20 octobre 2026 ;
son HEAD de branche `26c68f9` est distinct. Aucun déploiement n'en découle.

## V13.7.3-A — frontière tenant fusionnée

[ADR et matrice de ressources](../security/tenant-authorization-boundary.md) :
TenantContext immuable lié à la session validée, revalidation du Principal,
opération/permission/type/ownership et association acheteur explicite.
Décisions ALLOW/DENY/NOT_FOUND/INDETERMINATE ; aucun accès global de secours.
Contrats et tests uniquement : aucune migration, collection métier, route ou
activation des repositories tenant. Les lectures RAG/conversations/checkpoints,
la création B2B et le rejeu d'idempotence restent refusés dans cette politique.
Leur isolation réelle et l'atomicité vérification/écriture relèvent de V13.7.3-B.

PR #46 fusionnée à `b8fb6ee229ed64de5e00881d43c66ff79e4a1f24`, vérifiée le
21 octobre 2026 ; distincte du HEAD de branche `cb778eb`.

## V13.7.3-B1 — catalogue/stock isolés, sans activation métier

[ADR](../security/tenant-catalogue-stock.md) : collection `b2b_products`, stock
embarqué, organisation obligatoire, unicité `(organization_id, sku)` et adapters
de lecture explicitement scoped. [Migrations versionnées](../mongodb-migrations.md)
avec historique, checksums, verrou sans takeover et gate de version ; apply limité
aux cibles jetables explicitement identifiées. Aucun backfill ni migration DEMO.
[Preuves et limites](../validation/v13.7.3b1-tenant-catalogue-stock.md).
Le BFF n'expose aucun métier. ORDER/ADVICE historiques restent sur leurs adapters ;
commandes B2B, RAG et checkpoints ne sont pas isolés par B1. Pas de SaaS opérationnel.
