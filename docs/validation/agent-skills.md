# Validation de la bibliothèque Agent Skills

Audit et validations du 27 octobre 2026. Périmètre : instructions uniquement,
sans code métier, schéma, workflow CI ou infrastructure de déploiement modifiés.

## Base et audit ciblé

- Dépôt : [WaelOuni/stockpilot-ai](https://github.com/WaelOuni/stockpilot-ai).
  `origin/main` actualisé : `b3258cb34d8f8507205a8bb28003b93e8a67cb52`,
  fusion de #62. Le checkout historique propre reste à `6f09496`.
- Worktree dédié `stockpilot-agent-skills`, branche
  `codex/stockpilot-agent-skills`, créée depuis ce main distant.
  Aucun contenu de `dev` intégré ; aucun autre worktree modifié.
- Un seul AGENTS.md suivi et aucun SKILL.md préexistant au HEAD audité.
  Instructions racine et quatre points d'entrée mémoire lus.
- Architecture constatée : domaine, application/ports/use cases/workflows,
  adaptateurs et FastAPI dans [src/stockpilot](../../src/stockpilot/).
  Deux compositions distinctes : [main.py](../../src/stockpilot/main.py)
  et [bff.py](../../src/stockpilot/bff.py). LangGraph appartient à l'application.
- Frontend Vue 3/TypeScript/Vite dans [frontend](../../frontend/), parcours
  historique et B2B distincts, tokens existants, préférences FR/EN et clair/sombre.
  Le contrat B2B garde prix en unités mineures exactes et devise.
- Migrations Mongo JSON et PostgreSQL SQL sous
  [persistence](../../src/stockpilot/infrastructure/persistence/), runners
  explicites et historique immuable. Pas d'Alembic/ORM ajouté.
- Tests pytest dans [tests](../../tests/), Vitest près des composants,
  Playwright et harnais BFF dédiés, evals dans [evals](../../evals/).
  Commandes recoupées avec [testing](../testing.md),
  [pyproject.toml](../../pyproject.toml), [package.json](../../frontend/package.json)
  et [CI](../../.github/workflows/ci.yml).
- ORDER : preuve/confirmation/désarmement, quantités et identités exactes,
  ownership, idempotence, atomicité et checkpoints conservés.
  ADVICE reste sans réservation, avec faits catalogue vérifiés.
- B2B : identité serveur, sessions BFF/OIDC/PKCE/CSRF, droits réévalués,
  tenant dans les requêtes, conversations privées au créateur, retrieval
  organisationnel avant LLM et réconciliation Mongo/PG à clé constante.
- Plusieurs documents sont des snapshots historiques. Les compétences demandent
  de les recouper avec HEAD ; elles ne recopient pas une ancienne absence de
  fonctionnalité comme vérité actuelle. Aucun audit de sauvegarde/récupération refait.

## Inventaire et règles communes

| Skill | Responsabilité principale |
| --- | --- |
| [stockpilot-architect](../../.agents/skills/stockpilot-architect/SKILL.md) | Impacts inter-couches, contrats, compatibilité et ADR |
| [stockpilot-backend](../../.agents/skills/stockpilot-backend/SKILL.md) | API, validation stricte, métier et persistance |
| [stockpilot-frontend-ux](../../.agents/skills/stockpilot-frontend-ux/SKILL.md) | Parcours Vue, accessibilité, langues et thèmes |
| [stockpilot-ai-engineer](../../.agents/skills/stockpilot-ai-engineer/SKILL.md) | Graphe, recherche naturelle, RAG et évaluations |
| [stockpilot-security](../../.agents/skills/stockpilot-security/SKILL.md) | Frontières de confiance, sessions et non-fuite |
| [stockpilot-qa-reviewer](../../.agents/skills/stockpilot-qa-reviewer/SKILL.md) | Recette, régression, preuves et verdict |
| [stockpilot-devops-sre](../../.agents/skills/stockpilot-devops-sre/SKILL.md) | CI, exploitation, migration et rollback contrôlés |
| [stockpilot-product-growth](../../.agents/skills/stockpilot-product-growth/SKILL.md) | Découverte B2B, qualification, pilote et valeur |

[AGENTS.md](../../AGENTS.md) conserve ses règles initiales et ajoute la source
GitHub, la vérification des snapshots, les changements minimaux, la protection
des données/tenants/credentials/ressources sensibles et les verdicts bornés.
Les commandes canoniques restent centralisées. Un skill ne constitue pas une
autorisation de modifier ORDER, déployer, contacter un prospect ou créer des agents.

Huit fichiers SKILL.md autonomes ; aucun script, dépendance, copie de runbook ou
dossier references superflu ajouté. Les références pointent vers le code et les
guides existants. La sélection combine seulement les compétences nécessaires :
un auteur principal, une analyse de sécurité si la frontière l'exige, QA pour la
preuve ; Architect intervient sur un impact transverse, pas sur chaque correction.

## Découverte Codex et conformité

Sources consultées le 27 octobre :
[documentation officielle Codex](https://learn.chatgpt.com/docs/build-skills)
et [standard Agent Skills](https://agentskills.io/specification).

Chaque dossier porte le même nom que son frontmatter YAML, avec les deux champs
requis non vides `name` et `description`. Les descriptions contiennent critères
d'usage et exclusions. Les corps restent courts et chargent leurs références à
la demande. L'absence de `agents/openai.yaml` conserve la sélection implicite par
défaut ; aucune métadonnée UI ni dépendance MCP n'est nécessaire ici.

Test réel avec `codex-cli 0.164.0-alpha.11.1` :
un processus `codex app-server --stdio` a été initialisé, puis interrogé par
`skills/list` avec `forceReload: true` sur la racine du worktree et `frontend/`.
Dans chaque réponse : **8 skills StockPilot, scope=repo, enabled=true, errors=[]**.
Aucune configuration utilisateur modifiée et aucun tour applicatif lancé.

Ce test prouve la découverte dans cette version et ce worktree. Il ne prouve pas
que toute reformulation déclenchera automatiquement la bonne combinaison.
Les huit skills n'apparaissent pas magiquement dans un autre checkout qui ne
contient pas encore ces fichiers.

## Matrice de déclenchement

La matrice ci-dessous est une revue de routage, pas un test automatique de
l'interface Codex. Pour l'invocation explicite, préfixer chaque cas positif par
`$nom-du-skill` en CLI/IDE ; dans l'application, utiliser le sélecteur disponible.
Une invocation explicite ne supprime pas les préconditions ou limites du skill.

| Skill | Cas positif | Cas négatif | Chevauchement attendu | Cas sensible / arrêt |
| --- | --- | --- | --- | --- |
| Architect | Évaluer l'évolution des réponses B2B, checkpoints et Vue | Corriger une marge CSS | Backend implémente ; Architect arbitre les impacts transverses | Migration persistante ou changement ORDER hors demande : suspendre la mutation |
| Backend | Ajouter un filtre B2B strict avec tests tenant | Ajuster le style d'un dialogue | Security revoit l'accès ; Backend réalise le contrat | Préparer une migration ne permet pas de l'appliquer à une installation |
| Frontend UX | Rendre la comparaison accessible, mobile et bilingue | Modifier l'atomicité des réservations | Backend traite un contrat manquant ; Frontend le parcours existant | Aucun succès inventé après timeout ni changement implicite de sécurité |
| AI Engineer | Diagnostiquer recherche B2B ambiguë et retrieval | Ajouter un CRUD indépendant | Security revoit les données LLM ; Architect les impacts transverses | Prompt/modèle partagé affectant ORDER : demande ORDER explicite requise |
| Security | Revoir les conversations après changement de compte | Ajuster un espacement sans effet sur l'accès | Frontend invalide les vues ; Security vérifie serveur/stores | Revue seule sans correctif implicite ; rotation réelle à autoriser |
| QA Reviewer | Exécuter recette et verdict fondé sur les preuves | Implémenter une feature | Backend corrige sur demande ; QA contrôle les invariants | Cible incertaine : suspendre l'intégration et continuer offline |
| DevOps SRE | Diagnostiquer CI et préparer correction/rollback | Modifier métier ou réponses LLM | Backend écrit la migration ; SRE prépare exécution/restauration | Pas de déploiement implicite ; demander l'autorisation manquante sur une action précise |
| Product Growth | Préparer qualification et démonstration de neuf minutes | Implémenter une feature ou contacter un prospect | QA/SRE établissent les préconditions de la démo ; Product son scénario | Aucun contact, commande réelle ou promesse non prouvée implicites |

Les 32 cas ont été examinés par le reviewer indépendant : séparation cohérente,
pas de contradiction bloquante. Le cas positif Backend doit commencer par
vérifier l'existant : le scénario A a justement révélé un filtre déjà livré.

## Trois simulations indépendantes, sans mutations applicatives

Un agent séparé, sans historique de rédaction ni réponses attendues, a d'abord
lu noms/descriptions, choisi les compétences, puis lu les corps et les références
nécessaires. Il avait pour consigne de produire uniquement plans et analyses.
Les résultats ci-dessous sont des observations d'une évaluation dirigée ; ce
n'est pas une mesure statistique du routage implicite de Codex.

### A — Filtre de catégorie B2B

Demande : « Plan fictif : ajouter un filtre de catégorie strict à une lecture
du catalogue B2B existante, en préservant accès et isolation ; proposer la recette. »

Sélection observée : **Backend + Security + QA**. Le filtre existe déjà dans
[CatalogueFilters](../../src/stockpilot/presentation/api/routes/b2b_business.py),
[TenantProductQuery](../../src/stockpilot/domain/entities/tenant_product.py)
et [Mongo tenant_catalogue](../../src/stockpilot/infrastructure/persistence/mongodb/tenant_catalogue.py).
Le plan commence donc par établir le comportement existant, pas créer un endpoint
ou une migration. La catégorie est littérale et bornée ; organisation et catégorie
précèdent pagination et total dans la requête.

Plan produit : reproduire un défaut de validation précis, ajouter uniquement sa
régression, puis corriger la couche fautive si nécessaire. Recette proposée :
absent/vide/1/128/129 caractères, doublons, champs inconnus, types non textuels au
contrat interne, Unicode/opérateurs littéraux, organisations A/B avec mêmes SKU,
permission/révocation/panne et pagination exacte. Suites identifiées :
`test_tenant_catalogue_contracts.py`, `test_b2b_http_contracts.py`,
`test_tenant_catalogue_mongo_integration.py`, `test_b2b_http_mongo_integration.py`
dans [tests](../../tests/).

Acceptation : résultat scoped exact, accès inchangé, aucune mutation stock/ORDER.
Cible non prouvée = arrêt des tests dépendants, analyse offline possible.

### B — Dialogue de comparaison

Demande : « Plan fictif : améliorer le dialogue de comparaison B2B au
clavier/mobile dans les deux langues et thèmes, avec sa recette UI. »

Sélection observée : **Frontend UX + QA**. L'analyse retrouve
[ComparisonPanel](../../frontend/src/b2b/ComparisonPanel.vue) et
[DetailsPanel](../../frontend/src/b2b/DetailsPanel.vue) : quatre IDs uniques au plus,
table sémantique, dialogue natif, autofocus, Escape et retour du focus déjà présents.
Des couleurs locales fixes dans DetailsPanel sont une piste à vérifier au rendu,
pas un défaut navigateur prétendument observé.

Plan produit : reproduire avec quatre produits et textes longs, corriger seulement
les défauts constatés par les tokens existants, conserver prix exacts, permissions
et descriptions métier non traduites ; vérifier les autres consommateurs du
dialogue partagé. Recette : FR/EN × clair/sombre × bureau/mobile, clavier,
Tab/Shift+Tab, Escape, focus de retour, zoom 200 %, défilement, chargement/erreur,
session devenue obsolète. Vitest puis navigateur B2B isolé ; le harness Advice
historique ne prouve pas Keycloak/BFF.

Acceptation : actions accessibles dans chaque variante sans changement de contrat
ou de stockage sensible. Permission serveur nouvelle = cadrage séparé.

### C — Recherche naturelle documentaire

Demande : « Plan fictif : améliorer la recherche naturelle documentaire de
l'assistant B2B ; analyser impacts graphe/checkpoints et risque de fuite ou
injection, sans modifier ORDER. »

Sélection observée : **AI Engineer + Security + Architect**, avec **QA** ajouté
par l'évaluateur pour les preuves. Aucun besoin de SRE ou Product Growth.
Architect traite ici l'analyse transverse demandée, sans proposer de refonte.

L'analyse retrouve le graphe `understand → answer` dans
[tenant_assistant.py](../../src/stockpilot/application/services/tenant_assistant.py),
la récupération documentaire distincte du catalogue dans
[tenant_retrieval.py](../../src/stockpilot/application/services/tenant_retrieval.py),
le CTE filtré avant distance dans
[tenant_knowledge.py](../../src/stockpilot/infrastructure/persistence/postgres/tenant_knowledge.py)
et le saver privé
[tenant_checkpointer.py](../../src/stockpilot/infrastructure/persistence/postgres/tenant_checkpointer.py).

Plan produit : cas documentaires synthétiques avec passages attendus, diagnostic
séparé du routage/retrieval/formulation, correction locale seulement si un défaut
est démontré. Pas de nouvelle topologie, état ou migration par défaut.
Recette : tenant étranger dominant les vecteurs, mêmes IDs/titres, deux utilisateurs
d'une organisation (manager compris), injection, révocation pendant embedding,
vecteur invalide, panne PG/Ollama, absence de corpus, reprise et anciennes données
de checkpoint. Tests knowledge/assistant existants, régressions ORDER/ADVICE,
eval déterministe puis mesure réelle dédiée si qualité modèle revendiquée.

Acceptation : aucun passage étranger envoyé au LLM, absence distincte d'une panne,
réponse limitée aux sources, reprise conservée. Un changement de prompt/modèle
partagé affectant ORDER suspend cette modification jusqu'à demande explicite.
Les limites de révocation inter-stores restent visibles.

**Résultat des trois simulations :** plans ciblés, références réelles, aucune
fonctionnalité inventée, refonte proposée ou mutation exécutée. Les cas ressemblent
aux exemples des skills : cette réussite dirigée ne garantit pas leur
généralisation à toute demande imprévue.

## Vérifications exécutées

| Contrôle | Résultat local |
| --- | --- |
| Validateur officiel `skill-creator/scripts/quick_validate.py` fourni par Codex | 8/8 valides |
| Frontmatter : champs non vides, noms uniques et égaux aux dossiers, descriptions bornées | 8/8 conformes |
| Références relatives des skills, AGENTS et de ce rapport | Toutes résolues au contrôle final |
| Revue des commandes | Correspondance avec AGENTS, testing, package.json, pyproject et CLI présents |
| Recherche de secrets sur les fichiers livrés + lecture du diff | Aucun credential détecté ; aucune configuration privée lue |
| Découverte réelle `skills/list` racine et frontend | 8/8 enabled, scope repo, aucune erreur dans les deux contextes |
| Matrice de routage | 32 cas examinés indépendamment, validation statique |
| Scénarios pratiques dirigés | A, B et C réalisés en analyse seule |
| `.venv/bin/python -m pip check` | Réussi, package editable du worktree dédié |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider` | **3 359 passed, 348 subtests passed, 516 deselected**, 1 warning |
| `.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched` | **122 cas, 0 échec** ; aucune mesure de qualité Ollama revendiquée |
| `cd frontend && npm test && npm run typecheck && npm run lint && npm run build` | **33 fichiers / 524 tests réussis**, typecheck/lint/build réussis |
| `git diff --check` | Réussi |
| Inspection du périmètre | AGENTS + 8 SKILL.md + ce rapport seulement |

Dépendances propres au worktree installées depuis les lockfiles Python et
`npm ci`, Node 22.19.0 / Python 3.12.3. Le warning pytest vient de la clé HMAC
courte de la fixture synthétique dans `test_demo_launcher.py` ; il n'a pas été
masqué ni corrigé dans cette mission documentaire.
Les 516 tests désélectionnés ne sont pas comptés comme réussis.

Les petits contrôles de validation et le client JSON-RPC utilisés pour cette
mission sont temporaires hors dépôt ; aucun framework de test des skills
supplémentaire n'est ajouté. Le validateur officiel est une preuve de structure,
pas une preuve de comportement ni un détecteur exhaustif de secrets.

## Revue indépendante

Un second agent séparé a relu les huit skills, leurs références et des contrats
du code, sans participation à leur rédaction. Contrôles : pertinence StockPilot,
déclencheurs/exclusions, conflits, contexte, garde-fous, exactitude et maintenance.

Verdict : **GO sur le contenu des huit skills, aucun finding bloquant**.
Il a confirmé notamment RAG scoped avant LLM, confirmation HTTP et journal,
prix exacts, OIDC/BFF, préférences navigateur et ports isolés.
Le seul point de livraison signalé était le lien vers ce rapport encore absent
pendant la rédaction ; le fichier est maintenant présent et fait partie du
contrôle final des liens. Les trois références de tests incorrectes trouvées
par le contrôle statique initial ont été corrigées avant cette revue.

Les corps font 55–62 lignes chacun (469 lignes au total), environ 30 Ko pour
la bibliothèque entière. Le reviewer recommande de conserver leur chargement
sélectif. La lecture des quatre mémoires déjà imposée par AGENTS représente
davantage de contexte ; cette obligation préexistante n'est pas élargie.
La revue ne certifie ni la sélection automatique ni un environnement déployé.

## Réutilisation et limites

Exemples de demandes explicites :

- `$stockpilot-backend $stockpilot-security $stockpilot-qa-reviewer Analyse ce filtre B2B et prépare une correction minimale avec sa recette.`
- `$stockpilot-frontend-ux $stockpilot-qa-reviewer Améliore le dialogue de comparaison au clavier, en FR/EN et clair/sombre.`
- `$stockpilot-ai-engineer $stockpilot-architect $stockpilot-security Analyse les impacts d'une évolution RAG B2B, sans toucher ORDER.`
- `$stockpilot-devops-sre Prépare un rollback vérifiable sans l'exécuter.`
- `$stockpilot-product-growth Prépare une démonstration B2B de neuf minutes et une grille de qualification, sans contact externe.`

Après une évolution des contrats, relire le skill concerné, vérifier ses liens,
rejouer les cas positifs/négatifs et le scénario affecté. Utiliser le
`scripts/quick_validate.py` fourni par le skill système `skill-creator` quand
disponible, puis `skills/list` pour vérifier la découverte. Ne pas dépendre d'un
chemin absolu propre à cette machine dans les compétences.

La validation locale ne comprend pas de services Mongo/PG/Keycloak, de navigateur
Playwright ni d'Ollama réel : changement documentaire, aucune garantie applicative
nouvelle à éprouver sur ces services. Aucun secret privé lu ou publié, aucune
démo lancée/migrée, aucun volume modifié, aucun merge ni déploiement.
Le statut de CI distant est à lire sur la PR et son SHA courant ; il ne remplace
ni une recette B2B réelle ni une décision d'activation.
