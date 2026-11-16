# Qualification RAG réelle isolée — 16 novembre 2026

Verdict à la fin de cette qualification : **READY FOR PR**, pour le contrat
documentaire par extraits attribués dans le périmètre backend décrit ici. À cette
étape, aucune PR, aucun commit, push, merge, déploiement ou changement de release
n'avait été effectué. La [revue de finalisation](rag-pr-review.md) décrit l'audit
suivant. Ce verdict ne qualifie pas un
déploiement ni la qualité générale de Qwen sur des documents métier.

Worktree : `/home/waelko/projects/stockpilot-rag-citations`.
Branche : `fix/rag-citation-grounding-post-v026`.
Base : `57be3df363f535bd3e330dca71ba5e853907173a`, avec le diff local du contrat B.
Le diff des trois fichiers applicatifs est identique au relevé avant cette
qualification : aucune correction applicative n'a été nécessaire.

## Résultats réellement exécutés

| Exécution | PASS | FAIL | ERROR | SKIP | Autres | Durée |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Première qualification réelle | 11 | 2 | 0 | 0 | Deux assertions nouvelles erronées sur l'état persistant après panne | 63,50 s |
| Qualification réelle finale | **13** | **0** | **0** | **0** | Ollama, embeddings, Mongo, pgvector, API et historique | **38,89 s** |
| Six modules d'intégration, avant adaptation | 79 | 21 | 0 | 0 | Fixtures historiques incompatibles, classifiées avant modification | 63,13 s |
| Six modules d'intégration, après adaptation | **100** | **0** | **0** | **0** | Aucun test supprimé, ignoré ou désélectionné dans ces modules | **82,25 s** |
| Première collecte de la suite déterministe complète | 0 | 0 | 5 | 0 | Collision avec cinq copies de preuve `test_*.py` | 6,71 s |
| Suite déterministe complète finale | **5 893** | **0** | **0** | **0** | 370 sous-tests PASS ; 819 intégrations désélectionnées par la configuration officielle | **99,63 s** |
| Évaluation déterministe enrichie | **122 cas** | **0** | **0** | **0** | Smoke déterministe, pas une mesure de qualité Ollama | — |
| Vérification des identités et nettoyage final | PASS | 0 | 0 | 0 | Deux conteneurs et leur réseau retirés ; autres ressources préservées | — |
| `git diff --check` | PASS | 0 | 0 | 0 | Diff local complet | — |

Les sous-tests et les sélections historiques de 897/140/55/310 tests ne sont pas
additionnés aux 5 893 tests. Les 819 intégrations désélectionnées ne sont pas
comptées comme réussies. Les 100 intégrations du tableau sont une sélection
explicite distincte, pas toute la suite d'intégration du dépôt.

Un avertissement pytest subsiste dans la fixture existante de
`test_demo_launcher` : clé HMAC synthétique courte. L'évaluation déterministe
signale aussi les avertissements existants de désérialisation LangGraph des enums
`Intent` et `ProductCategory`. Aucun n'a été supprimé ou masqué.

## Identité et isolation des ressources

Les instructions `docs/testing.md`, les fixtures `tests/integration_targets.py`,
les procédures Docker officielles et les skills `stockpilot-qa-reviewer` et
`stockpilot-ai-engineer` ont servi à préparer la qualification.

Avant toute connexion aux bases : vérification du daemon Docker local, des
labels Compose, du chemin de configuration, des images, des montages, des ports
et de l'appartenance exclusive au réseau. Après connexion et avant écriture :
vérification de l'identité effective des bases et des marqueurs DEMO via les
gardes officiels. Chaque test utilise une base Mongo et un schéma PG neufs,
des identités synthétiques et de nouvelles conversations.

| Ressource | Identité effective |
| --- | --- |
| Docker | Contexte `default`, socket Unix local, daemon `docker-desktop`, version 28.4.0 |
| Projet propre | `stockpilot-rag-qual-20261116` |
| Réseau propre | `stockpilot-rag-qual-20261116_default`, sous-réseau privé dédié vérifié |
| Identifiant réseau | `d34a4496441554a77dc6d516235dd6fff77cc51e4183e834a04f85d47d961ec1` |
| MongoDB | 8.3.11, replica set `rs0`, `127.0.0.1:56030`, authentification activée |
| Conteneur Mongo | `d1f9edf183a7044a2723c3d62e812cf607398f62814be2619b35e191c9a852d0` |
| Image Mongo | `mongo@sha256:d731d77bfd7afd66bd487bdf627b5bf7ce4c3602ec461d635977021db529ebbc` |
| PostgreSQL | 16.15, `127.0.0.1:56029`, base `stockpilot_test_rag_20261116` |
| Identifiant système PG | `7708837792526254119` |
| Conteneur PG | `45a86d740a77f87f933c704667a2a1244f59bfa5fc37f081e728b30dbb7eb22c` |
| Image PG | `pgvector/pgvector@sha256:7b822b0aac60967beb1ea5e576b8602c94c300a157d187f385ae3e0da199b90a` |
| Extension effectivement installée | `vector` 0.8.7 |
| Comptes | Comptes de test `rag_qual`, mots de passe distincts générés pour cette exécution |
| Stockage | Exclusivement tmpfs : Mongo `/data/db`, `/data/configdb`, `/run/rag-auth` ; PG `/var/lib/postgresql/data` |

Le réseau contient uniquement ces deux services. La comparaison des routes WSL,
Windows et Docker confirme l'absence de chevauchement avec les réseaux Hyper-V,
WSL et Docker observés. Les adresses de l'hôte restent dans les preuves privées,
hors Git. Cette vérification lit les routes de l'hôte ; elle ne se connecte à
aucune VM.

Le bridge final utilise `internal:false` et des publications exclusivement sur
loopback, conformément au modèle des services CI. Il est dédié, mais ce n'est
pas un réseau sans sortie. Le premier essai `internal:true` n'avait aucun port
effectivement publié sur ce Docker Desktop. Ses seules ressources ont été
retirées après identification, avant toute utilisation par les tests. Aucun
volume applicatif, compte de production ou port de service existant n'a été
réutilisé. Les images déjà présentes ont été utilisées avec `--pull never`.

`scripts/ci/check_targets.py` impose les URI littérales de sa stack CI. Les ports
dynamiques et les identifiants générés ici ne correspondent pas à ces constantes.
Le contrôleur local reprend les vérifications Docker de provenance et utilise
les validateurs officiels `integration_targets` pour les cibles effectives ;
aucun garde officiel n'a été affaibli.

Au terme des tests, Mongo ne contenait que `admin`, `config`, `local`. PG ne
contenait que les bases initiales et aucun schéma de test résiduel. Les deux
conteneurs et leur réseau ont ensuite été supprimés par un `compose down` limité
au projet attesté, sans `-v`. Les deux autres conteneurs et l'inventaire des
volumes sont inchangés. Preuves : `final-identities.json`, `cleanup-command.txt`
et `cleanup.json` dans le répertoire local de preuves ci-dessous.

## Fournisseur et modèles réels

Ollama existant sur l'hôte Windows, vérifié avant les appels : processus
`ollama.exe`, propriétaire du listener correspondant à `B2B_TEST_OLLAMA_URL`.
L'adresse privée et l'identité détaillée du processus restent dans les preuves
locales hors Git. Version effective : **0.40.16**.
Le fournisseur est partagé ; seules des requêtes avec données synthétiques ont
été envoyées. Aucun téléchargement, déchargement forcé ou changement de sa
configuration n'a été effectué. Inventaire et digests inchangés en fin de test.

| Modèle | Paramètres observés | Digest |
| --- | --- | --- |
| `qwen2.5:7b` | 7.6B, Q4_K_M ; contexte chargé 4 096 | `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e` |
| `nomic-embed-text:latest` | 137M, F16 ; embeddings réellement vérifiés à 768 dimensions | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |

Le test appelle les vrais `OllamaAdapter`, `OllamaEmbeddingAdapter` et
`PgTenantKnowledge`. Les sous-classes d'observation délèguent aux méthodes
originales et enregistrent les résultats ; elles ne remplacent pas le transport.
Génération : température 0, 512 tokens au maximum, prompt borné à 24 Kio,
timeout client 60 s et véritable schéma JSON transmis dans `format`.

## Scénarios et contenu vérifié

| Scénario final | Nombre | Preuve attendue et obtenue |
| --- | ---: | --- |
| Parcours froid puis chaud | 1 | Classification réelle UNKNOWN, embeddings réels, recherche PG, JSON réel conforme, rendu exact, deux conversations nouvelles |
| Abstention du modèle | 1 | JSON réel `{"answerable":false,"excerpts":[]}`, message neutre, aucune source citée |
| Isolation tenant et accès API | 1 | Même identifiant documentaire dans A et B, contenus différents ; A ne reçoit jamais les 883 jours de B ; lectures étrangères et autre utilisateur 404 ; organisation forgée 400 |
| Refus de sécurité et ingestion | 1 | Demande de secret refusée avant classification/génération/embedding ; contenu sensible synthétique refusé avant ingestion et embedding |
| Extrait inventé après génération réelle | 1 | Injection explicite d'un extrait absent ; abstention, aucune provenance publiée |
| Référence inconnue après génération réelle | 1 | Injection explicite de S99 ; refus de citation, aucune provenance publiée |
| Extrait d'un autre tenant après génération réelle | 1 | Label local avec contenu B ; rejet, aucune provenance publiée |
| Prose libre après génération réelle | 1 | Remplacement explicite par du texte non structuré ; abstention, aucune citation artificielle |
| Historique après retrait de source | 1 | Réponse publique devenue indisponible ; tour synthétique original inchangé ; aucun nouvel appel LLM |
| Erreur fournisseur réelle | 1 | Nom de modèle volontairement absent, sans téléchargement ; HTTP fournisseur 404, API 503, tentative échouée persistante et relançable |
| Timeout fournisseur réel | 1 | Timeout de lecture de 10 ms sur ce seul client de test ; API 503, code `read_timeout`, aucune réponse documentaire réussie |
| Question anglaise | 1 | Extrait autorisé conservé dans la langue du document, sans traduction ni synthèse ajoutée |
| Taille d'entrée API | 1 | 4 001 caractères : 422 avant appel au modèle |

Les quatre injections ne sont pas présentées comme des hallucinations observées
spontanément chez Qwen. Elles corrompent une génération réellement obtenue pour
éprouver la validation serveur dans le parcours complet.

Exemple réellement publié :

```text
[S1] « Le délai de livraison de l'entreprise Atelier A est de 7 jours ouvrés. »
```

Les tests vérifient le JSON et son schéma, l'appartenance de l'extrait au passage,
la correspondance source/extrait, organisation/document/génération/chunk/hash,
les sources citées persistées et le texte final exact. Ils vérifient aussi
l'absence des passages retirés, sensibles et étrangers dans le prompt et la
réponse, l'historique et l'absence de mutation des produits, commandes,
opérations de commande et mouvements de stock synthétiques.

La whitelist API existante publie le texte avec `[Sx]`, mais pas le champ interne
`cited_sources`. Ce champ est vérifié dans le service et le journal PG, puis
comparé au texte effectivement publié ; aucune nouvelle propriété n'a été
ajoutée à l'API. Un HTTP 200 seul n'est jamais l'oracle de réussite.

## Mesures froid / chaud

Mesures conservées du premier scénario, déjà PASS lors du premier run :

| Mesure | Première question | Deuxième question |
| --- | ---: | ---: |
| Parcours API complet | **6,927 s** | **2,228 s** |
| Classification Ollama | 5,259 s | 0,662 s |
| Génération JSON documentaire | 1,237 s | 1,157 s |
| Chargement indiqué par Ollama pour la classification | 3 325,910 ms | 14,487 ms |

`/api/ps` était vide au préflight. Qwen était absent avant la première question
et présent avant la deuxième. Le premier embedding d'ingestion, à froid, a pris
5,199 s ; l'indexation ne fait pas partie des 6,927 s de réponse. La génération
documentaire suit la classification : Qwen y est déjà chargé, même pendant le
premier parcours. Aucune mesure de génération seule après déchargement forcé
n'est revendiquée. Deux observations ne constituent ni une distribution de
latence ni un engagement de performance.

## Échecs conservés et adaptations justifiées

Les deux échecs du premier test réel étaient des erreurs dans les nouvelles
assertions de qualification : elles exigeaient `status=failed` et `reply=None`.
Le contrat existant conserve `status=pending` pour empêcher une autre clé de
message de contourner le retry ; `execution.state=failed` et une réponse
`ASSISTANT_UNAVAILABLE` décrivent l'échec. Les nouvelles assertions suivent ce
contrat, contrôlent le code, l'autorisation de retry, l'absence de commande et de
sources, et le masquage des champs privés. Le test historique de résilience
confirme ce comportement ; aucun code de gestion d'erreur n'a été modifié.

Les **21 échecs supplémentaires d'intégration** sont distincts des anciennes
21 incompatibilités déterministes déjà réconciliées. La sortie complète, les
cinq fichiers originaux et la classification ont été sauvegardés **avant** leur
adaptation. Classement : **A = 4**, contrat de rendu remplacé ; **C = 17**, défaut
de fixture ; **B = 0**, aucune régression applicative démontrée.

| Test initialement en échec | Classe | Adaptation | Final |
| --- | --- | --- | --- |
| `tests/test_tenant_knowledge_integration.py::test_only_tenant_passages_reach_generator_under_prompt_injection` | A | Texte libre et annexe → extrait JSON exact, rendu et provenance vérifiés | PASS |
| `tests/test_tenant_knowledge_integration.py::test_real_langgraph_async_resume_and_history_after_new_adapter` | A | Texte libre et annexe → extrait JSON exact, rendu et provenance vérifiés | PASS |
| `tests/test_tenant_knowledge_failure_integration.py::test_generator_uses_existing_llm_port_signature` | A | Texte libre et annexe → extrait JSON exact, rendu et provenance vérifiés | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_exact_G15_06_uses_previous_cited_sources[delivery-guide]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_exact_G15_06_uses_previous_cited_sources[stocks-guide]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_exact_G15_06_uses_previous_cited_sources[preparation-guide]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_multiple_citations_exclude_consulted_but_uncited_documents` | C | Callback compatible avec response_format et extraits exacts ; attente d’annexe remplacée par provenance/historique exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_missing_unverifiable_or_non_documentary_prior_never_infers_a_source[uncited]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_missing_unverifiable_or_non_documentary_prior_never_infers_a_source[invented]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_missing_unverifiable_or_non_documentary_prior_never_infers_a_source[catalogue]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_missing_unverifiable_or_non_documentary_prior_never_infers_a_source[legacy]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_current_source_revalidation_protects_followup_replay_and_history[deleted]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_current_source_revalidation_protects_followup_replay_and_history[replaced]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_current_source_revalidation_protects_followup_replay_and_history[sensitive]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_current_source_revalidation_protects_followup_replay_and_history[content_changed]` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_one_revoked_source_does_not_return_a_partial_historical_citation` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_private_previous_turn_cannot_cross_conversations_users_or_organizations` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_revoked_permission_and_old_session_cannot_replay_a_citation` | C | Callback compatible avec response_format et extraits exacts | PASS |
| `tests/test_assistant_v025_citation_integration.py::test_G14_05_documentary_snapshot_replay_cannot_bypass_later_revocation` | C | Callback compatible avec response_format et extraits exacts ; attente d’annexe remplacée par provenance/historique exacts | PASS |
| `tests/test_assistant_v025_rag_security_integration.py::test_legacy_generation_is_filtered_across_chunk_boundary_and_metadata_reads` | A | Texte libre et annexe → extrait JSON exact, rendu et provenance vérifiés | PASS |
| `tests/test_assistant_resilience_integration.py::test_real_advisory_connection_loss_fences_the_old_running_graph` | C | Nom CI littéral → garde officiel de la base effective | PASS |

Dans les 16 cas utilisant `generate_cited`, le callback `generate(*, prompt)`
ne pouvait accepter `response_format` : le service exposait donc
`TenantDataUnavailable` avant toute assertion de provenance. La fixture renvoie
maintenant les extraits exacts des sources autorisées sous le schéma existant.
Le cas négatif de prose non structurée reste présent et doit s'abstenir ; le cas
S99 reste invalide. Les assertions de permissions, isolation, révocation,
immutabilité des tours et non-mutation métier sont conservées.

Deux assertions de cette fixture attendaient aussi une annexe de documents
consultés. Elles vérifient maintenant les passages consultés persistés
séparément, les seuls extraits publiés et la copie fidèle de l'extrait dans
l'historique. Le test des passages sensibles conserve toutes ses assertions de
filtrage à travers les chunks et les métadonnées ; son exemple positif utilise
un extrait littéral et vérifie les références autorisées.

Le test de perte de connexion advisory exigeait littéralement `stockpilot_ci`.
Il appelle désormais `verify_postgres_identity(connection, e.uri)` avant la
terminaison du seul PID advisory identifié. Ce garde officiel exige le nom
effectif attendu, un nom de base de test autorisé et l'absence de marqueur DEMO.
La sélection précise du PID, le fencing de l'ancien worker, le retry et la
non-mutation métier sont inchangés.

La première collecte backend a trouvé les copies de preuve `test_*.py` et
produit cinq erreurs de module dupliqué. Ces copies sont conservées sous
`*.py.txt` ; aucun test du dépôt n'a été exclu. Une première invocation des
évaluations dans la sandbox a été interrompue (code 130) sans résultat
exploitable ; l'invocation bornée hors sandbox a terminé avec 122/122 cas PASS.
Le diagnostic déjà acquis des restrictions asyncio n'a pas été refait.

## Fichiers de cette intervention

- Créé : `scripts/tests/validate_rag_excerpt_ollama.py`, 13 tests manuels opt-in,
  gardes d'identité, observations des vrais adaptateurs, API et persistance.
- Adapté : `tests/test_tenant_knowledge_integration.py`, exemples positifs
  structurés et séparation des sources ; oracles tenant et checkpoint conservés.
- Adapté : `tests/test_tenant_knowledge_failure_integration.py`, fixture du port
  LLM et assertion du schéma ; signature publique inchangée.
- Adapté : `tests/test_assistant_v025_citation_integration.py`, callback et
  attentes d'annexe obsolètes ; nouveaux contrôles du rendu exact.
- Adapté : `tests/test_assistant_v025_rag_security_integration.py`, seul exemple
  positif structuré et provenance ; refus historiques conservés.
- Adapté : `tests/test_assistant_resilience_integration.py`, garde officiel
  d'identité de la base au lieu du nom CI codé en dur.
- Créé : le présent rapport.

Les fichiers applicatifs et les autres changements préexistants du worktree
sont conservés sans modification pendant cette qualification. Le diff exact de
cette intervention est dans `qualification.diff`, et le diff suivi complet
contre HEAD dans `tracked-final.diff`, parmi les preuves privées ci-dessous.

## Commandes et preuves

Toutes les commandes ont été exécutées depuis le worktree indiqué. Les runners
locaux vérifient les ressources avant connexion et chargent seulement les URI
de test générées dans un fichier privé à permissions 0600. Aucune URI avec
mot de passe n'est reproduite ici. L'environnement a été supprimé : une nouvelle
exécution doit préparer et attester de nouvelles ressources.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/isolation.py start

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/run_pytest.py live-first scripts/tests/validate_rag_excerpt_ollama.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/run_pytest.py live-final scripts/tests/validate_rag_excerpt_ollama.py

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/run_pytest.py integrations-first tests/test_tenant_knowledge_integration.py tests/test_tenant_knowledge_failure_integration.py tests/test_assistant_v025_citation_integration.py tests/test_assistant_v025_rag_security_integration.py tests/test_assistant_v025_final_rag_integration.py tests/test_assistant_resilience_integration.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/run_pytest.py integrations-final tests/test_tenant_knowledge_integration.py tests/test_tenant_knowledge_failure_integration.py tests/test_assistant_v025_citation_integration.py tests/test_assistant_v025_rag_security_integration.py tests/test_assistant_v025_final_rag_integration.py tests/test_assistant_resilience_integration.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout --signal=INT --kill-after=5s 180s .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src --junitxml=tmp/rag-live-qualification-20261116/deterministic-final.xml > tmp/rag-live-qualification-20261116/deterministic-final.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout --signal=INT --kill-after=5s 60s .venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched --output tmp/rag-live-qualification-20261116/evals-final.json > tmp/rag-live-qualification-20261116/evals-final.txt 2>&1

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/final_verify.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tmp/rag-live-qualification-20261116/cleanup.py
git diff --check
```

Les quatre appels au runner exécutent réellement `.venv/bin/python -m pytest
-vv -s -p no:cacheprovider -ra --tb=short -m integration -o 'pythonpath=src tests .'
--junitxml=...` sur les fichiers indiqués. Les argv complets sont conservés dans
`live-first-command.json`, `live-final-command.json`,
`integrations-first-command.json` et `integrations-final-command.json`.
La collecte backend initiale utilisait la même commande avec les fichiers de
sortie `deterministic-full.*` ; les deux logs sont conservés.

Répertoire local privé, ignoré par Git :
`tmp/rag-live-qualification-20261116/`. Il contient les sorties pytest intégrales,
JUnit, JSON réels par scénario, métriques par phase, inventaires, versions,
classification avant modification, originaux des tests, diffs et preuve du
nettoyage. `first-run/` conserve les premières mesures à froid. Les empreintes
des fichiers pertinents figurent dans `evidence-sha256.json`. Les logs, fichiers
de configuration privée, identifiants et artefacts ne doivent pas être commités.

## Limites restantes

- L'appartenance exacte d'un extrait prouve sa provenance, pas sa pertinence
  pour toute question ni la vérité du document. Aucune synthèse libre n'est
  déclarée vérifiée ; aucun vérificateur sémantique LLM n'est introduit.
- Corpus petit et synthétique, deux tenants, un modèle et une configuration.
  Pas de qualification statistique, de charge, ni de campagne générale sur des
  documents métier. Les bornes de sortie sont couvertes par les tests
  déterministes ; aucun dépassement spontané de Qwen n'est revendiqué.
- FastAPI et BFF sont exécutés via TestClient en processus avec des sessions
  synthétiques et de vrais stores isolés. Aucun navigateur, frontend, reverse
  proxy ou vrai parcours SSO Keycloak n'est qualifié dans ce run.
- L'anglais est testé avec un extrait documentaire français conservé littéral.
  Ce contrat ne garantit pas une traduction de la réponse documentaire.
- Ollama est le fournisseur partagé autorisé, avec client de test distinct ;
  ses performances peuvent dépendre d'autres charges. Le timeout de 10 ms
  éprouve le chemin d'erreur et ne mesure pas le taux de timeouts à 60 s.
- Les 100 intégrations pertinentes et toute la suite déterministe passent.
  La totalité des intégrations CI, les gates frontend et une revue humaine
  indépendante ne sont pas revendiqués ici. La CI et la revue de la future PR
  restent requises avant toute décision de fusion.
- Production v0.2.4, bases sources, VM Hyper-V, tour historique `completed`,
  volumes applicatifs, tags et releases n'ont fait l'objet d'aucune mutation.
