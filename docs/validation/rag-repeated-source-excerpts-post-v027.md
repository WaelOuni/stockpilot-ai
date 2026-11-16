# Extraits répétés d'un passage — correction post v0.2.7

Périmètre : défaut reproduit sur v0.2.7, sans attribution à la cause historique
du NO-GO, dont le JSON et la map de sources n'ont pas été conservés.
Base `origin/main` vérifiée : `50115b6618a8fdc330b6eddc7708944fecbff100`.
Branche `fix/rag-repeated-source-excerpts-post-v027`, worktree dédié
`/home/waelko/projects/stockpilot-rag-repeated-source-fix`.

## Invariant documenté avant modification

Chaque extrait affiché doit appartenir exactement, après NFC et CRLF/CR→LF
seulement, au passage lié à son label dans la map du retrieval tenant courant.
Répéter un label ne dispense jamais de valider le nouvel extrait. Aucun contenu
factuel libre n'est publié. Tous les passages consultés, même non cités, sont
revalidés après génération : organisation, document, génération, chunk, hash
brut, contenu sûr et droits actuels. Les collecteurs restent vides en cas d'échec.

L'enveloppe JSON doit contenir seulement `answerable` et `excerpts`, sans clés
dupliquées ; chaque item contient seulement `source_id` et `excerpt`. Maximum
cinq **extraits**, de 1 à 2 000 caractères chacun ; résultat fournisseur et rendu
public bornés à 8 000 caractères. Les doublons ne réduisent pas ces compteurs.
Les budgets modèle, prompt et timeout restent inchangés : 512 tokens de sortie,
24 Kio de prompt, 60 s côté provider et 75 s côté assistant. Le retrieval
PostgreSQL demande trois passages par défaut (paramètre borné entre 1 et 20).
Le validateur de preuves accepte de 1 à 20 passages **distincts**, contrôle les
identifiants et le hash brut, et refuse toujours un doublon dans une liste de
preuves. Cette limite est indépendante des cinq extraits de la réponse.

Les labels S1… identifient des passages pour cet appel ; plusieurs extraits
peuvent légitimement utiliser le même label. Les preuves persistées représentent
les **passages distincts**, identifiés par organisation/document/génération/chunk
avec hash de contenu. `cited_sources` doit être unique par passage, dans l'ordre
de première citation. Deux chunks d'un même document restent deux preuves.
`_rag_read_sources` conserve les passages consultés ; le validateur historique
continue de refuser les listes de preuves dupliquées ou modifiées.

Dans v0.2.7, la boucle valide l'extrait puis refuse une identité déjà rencontrée
à la ligne 290. Elle confond l'unicité des preuves avec l'unicité des extraits.
Les listes ne sont remplies qu'après revalidation, puis l'historique revalide
à nouveau les preuves. Aucun changement de cette frontière n'est nécessaire.

## Correctif minimal et tests

`TenantRetrieval.execute` remplace le `seen` de la boucle d'extraits par un
dictionnaire de références indexé par l'identité complète du passage.
`validate_excerpt` est appelé pour **chaque** item avant `setdefault` ; aucun
`continue` ne saute la validation d'une répétition. Le rendu conserve les extraits
dans leur ordre, et `cited_sources` reçoit les valeurs uniques du dictionnaire,
uniquement après revalidation de tous les passages consultés. Le garde de
`revalidate_citations` contre les preuves dupliquées reste inchangé.

Diff applicatif : **4 lignes ajoutées, 5 supprimées**, dans un seul fichier.
Aucun changement de schéma, de prompt, de modèle, de budget, de retry ou d'ORDER.
Les deux fichiers de tests ajoutés couvrent les dix exigences, les chunks
distincts d'un même document, les sources non citées révoquées, la perte de droit,
la persistance, le suivi de citation et le masquage de l'historique après révocation.

Sur la base exacte v0.2.7, avant modification applicative : **8 échecs / 16 succès**
unitaires et **3 échecs** d'intégration. Après correction : **24 + 3 succès**.
Les deux fichiers de tests ont conservé leurs SHA-256 entre RED et GREEN.
Les quatre cas de revalidation RED échouaient aussi sur le compteur de lectures :
le refus prématuré empêchait d'atteindre la vérification des sources.

## Validation exécutée

| Campagne | Résultat |
| --- | --- |
| Nouveaux tests unitaires, RED | 8 FAILED, 16 PASS |
| Nouvelles intégrations, RED | 3 FAILED |
| Nouveaux tests unitaires, GREEN | 24 PASS |
| Nouvelles intégrations, GREEN | 3 PASS |
| RAG/sécurité ciblés | 133 PASS |
| Intégrations complètes requises | 821 PASS, 5 848 désélectionnés, 0 échec/skip ; 623,15 s |
| Sous-ensemble documentaire de cette campagne | 95 PASS, inclus dans les 821 : routage, citations, sécurité, knowledge et nouvelles régressions |
| Backend complet | 5 917 PASS, 370 sous-tests PASS, 882 désélectionnés ; 89,50 s |
| Évaluations déterministes enrichies | 122 PASS, 0 échec |
| Frontend | 642 tests PASS ; typecheck, lint et build PASS |
| Dépendances Python | `pip check` PASS |

Les 882 désélections backend incluent les intégrations et les 60 cas manuels de
caractérisation dans `tmp/`. La campagne complète d'intégration sélectionne
explicitement `tests/`. Un avertissement PyJWT sur une clé de test courte ;
`npm ci` signale 12 vulnérabilités modérées du lock existant et deux dépréciations.
Aucun changement de dépendance. Pas de recette navigateur/production exécutée.
L'intégration optionnelle du cycle de vie DEMO est exclue, conformément à la
sélection CI requise ; aucun service DEMO n'est utilisé.

## Caractérisation des deux échecs d'intégration précédents

Les traces conservées du diagnostic localisent les deux arrêts :

- `test_unknown_with_no_observed_commit_is_not_assumed_aborted` : échec de
  `prepare(orders)` ligne 77, **avant** le monkeypatch du commit inconnu.
- `test_changes_between_prepare_confirm[price]` : échec de `prepare(...)` ligne
  322, **avant** la modification du prix et l'oracle de conflit.

Les deux chemins aboutissent à `tenant_unit_of_work.py:177`, traduction en
`TenantOrdersUnavailable` d'un `PyMongoError`, `MigrationError` ou
`TenantContextUnavailable`, avec suppression du contexte par `from None`.
Le type, le code et les labels d'origine sont absents des preuves antérieures :
ni un timeout, ni un conflit de verrouillage, ni une panne réseau ne sont établis.
Ce chemin de préparation n'appelle pas le RAG.

Les durées JUnit des deux cas, setup et teardown inclus, sont **0,343 s** et
**0,423 s** : elles ne correspondent pas à l'épuisement du plafond transactionnel
de 15 s. La campagne intégration ancienne débute le 16/11 à 23:29:15 CET et dure
573,348 s ; la qualification Ollama ancienne ne commence qu'à 23:43:17 CET.
Ces échecs ne sont donc pas contemporains de cette charge Ollama. Ces éléments
écartent deux explications simples, sans identifier l'exception supprimée.

Avant le correctif, les fonctions originales et leurs oracles ont été exécutés
**30 fois chacun** sur des fixtures dédiées : **60 PASS en 22,28 s**. Une observation
externe de `_transaction` conserve uniquement type/code/labels, phase et tentative,
sans message d'exception ni contenu métier. Les 240 événements observés sont les
exceptions `UnknownTransactionCommitResult` volontairement injectées dans le
premier scénario, observées à plusieurs niveaux de propagation ; aucun échec de
préparation reproduit. Les succès suivants ne réécrivent pas la campagne
historique en PASS et ne justifient pas l'étiquette « intermittent ».

La nouvelle campagne complète passe **821/821**, dont ces deux scénarios.
L'observateur confirme zéro exception de préparation pour eux ; il observe les
injections prévues en confirmation et les conflits `WriteConflict` des autres
tests de concurrence, résolus par les mécanismes existants. Aucune intervention
sur les retries ou sur ORDER. La cause profonde ancienne reste inconnue malgré
cette caractérisation du lieu, de la durée et de la phase d'échec.

## Qualification Ollama et corpus dense

Oracles figés avant l'exécution et conservés à l'identique. Corpus uniquement
synthétique, 24 documents tenant A et 12 tenant B, un chunk par document, contexte
réel de trois passages. La question issue des preuves existantes est utilisée
en mémoire ; le rapport et les artefacts n'en exportent que le SHA-256. Aucun
contenu documentaire de production n'est extrait.

Instance Ollama propre à cette tâche, 0.40.16, CPU Linux limité à 10 CPU et 9 Gio,
sans GPU. Images et modèles vérifiés, copie indépendante des blobs. Froid signifie
que `qwen2.5:7b` est absent de `/api/ps` avant le tour ; chaud utilise un nouveau
tour et une nouvelle conversation après ce chargement. Aucun tour historique
n'est rejoué. Aucun appel au provider ou aux bases de production.

Image : `sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592`.
Modèles vérifiés :

- `qwen2.5:7b` : `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
- `nomic-embed-text:latest` : `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`.

| Corpus / état | Durée du tour | Labels / preuves | Résultat |
| --- | --- | --- | --- |
| Un fait, froid | 28,114 s | S1 S2 S3 / 3 | PASS |
| Un fait, chaud | 12,342 s | S1 S2 S3 / 3 | PASS |
| Trois faits par passage, froid | 32,896 s | S1 S2 S3 S1 S2 / 3 | PASS |
| Trois faits par passage, chaud | 20,055 s | S1 S1 S1 S2 S2 / 2 | PASS |

Ces deux tests passent en **105,16 s**, quatre tours indépendants. Pour chaque
passage à plusieurs faits, le SHA-256 du prompt et le JSON brut sont **identiques**
à ceux archivés pendant le diagnostic v0.2.7. À froid comme à chaud, cette même
réponse quittait auparavant `execute` ligne **290**, sans preuve ; elle atteint
maintenant le rendu validé ligne **311**. Le témoin sans répétition a aussi le
même prompt et le même JSON avant/après, et reste accepté.

Les oracles contrôlent l'appartenance de chaque extrait au contenu réellement
fourni, le rendu intégral exact, l'ordre et l'unicité des preuves, l'isolation
organisation, la revalidation des trois passages consultés, la persistance et
la lecture de l'historique. Les faits métier synthétiques restent inchangés.
Le succès ne repose pas sur le seul statut HTTP ou `completed`.

Pour le cas corrigé, Ollama produit 193 puis 192 tokens, `done_reason=stop`.
À froid : chargement **6,588 s**, évaluation du prompt **6,813 s**, génération
**18,818 s**. À chaud : chargement **0,0015 s**, prompt **0,214 s**, génération
**19,218 s**. Les écarts sont des mesures de cette instance CPU isolée ; ils ne
mesurent pas les performances de production.

### Corpus dense : limite conservée, test d'acceptation FAILED

La campagne séparée garde son oracle de réponse documentaire : **1 FAILED** en
120,33 s, sans xfail, relâchement d'assertion ou réparation du JSON. Corpus de dix
étapes par passage ; contexte réel de 1 743 caractères, prompt de 2 836 octets
(869 tokens à chaud), donc très inférieur à la borne d'entrée de 24 Kio.
L'ingestion synthétique prend 5,324 s, séparément des tours ci-dessous.

| État | Observation | Branche / résultat |
| --- | --- | --- |
| Froid | 60,403 s ; `ModelTimeoutError/read_timeout` | dépendance de génération indisponible, `TenantDataUnavailable`, aucune citation |
| Chaud | 51,859 s ; `done_reason=length`, 512 tokens | JSON non parseable, abstention à `execute:264`, aucune citation |

Le journal du serveur situe le chargement froid du modèle à **4,78 s** ; le reste
du délai inclut le prompt et la génération. La réponse froide étant interrompue,
leurs métriques finales ne sont pas disponibles. À chaud : chargement **0,0016 s**,
prompt **0,344 s**, décodage **51,076 s**, environ **10 tokens/s**. Les compteurs
cgroup mesurent **9,34 CPU** utilisés en moyenne à froid et **9,98 CPU** à chaud,
pour une limite de 10 CPU. Cela établit la consommation CPU de cette instance,
sans prédire l'effet d'un autre matériel ni justifier un changement de délai.

Le prompt et le JSON chaud sont identiques à ceux du diagnostic v0.2.7. Deux
objets d'extrait entièrement fermés dans le préfixe sont exacts après NFC/LF ;
le troisième est tronqué. Cette inspection du préfixe reste diagnostique :
aucun JSON n'est complété et aucun extrait partiel n'est publié. La branche de
parsing refuse **avant** la boucle modifiée. Augmenter le délai ne supprimerait
pas la borne indépendante de 512 tokens. Les deux limites restent ouvertes,
sans modification arbitraire du budget, du prompt ou de l'oracle.

Ce FAILED est séparé du PASS du correctif à plusieurs extraits ; il interdit de
présenter toute la qualification documentaire comme verte. Le corpus synthétique
à plusieurs faits constitue la régression représentative ajoutée, sans données
métier. Le corpus dense et son script reproductible sont conservés dans les
preuves pour un travail ultérieur distinct.

## Traçabilité et limites

Preuves locales :
`/home/waelko/projects/stockpilot-rag-repeated-source-evidence/20261116T230201Z`.
Les logs, réponses synthétiques et scripts d'observation restent hors Git.
Les services MongoDB et PostgreSQL utilisent des tmpfs propres au projet
`stockpilot-rag-repeat-fix-20261117`. PostgreSQL est publié sur 45432, le port
55432 étant réservé sur l'hôte ; les fixtures canoniques et un contrôle dédié
vérifient les identités Docker, images, ports loopback, montages et réseau exclusif.
Le vérificateur canonique qui fixe 55432 n'a pas été modifié.

Le lien avec le NO-GO historique reste **non démontré**, car son JSON brut et sa
map de sources ne sont pas disponibles. Ce correctif porte seulement sur le
refus déterministe reproduit. La cause initiale des deux anciens échecs ORDER
reste inconnue. Le corpus dense et les performances CPU sont évalués séparément.


## Commandes et diff

Les endpoints de test ont été explicitement configurés et contrôlés avant toute
fixture. Commandes principales exécutées dans ce worktree :

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --junitxml=tmp/backend-green.xml
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
PYTHONPATH=tmp:tests RAG_CHARACTERIZATION_PATH=tmp/integration-uow-observation.json PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q --tb=short -p no:cacheprovider -p uow_observer -m 'integration and not integration_optional' tests --junitxml=tmp/integration-green.xml
python3 tmp/run_live_qualification.py representative
python3 tmp/run_live_qualification.py dense
```

Les deux nouveaux fichiers pytest ont été exécutés séparément en RED puis GREEN,
avec la sélection `-m integration` pour le second. Les 133 tests ciblés regroupent
`test_rag_repeated_source_excerpts`, `test_tenant_retrieval_excerpt`,
`test_rag_citation_grounding_post_v026`, `test_assistant_v025_citation_retrieval`,
`test_assistant_v025_citation_boundaries`, `test_assistant_v025_rag_security` et
`test_model_resource_limits`. Le frontend a exécuté `npm ci`, `npm test`,
`npm run typecheck`, `npm run lint`, `npm run build`. `git diff --check` passe.
Les résultats ci-dessus sont locaux ; la CI GitHub après push est distincte.

Fichiers de la correction :

- `src/stockpilot/application/services/tenant_retrieval.py` : déduplication des preuves après validation individuelle.
- `tests/test_rag_repeated_source_excerpts.py` : 24 cas unitaires et de sécurité.
- `tests/test_rag_repeated_source_excerpts_integration.py` : trois régressions de persistance, révocation et historique.
- Le présent rapport : invariant, preuves et limites.

La copie initiale du cache n'a pas pu lire quelques métadonnées locales détenues
par root. Aucun ancien cache n'a été modifié : les **11 blobs** copiés ont chacun
été vérifiés par SHA-256, les manifestes par digest, et l'instance isolée a généré
ses propres métadonnées. Cette préparation n'a pas changé les modèles testés.

## Verdict

**READY FOR REVIEW pour le correctif ciblé**, en PR Draft vers `main` seulement.
Le défaut applicatif est reproduit, son correctif minimal est GREEN, les tests
requis et le cas Ollama représentatif passent. Le corpus dense conserve un échec
d'acceptation connu et la cause sous-jacente des deux anciens arrêts ORDER demeure
inconnue. Aucun GO de release ou de déploiement n'est donné par cette revue.

Production v0.2.4 non contactée ; aucun tour historique rejoué, aucune base source
copiée ou restaurée, aucun reset/reseed de source, aucun changement de réseau privé
Ollama. Worktrees anciens, `main` et tags v0.2.5/v0.2.6/v0.2.7 préservés. Les seules
mutations de données concernent les fixtures synthétiques des services jetables.

Après qualification, les quatre conteneurs et les deux réseaux de cette tâche ont
été supprimés par leurs IDs après vérification des labels, images, montages et
appartenances exclusives. Les 202 bases transactionnelles synthétiques laissées
par les fixtures complètes étaient uniquement dans le tmpfs dédié ; PostgreSQL
ne conservait que le schéma `public`. Les caches de modèles indépendants et les
preuves sont conservés. Aucun `prune`, suppression de volume externe ou mutation
d'un service préexistant.
