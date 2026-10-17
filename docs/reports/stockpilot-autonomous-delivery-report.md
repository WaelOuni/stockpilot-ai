# StockPilot — Livraison autonome, 17 octobre 2026

Statut : **PARTIAL**. V13.3 est validée et ses outils publiés. Les livrables SaaS
et commerciaux sont des propositions ; ils n'activent aucune fonctionnalité SaaS,
ne promeuvent aucune release et ne remplacent pas une validation humaine de pilote.
Les états définitifs de PR/CI de cette livraison documentaire figurent dans sa PR.
Ce rapport est consolidé après chaque phase ; aucun résultat en attente ne vaut PASS.

## A — Candidate V13.3 et GitHub

Baseline d'application vérifiée : `b503af9b2fa3c746e8be05eae562a39b66d3ddbe`.
La [PR #33](https://github.com/WaelOuni/stockpilot-ai/pull/33), branche
`validation/v133-integrated-demo` vers **main**, livre les scripts, infra, tests et
rapport. HEAD de livraison `6b632b8264c32bcd1243849241cd50ad87107dd5` ; fusion
`0a343a54d272d81bda5462543495f454bc128606`, effectuée selon l'autorisation de phase A
après revue et les cinq checks obligatoires SUCCESS. Aucun bypass ni force-push.

| Validation locale finale V13.3 | Résultat réellement exécuté |
|---|---|
| Backend | 2 258 PASS, 200 désélectionnés, 59,97 s |
| Mongo/PostgreSQL intégration | 199 PASS, 2 256 désélectionnés, 33,96 s ; services jetables distincts |
| Lifecycle auparavant optionnel | 1 PASS, 29,61 s ; nouvelles instances tmpfs 28027/56432, API/Vue enfants 8003/5175 |
| Évaluations déterministes | 104 cas, aucun échec |
| Frontend | 407 PASS, 23 fichiers ; typecheck, lint, build PASS |
| Playwright avec Ollama réel | 2 PASS, 20,1 s, après préflight d'identité |
| Gardes de candidate | 21 PASS ; compris dans le backend |
| Revue indépendante | Deux P2 corrigés : provenance des builds et masquage des erreurs réseau contenant Authorization ; aucun P0/P1 restant identifié dans ce diff |
| CI PR #33 | Unit Tests, AI Eval Framework, frontend-quality, integration-tests, containers : SUCCESS ; ancien job E2E désactivé, pas présenté comme exécuté |

Les nombres désélectionnés dépendent de l'instant de la collecte : les 199
intégrations ont été exécutées avant les derniers tests purs de lanceur. Leur
exclusion de la commande unitaire ne constitue jamais une validation.

Détails, scénarios A–F, valeurs Mongo, modèles/digests, mesures et limites :
[rapport V13.3](../validation/v13.3-integrated-candidate.md).

### Environnements conservés

- Candidate : `/home/waelko/deployments/stockpilot-candidate-v133/`,
  **https://localhost:5193**, cinq conteneurs sains lors du préflight final.
- Application/image : **b503af9** ; fusion des scripts sans reconstruction ni
  promotion de l'application déployée. Les nouveaux builds utilisent une archive
  immuable du commit du manifeste.
- Mongo et PostgreSQL : `stockpilot_candidate_v133`, propres au projet
  `stockpilot-v133-candidate` ; trois volumes et quatre réseaux dédiés. Seul le web
  TLS est publié, sur `127.0.0.1:5193`.
- 512 produits synthétiques, 2 000 commandes initiales et 3 000 mouvements ; les
  commandes de contrôle annulées restent traçables. L'empreinte produits reste
  `d18a247367c6fdfb98ccb62ac17e5cbc2ca8b810106ece29fb71b8d95b2dc7b1` après les parcours
  ORDER et les validations navigateur. Quatre embeddings de 768 dimensions.
- Ollama Windows partagé, `qwen2.5:7b` et `nomic-embed-text`, sans modification du
  fournisseur ; logs natifs corrélés, aucun déploiement d'observabilité supplémentaire.
- `./status.sh`, `./start.sh`, `./stop.sh` dans le répertoire candidat ; stop
  conserve les volumes. La candidate n'est pas arrêtée pour nettoyage.
- Certificat local auto-signé à accepter ; JWT client privé dans `customer.jwt`,
  login administrateur dans `admin-login.json`. Ne jamais publier ces contenus.
  Renouvellement JWT documenté dans le runbook, jeton/historique UI en mémoire.
- PostgreSQL checkpoints et sessions admin Mongo survivent à une reprise API.
  Un redémarrage PG externe peut laisser le saver fermé : `./start.sh` rouvre
  seulement l'API si PG est plus récent ; `/health` seul n'est pas une readiness DB.
- Les services de tests `stockpilot-v133-tests` et `stockpilot-v133-lifecycle` ont
  été contrôlés puis retirés ; aucun volume candidat supprimé.

DEMO stable v017 : aucune commande de mutation, aucun accès aux données ou secrets,
aucun test navigateur ni redémarrage. IDs de conteneurs, dates de démarrage, états
et ensembles de montages comparés avant/après : inchangés. Ce contrôle de
métadonnées ne prétend pas être un checksum du contenu de ses bases, qui n'a pas
été lu.

## B — Sécurité et qualité résiduelles

L'audit ciblé utilise `security-best-practices`, les références FastAPI/Vue et les
sources Python HMAC/OWASP. Les sessions stockent un SHA256 du tuple canonique de
configuration, qui inclut plusieurs secrets et le vérificateur PBKDF2 salé. Une
base Mongo seule ne contient pas tous ces éléments. Aucune attaque pratique
DB-only démontrée sur la candidate, dont les secrets sont générés indépendamment
avec une forte entropie.

Un oracle de dictionnaire conditionnel est démontré avec des données synthétiques
si les autres éléments sont connus et qu'un secret est faible. Une clé HMAC
indépendante serait un durcissement, avec nouvelle configuration partagée durable
et invalidation des sessions SHA. Cette évolution demande une décision humaine sur
le contrat d'authentification ; aucun remplacement mécanique ni déploiement n'est
réalisé. 52 tests offline de sessions et pannes de store : PASS.

La rotation actuelle refuse les sessions de l'ancienne configuration sans les
effacer ; une restauration exacte peut les réactiver avant expiration. Les
sessions anciennes comptent toujours dans la capacité. Ces comportements doivent
être compris avant toute migration HMAC ou engagement de révocation.

L'ancien initialiseur E2E pouvait déduire des URI DEV et ne vérifiait que le nom de
base. Ce chemin n'a pas été lancé. Un correctif séparé impose des cibles explicites
et une preuve de services jetables avant toute mutation ; son état de livraison
est complété après validation. Le harnais synthétique sans DB/LLM est adapté à la
CI rapide ; la candidate avec Ollama réel reste opt-in, sérialisée et précédée de
son préflight. Les suites lourdes ne sont pas activées indistinctement.

Garanties maintenues : HTTP/Origin/Host/HTTPS, CSP, InputProtection et admission
LLM, JWT, CSRF, sessions persistantes, ownership client et thread, confirmation
persistée/désarmée, idempotence et réservation atomique ORDER. Aucun changement
ORDER, prompts ou modèles ; aucune désactivation du fail-closed SaaS.

## C — Démonstration et UX

Les scénarios navigateur V13.3 valident cartes multiples, fiche ciblée, comparaison,
chargement, erreur JWT, disparition des anciennes cartes, nouvelle conversation,
autre client, préparation/confirmation et session back-office après rafraîchissement.
Le diagnostic complémentaire desktop a réussi **17 assertions sur 16 états**, à
1366×768 : catalogue, navigation, fiche, rupture, historique, back-office et
aperçus CSV. Aucun nouvel appel ORDER/Ollama, aucun import exécuté ; empreinte
produits inchangée. Aucun pageerror ni débordement horizontal ; 404 produit absent
et 401 session absente attendus. Ni mobile ni audit assistif complet exécutés.
Une amélioration P2 de texte de préparation est traitée séparément ; les autres
observations (unité monétaire, saisie JWT technique) restent documentées.

Une proposition de commande est distincte de la confirmation et de la réservation.
Les montants n'ont pas de devise dans le modèle : les euros du jeu synthétique
ne constituent pas un contrat multidevise. La saisie de JWT et la perte de l'état
UI après refresh restent des limites de démonstration, pas une expérience de
connexion commerciale achevée. Aucun redesign spéculatif n'est engagé.

## D — Préparation V14, sans activation

[Proposition V14](../security/v14-saas-design.md) : `customer_id != tenant_id`,
Organization/User/Membership/Role, contexte serveur immuable, permissions, clients,
stores, ORDER, conversations/checkpoints, RAG, imports, sessions, quotas et
observabilité. Comparaison installation dédiée, bases dédiées sur serveurs
partagés et base commune avec tenant_id.

La cible initiale proposée est un pilote isolé puis des bases dédiées par
organisation ; ce choix n'est pas une décision irréversible prise par l'agent.
Dix futures PR avec contrats, tests de non-fuite, dépendances, risques et gates
humaines sont définies. Gel/drain des écritures avant snapshots appariés et
restauration, aucun transfert de confirmations armées. Revue indépendante : aucun
blocage majeur, précision sur cohérence des snapshots intégrée.

Aucun tenant runtime, fournisseur OIDC payant, migration ou ouverture SaaS ajouté.
Les futurs contrats auth/ORDER et toute activation exigent une validation humaine.

## E — Préparation commerciale

[Documents produit](../product/README.md) : positionnement, script de démonstration
8 minutes, onboarding pilote, coûts/pricing hypothétiques et validation prospects.
Sources officielles actuelles citées, calculs séparés des hypothèses. La cible
PME/grossistes/distributeurs de Lille/Hauts-de-France est à valider ; Tunisie en
second temps. Aucun prospect contacté ni abonnement/ressource payante créé.

Aucun avantage sur Excel ou Odoo n'est supposé sans test ; aucun gain client mesuré
inventé. Le scénario matériel client estime 140 €/mois de coût fournisseur pour
un prix hypothétique de 299 €, hors coûts fixes ; l'option GPU dédié illustre un
coût beaucoup plus élevé. Ce sont des hypothèses d'entretien, pas une grille
acceptée par le marché. Licences, sauvegardes/restauration, responsabilités et
support doivent être vérifiés avant une vraie donnée client.

## F — Inventaire et nettoyage conservateur

Inventaire réalisé avec `git worktree list --porcelain`, état individuel, commits
hors main/remotes, PR, cwd de processus, labels et mounts Docker. Aucun reset,
clean, prune, suppression de branche ou suppression de dossier ordinaire.

| Worktrees | Décision / preuve |
|---|---|
| `stockpilot-ai` | Conservé, dépôt principal propre ; branche locale main historique non déplacée |
| `stockpilot-v133-validation` | Conservé obligatoirement : candidate active et scripts bind-mountés |
| `stockpilot-e2e-safety`, `stockpilot-v14-design`, `stockpilot-product-readiness` | Conservés : travaux de cette mission / revue / publication |
| `stockpilot-order-quantity-guard`, `stockpilot-v014-hotfix`, `stockpilot-v1232-conversation-audit`, `stockpilot-v1238-conversation-fix` | Conservés : dossiers reports non suivis, aucun effacement ni archivage supposé |
| `stockpilot-v132-auth-callers`, `stockpilot-v132-io-measurement` | Conservés : chacun un commit non publié détecté |
| `stockpilot-v1321-integration` | Conservé : 20 commits non présents sur les remotes |
| `stockpilot-v132-composed` | Conservé : 20 commits hors main, fin de chantier non démontrée individuellement |
| `stockpilot-v131-ci`, `stockpilot-v131-docs`, `stockpilot-v132-{foundation,http-boundary,input-limits,admin-sessions}`, `stockpilot-v132-final`, `stockpilot-v13c1-advice` | Commits publiés/main et pas de cwd actif observé, mais logs ignorés non archivés ; artefacts de test supplémentaires pour C1. Conservés tant que leur utilité et les anciennes dépendances ne sont pas levées |
| Anciennes entrées `/tmp` absentes | Métadonnées conservées, pas de prune global |
| `/tmp/stockpilot-v125-order-natural` | Dossier existant mais plus worktree Git valide ; conservation obligatoire, aucun rm |

**Aucun worktree supprimé ; espace récupéré : 0.** La préservation du travail et des
rapports prime sur le gain disque. Le journal local d'inventaire ne contient que
métadonnées Git/process/Docker, hors secrets. Les dossiers et fichiers ordinaires
`STOCKPILOT_AI_CHEAT_SHEET_COMPLET.html` et fichiers `:Zone.Identifier` ne sont pas
reconnus comme worktrees et restent intacts. Aucun nettoyage de volumes Docker.

## Recommandations et prochaine décision humaine

1. **Démonstration commerciale : oui, supervisée et synthétique**, après contrôle
   du JWT, des services et du scénario. Candidate locale, limites clairement dites.
2. **Pilote isolé : préparé sous conditions**, pas une autorisation de production.
   Avant données réelles : référent, source de vérité stock, accès, accord de
   traitement, sauvegarde/restauration prouvée, procédure d'incident et support.
3. **SaaS multi-tenant commercialisable : non.** Isolation, OIDC, autorisations,
   stores et checkpoints scoped, quotas distribués, migrations et exploitation
   doivent être implémentés et prouvés ; fail-closed maintenu.
4. **Premier client payant :** valider un problème et un budget, exécuter le pilote
   isolé avec mesure avant/après et temps de support, puis vérifier marge, licences,
   contrat et responsabilités. Une DEMO et une CI vertes ne prouvent pas la demande.
5. **Priorité humaine :** tester la candidate puis mener les premiers entretiens
   ciblés avant d'engager V14 ou une offre tarifaire. Valider séparément le modèle
   d'isolation, les rôles et le contrat d'authentification.

Les publications et fusions techniques de ce programme ne changent ni v017 ni le
statut stable de la candidate. Aucune promotion ou mise en production effectuée.
