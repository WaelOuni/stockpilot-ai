# Réconciliation des contrats RAG — 16 novembre 2026

Ce rapport conserve les étapes et verdicts successifs du chantier. Les mentions
BLOCKED, RED et « non exécuté » ci-dessous décrivent leur étape, pas l'état final.
Le contrat B a ensuite été arbitré et implémenté : voir la
[qualification réelle finale](rag-live-qualification.md) et la
[revue de finalisation](rag-pr-review.md).

Périmètre : worktree `stockpilot-rag-citations`, branche
`fix/rag-citation-grounding-post-v026`, HEAD
`57be3df363f535bd3e330dca71ba5e853907173a`. Tests synthétiques locaux ; aucune
qualification de déploiement. Les points d'entrée mémoire ont été lus dans cette
session ; leurs snapshots datés ne remplacent pas le code examiné.

**Verdict : BLOCKED pour le contrat B**, en attente de l'arbitrage décrit plus bas.
La réconciliation et le retrait ciblé sont terminés. Aucune nouvelle architecture
n'est implémentée. Les deux tests RED de qualité restent exécutés et échouent
explicitement ; aucun skip, xfail ou assouplissement des tests historiques.

## Retrait exact et preuves

Le seul diff applicatif était le bloc ajouté aux lignes 194–206 de
`src/stockpilot/application/services/tenant_retrieval.py`. Ses commentaires,
sa liste de phrases et son refus général ont été retirés ensemble : aucune autre
référence à ces variables locales n'existait. Les contrôles d'origine restent en
place. Le fichier est désormais identique à HEAD, donc son diff Git final est vide.

Diff de cette intervention par rapport à son état initial :

```diff
         if cited - sources.keys():
             return RAG_CITATION_REFUSAL
-        # An appendix is server-generated provenance, not a model citation.
-        # Allow a small, explicit abstention vocabulary without citations; any
-        # substantive answer must cite a retrieved passage in its own prose.
-        normalized_result = result.casefold()
-        abstention = any(phrase in normalized_result for phrase in (
-            "les documents ne précisent pas", "les documents ne mentionnent pas",
-            "les documents n'indiquent pas", "les documents n’indiquent pas",
-            "information indisponible", "information non disponible",
-            "je ne peux pas répondre à partir des documents",
-            "je ne peux pas repondre a partir des documents",
-        ))
-        if result.strip() and not cited and not abstention:
-            return RAG_CITATION_REFUSAL
         provenance = '\n'.join(f'[{label}] {self.source_identifier(p.document_id)}, passage {p.chunk_id}' for label,p in sources.items())
```

Les preuves locales sont conservées hors Git dans
`/tmp/stockpilot-rag-contract-reconciliation/` :

- `manifest.json` : HEAD, chemins et SHA-256 des deux fichiers avant intervention.
- `before-tenant_retrieval.py`, `before-test_rag_citation_grounding_post_v026.py`,
  `before-application.diff` : copies de l'état provisoire avant retrait.
- `before-13-failures.txt` : copie sans modification du log antérieur
  `/tmp/stockpilot-rag-citation-13-failures.txt` ; l'original est conservé.
- `historical-results.txt` : le premier RED (4 failed, 3 passed en 0,94 s) est
  attesté par la conversation ; aucun log brut sauvegardé de ce premier run n'a
  été identifié. Il ne faut pas le confondre avec un nouveau run.
- `application-removal.diff`, `test-reconciliation.diff` : diffs exacts avant/après.
- `after-application-vs-HEAD.diff` : vide, attestant l'absence de correctif
  applicatif restant.
- `after-removal.txt`, `after-test-reconciliation.txt` : résultats ciblés successifs.
- `regressions*.txt`, `http-*.txt`, `subprocess-import.txt` : validations et
  incidents d'environnement, sans données de bases ni appel de modèle réel.

## Résolution des 13 échecs

Préfixes : **N** = `test_rag_citation_grounding_post_v026.py`, **C** =
`test_assistant_v025_citation_retrieval.py`, **S** =
`test_assistant_v025_rag_security.py`.

| Test | Entrée et attendu | Cause observée avec la règle provisoire | Résolution |
|---|---|---|---|
| N `test_factual_documentary_answer_without_citations_has_empty_cited_sources` | « La livraison prend sept jours ouvrés. », publiée sans preuve citée | Refus anticipé ; 0 revalidation au lieu des 2 exigées par la fixture | Caractérisation historique restaurée, PASS |
| N `test_factual_answer_without_valid_citation_uses_explicit_refusal_fallback` | Même fait, attente de repli explicite | La fixture masquait le résultat en exigeant 2 revalidations | Fixture corrigée ; attente B toujours FAIL sur la réponse, sans erreur technique |
| N `test_unknown_or_factually_mismatched_citation_is_rejected[S99]` | Fait avec `[S99]`, refus attendu | Le rejet correct précédait la revalidation exigée par la fixture | Erreur de fixture corrigée, PASS |
| N `test_unknown_or_factually_mismatched_citation_is_rejected[S1]` | Fait livraison avec `[S1]`, refus demandé à tort | S1 est précisément le passage livraison autorisé ; l'application l'acceptait | Erreur d'attendu synthétique corrigée, PASS |
| N `test_markers_only_in_server_appendix_do_not_justify_fact` | Fait sans citation du modèle, repli attendu | Fixture exigeant revalidation et annexe même pour un refus | Fixture corrigée ; aucune fausse citation créée, mais attente B toujours FAIL |
| C `test_server_consulted_footer_does_not_create_a_model_citation` | « Les informations sont présentes dans les documents. », annexe mais aucune citation | Refus général supprimant annexe et sources consultées | Retrait de la règle ; test historique inchangé, PASS |
| S `test_sensitive_passage_and_metadata_never_reach_generator` | « Tous les six mois. », seuls passages sûrs transmis | Filtrage de sécurité correct, mais réponse non citée rejetée ensuite | Retrait ; filtrage et assertion historique conservés, PASS |
| S `test_legitimate_concepts_and_product_questions_are_not_credentials[clé API]` | Question de renouvellement ; modèle « Tous les six mois. » | Rejet de citation sans rapport avec le contrat de détection des secrets | Retrait, PASS |
| S `test_legitimate_concepts_and_product_questions_are_not_credentials[mots de passe]` | Question de protection ; même modèle | Même cause | Retrait, PASS |
| S `test_legitimate_concepts_and_product_questions_are_not_credentials[maintenance]` | Fréquence de maintenance ; même modèle | Même cause | Retrait, PASS |
| S `test_legitimate_concepts_and_product_questions_are_not_credentials[secrets de fabrication]` | Question produit ; même modèle | Même cause | Retrait, PASS |
| S `test_unknown_arbitrary_unlabelled_string_is_not_claimed_detectable` | Texte arbitraire sans signature de secret, publié historiquement | Une absence de marqueur devenait un motif général de refus | Retrait ; limite du détecteur préservée, PASS |
| S `test_final_response_bound_includes_server_provenance` | `x` répété 8000 fois ; erreur de taille publique attendue | Refus anticipé court-circuitant le contrôle après ajout de l'annexe | Régression de borne résolue par retrait, PASS |

Le précédent qualificatif « quatre échecs attendus » était trop large : un cas
échouait à cause de la fixture et un autre avait un attendu erroné pour S1.
Les anciens tests de sécurité ne prouvent pas la pertinence sémantique de leurs
réponses simulées. Leur texte non cité entre en conflit avec un éventuel futur
contrat documentaire strict, mais leur couverture de sécurité doit être conservée.

## Changements de tests et justification

Seul le nouveau module N est modifié ; les deux modules historiques C et S ont été
comparés octet par octet à HEAD et sont inchangés.

| Changement | Catégorie et justification |
|---|---|
| `exercise()` renvoie réponse, passages et repository sans imposer revalidation, intention ou annexe | Erreur de fixture : un refus légitime peut survenir avant les lectures de provenance. Les assertions de revalidation sont déplacées dans les branches acceptées, pas supprimées de la couverture. |
| Caractérisation initiale conservée avec assertions sur prose, annexe, `cited_sources=[]`, absence d'ordre et sources relues | Objectif historique explicité ; ce PASS démontre le comportement actuel et ne certifie pas B. |
| Cas abstention et réponse citée adaptés au nouveau retour de la fixture | Erreur de fixture uniquement ; attentes fonctionnelles inchangées. |
| Paramètres S99/S1 séparés par un attendu d'acceptation explicite | Attendu synthétique incorrect corrigé : S1 doit être accepté, S99 refusé. Aucun changement de code pour obtenir ces PASS. |
| Test d'annexe ne réclame plus simultanément une annexe et un texte égal au refus | Erreur de fixture/attendu contradictoire : l'annexe est déjà vérifiée dans la caractérisation et C. Le test conserve `cited_sources=[]` puis le repli B qui échoue. |
| Deux attentes de `RAG_CITATION_REFUSAL` restent RED et documentées comme provisoires | Évolution fonctionnelle à valider ; ni supprimée ni approuvée implicitement. |
| Ajout `test_current_reference_integrity_does_not_establish_semantic_support` | Nouvelle caractérisation : livraison attribuée à S2, qui concerne les archives. Le PASS montre la limite sémantique actuelle, pas un comportement à pérenniser. |

Lors d'une future implémentation B, la caractérisation de l'ancien comportement et
son inverse ne pourront pas être simultanément des critères GREEN de la même
exécution. Il faudra conserver cette preuve historique et approuver explicitement
l'évolution des assertions d'acceptation ; ne pas utiliser xfail pour cacher ce conflit.

## Contrats A et B

**A — Intégrité des citations** : les permissions et l'organisation sont contrôlées
avant publication ; les labels reconnus désignent uniquement les passages retenus.
Les preuves persistables restent les identités serveur
`organization_id/document_id/generation_id/chunk_id/content_sha256`, revalidées
sur les sources actives. Une source consultée ne devient pas une source citée.
Les limites de taille et le filtrage des secrets sont indépendants de la qualité.

Le parseur extrait les marqueurs du résultat du modèle **avant** l'ajout de
l'annexe serveur. Cette annexe ne peut donc créer une citation. En revanche,
un modèle peut écrire lui-même une liste « Sources : [S1] » : le parseur actuel
inspecte tout son texte et ne distingue pas une telle liste d'une citation liée
à une affirmation. La garantie actuelle ne doit pas être étendue à ce cas sans test
et sans structure explicite liant l'affirmation à sa preuve.

**B — Qualité documentaire** : l'attribution d'une source active ne prouve ni
la pertinence pour la question, ni le soutien de chaque affirmation. Le fait
initial sur la livraison est bien présent dans le corpus synthétique : le défaut
reproduit est son absence d'attribution, pas une hallucination démontrée.
Le cas S2 démontre, lui, une attribution reconnue sans soutien dans le passage cité.
L'absence de marqueur ne permet pas de classifier un texte comme factuel,
une abstention ou une instruction de sécurité. Aucun vocabulaire fermé ne doit
remplacer cette distinction.

| Branche | Comportement et politique de repli |
|---|---|
| Fait avec référence reconnue | A revalide son identité ; B reste non démontré. Ne jamais déclarer le fait vérifié sur cette seule base. |
| Fait sans référence | A conserve `cited_sources=[]`. B proposé : ne pas publier le fait comme étayé ; rendre une abstention neutre calculée côté serveur, sans inventer de fait. |
| Abstention légitime | Peut être rendue sans citation. Ce n'est ni une fuite ni une référence invalide ; `RAG_CITATION_REFUSAL` n'est pas le bon diagnostic par défaut. |
| Refus de sécurité | Conserver `RAG_SENSITIVE_REFUSAL` et les contrôles existants avant/après génération. Aucun signal du modèle ne les contourne. |
| Réponse déterministe catalogue/stock/ORDER | Hors de cette politique documentaire ; autorité des stores et garanties transactionnelles conservées. |
| Référence inconnue/invalide/retirée | `RAG_CITATION_REFUSAL` convient ; ne publier ni contenu ni preuves devenues invalides. Une révocation de permission reste `AuthorizationDenied`, pas un succès avec une autre formulation. |
| Marqueurs uniquement dans l'annexe serveur | Sources consultées uniquement, aucune preuve citée ; politique B identique au cas sans attribution. |
| Sortie malformée, trop longue, panne fournisseur | Conserver une erreur de dépendance/validation assainie ; ne pas la convertir en absence de preuve ou en succès. |

Le refus existant dit « contient une référence non vérifiable » : il convient à
une référence invalide, mais ne décrit pas précisément une abstention, l'absence
de citation ou une insuffisance sémantique. Les deux RED actuels gardent ce texte
pour préserver la proposition antérieure ; ils ne fixent pas définitivement le
libellé métier. Proposition neutre à arbitrer : « Je ne dispose pas d'éléments
documentaires suffisamment étayés pour répondre à cette question. » Ce texte ne
prétend pas qu'aucun document n'existe et ne reprend aucun fait libre du modèle.

## Proposition minimale, non implémentée

Le port `LlmPort.generate(prompt, response_format=...) -> str` et l'adaptateur
Ollama prennent déjà en charge un schéma de sortie. La composition B2B utilise
`TenantRetrieval`; elle limite actuellement la génération à 512 tokens et le
prompt à 24 Kio. Il n'est pas nécessaire d'introduire un autre moteur, un service
ou un changement du classifieur partagé pour essayer un format documentaire.

Proposition : un DTO interne versionné, parsé strictement dans le seul chemin
documentaire, avec une union de trois intentions **déclarées** :

- `document_answer` : liste bornée d'affirmations et, pour chacune, labels et
  extraits de preuve. Pas de paragraphe libre supplémentaire hors de cette liste.
- `insufficient_evidence` : aucune affirmation ni citation ; rendu neutre serveur.
- `security_refusal` : aucune affirmation ni citation ; la déclaration ne prouve
  pas un incident de sécurité. Seuls les contrôles serveur déterminent son motif ;
  sans motif corroboré, utiliser une abstention neutre plutôt qu'une accusation.

Politique proposée, dans cet ordre :

1. Conserver authentification, portée tenant, filtrage des documents et protection
   des secrets avant tout appel. Ne transmettre que les passages autorisés.
2. Borner la sortie et valider JSON/types/discriminant/champs supplémentaires,
   cardinalités et longueurs. Rejeter les sorties ambiguës ou malformées ; aucune
   extraction opportuniste d'un fragment JSON dans un texte libre.
3. Pour chaque affirmation, résoudre les labels dans la table locale des passages.
   Contrôler que l'extrait existe exactement dans le passage indiqué et vérifier
   génération, hash, organisation et permissions à nouveau avant publication.
   Ces contrôles prouvent l'origine du texte, pas sa vérité ni sa pertinence.
4. Construire les citations serveur uniquement depuis les preuves réellement
   associées aux éléments publiés. Conserver séparément tous les passages lus.
   L'annexe, les titres et les champs ajoutés par le modèle ne créent pas de preuve.
5. Choisir explicitement la politique sémantique ci-dessous. À défaut de soutien
   qualifié, rendre l'abstention neutre ; ne pas reprendre une affirmation douteuse.
6. Contrôler la sécurité et la taille de la sortie finale, annexe comprise.
   Un appel de génération borné ; aucune régénération automatique. Toute extension
   vers un évaluateur sémantique doit avoir un budget fini distinct et revalider
   les droits après l'appel, avant publication.

**Option recommandée pour une première implémentation bornée : restitution
d'extraits vérifiés.** Le modèle sélectionne des passages ; le serveur affiche
les extraits complets autorisés ou des unités dont les bornes sont déterminées par
le serveur, avec attribution explicite. Ne pas autoriser le modèle à retirer une
négation ou des conditions en choisissant une sous-chaîne. Ne pas afficher sa
reformulation libre comme un fait vérifié. La sortie promet « le document dit »,
pas la vérité du document ni une synthèse exhaustive pertinente. La pertinence
des sélections reste à qualifier sur un corpus synthétique annoté.

**Alternative : synthèse libre.** La relation affirmation/preuve, les négations,
chiffres, unités, dates, conditions et contradictions demandent une qualification
sémantique. Un second modèle ou un score auto-déclaré n'est pas une preuve
suffisante. Définir un jeu annoté indépendant, seuils de faux soutien, cas adverses,
traitement de l'incertitude et repli neutre avant d'autoriser ce mode. Une absence
de référence ou une simple égalité lexicale ne remplace pas cette qualification.

### Impacts à qualifier avant implémentation

- **Application** : DTO/validation/rendu locaux au RAG ; préserver le retour public
  et les collecteurs existants si possible. Aucun changement ORDER ou du prompt
  de classification partagé, aucun changement de modèle par défaut.
- **Fournisseur** : utiliser l'argument existant `response_format` au seul appel
  documentaire. Qualifier JSON tronqué avec 512 tokens, erreurs et bornes sans
  augmenter silencieusement les budgets ni changer les adaptateurs historiques.
- **Historique et checkpoints** : `TenantAssistantCitations.public_reply()` contient
  une branche explicite pour les refus sans provenance. Un nouveau repli neutre
  doit être reconnu sans accepter toute prose arbitraire sans preuve. Préférer un
  motif/une version calculés serveur ; ne pas faire confiance à un drapeau du modèle.
  Les anciens tours restent lisibles avec leurs contrôles existants, sans réécriture
  ni attribution rétroactive. Un ancien tour ne devient pas sémantiquement vérifié.
- **HTTP/Vue** : `public_turn()` publie une liste fermée de champs ; Vue décode
  notamment `intent` et `text`, pas un état de vérification sémantique. Un DTO
  strictement interne peut préserver ce contrat. Un badge ou statut public demanderait
  un changement explicite des sérialiseurs et tests de lecture, pas un champ implicite.
- **Rollback** : si une métadonnée privée est persistée, version inconnue = aucune
  promotion en preuve. L'ancienne version peut masquer un nouveau repli comme source
  indisponible ; tester cette compatibilité avant toute activation. Aucun DDL ou
  migration de données ne sont proposés pour cette session.
- **Tests** : garder séparés les tests d'intégrité A, de refus de sécurité et de
  qualité B. Une évolution des anciennes réponses libres doit préserver des
  assertions équivalentes sur filtrage, révocation, taille et autorisations, et être
  explicitement revue. Ajouter notamment JSON malformé, abstention avec fait ajouté,
  faux refus de sécurité, référence étrangère, annexe du modèle seule, preuve retirée,
  négation amputée, chiffre modifié, soutien partiel et erreur de fournisseur.

### Arbitrage métier requis

Choisir entre une restitution d'extraits attribués avec promesse limitée et une
synthèse libre exigeant une qualification sémantique supplémentaire. Valider le
repli neutre et la transition des anciens tests de prose libre. Tant que cette
décision n'est pas acquise, les deux tests B restent RED : **BLOCKED pour B**,
sans prétendre que l'intégrité A assure la qualité documentaire.

## Commandes et résultats réellement obtenus

Commande ciblée, exécutée deux fois (avant puis après correction des tests) :

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src tests/test_rag_citation_grounding_post_v026.py tests/test_assistant_v025_citation_retrieval.py tests/test_assistant_v025_rag_security.py
```

| Étape | Résultat | Preuve |
|---|---|---|
| Provisoire, avant cette intervention | 13 failed, 50 passed en 1,20 s | `before-13-failures.txt` |
| Retrait du bloc, tests inchangés | 4 failed, 59 passed en 0,98 s | `after-removal.txt` |
| Réconciliation des fixtures et du cas S1, ajout de la caractérisation S2 | 2 failed, 62 passed en 1,15 s | `after-test-reconciliation.txt` |

Les deux échecs restants sont
`test_factual_answer_without_valid_citation_uses_explicit_refusal_fallback` et
`test_markers_only_in_server_appendix_do_not_justify_fact`. Ils atteignent leurs
assertions fonctionnelles ; aucun échec de fixture ne les masque. Le nouveau
module a 6 PASS et 2 FAIL ; les deux modules historiques ont ensemble 56 PASS.

Commande élargie synthétique, avec le chemin source transmis aux sous-processus :

```bash
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 60s .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src tests/test_assistant_v025_citation_retrieval.py tests/test_assistant_v025_rag_security.py tests/test_assistant_v025_citation_boundaries.py tests/test_assistant_documentary_routing.py tests/test_b2b_assistant_contract.py tests/test_tenant_knowledge_contracts.py tests/test_tenant_authorization.py tests/test_tenant_catalogue_contracts.py tests/test_tenant_orders_contracts.py tests/test_catalogue_advice.py tests/test_catalogue_advice_v13c1.py tests/test_catalogue_advice_v1331.py tests/test_order_quantity_security.py tests/test_order_confirmation_context.py tests/test_order_product_identity.py tests/test_order_transaction.py
```

Incidents d'environnement conservés :

- Première sélection élargie bloquée dans la sandbox et interrompue (code 130,
  `regressions.txt`). Sélection sans module HTTP également interrompue (code 130,
  `regressions-without-http.txt`) ; ces runs partiels ne sont pas comptés comme PASS.
- Module `test_b2b_assistant_contract.py` isolé avec `timeout 15s` et
  `-o faulthandler_timeout=5` : blocage dans `TestClient.__enter__`/AnyIO,
  terminaison forcée code 137 (`http-diagnostic.txt`). Même module hors sandbox,
  autorisé et borné à 30 s : **27 passed en 1,73 s** (`http-outside-sandbox.txt`).
- Sélection complète hors sandbox sans `PYTHONPATH` exporté : **1 failed,
  857 passed en 6,16 s** (`regressions-outside-sandbox.txt`).
  `test_imports_never_create_clients` lance un sous-processus qui ne reçoit pas
  l'option pytest `pythonpath=src` ; `ModuleNotFoundError: stockpilot`.
- Ce seul test relancé avec `PYTHONPATH` ci-dessus : **1 passed en 0,99 s**
  (`subprocess-import.txt`). Aucun changement du test ni de l'environnement installé.
- Relance finale complète avec `PYTHONPATH`, hors sandbox et autorisée :
  **858 passed en 6,71 s**, code 0 (`regressions-final.txt`). Elle comprend les
  56 tests historiques citations/sécurité déjà exécutés ; les totaux des runs
  ne doivent donc pas être additionnés comme des tests distincts.

Les sélections n'utilisent ni services DB ni Ollama réel. Tests d'intégration,
frontend, évaluations de modèle et revue indépendante non exécutés ; aucune
conclusion sur l'atomicité réelle inter-stores ou la qualité d'Ollama n'en découle.
Les fichiers applicatifs et les deux modules historiques restent identiques à HEAD.

## Mise à jour — validateur d'extraits structuré

Décision de reprise : ne publier que des extraits documentaires littéraux
attribués. `TenantRetrieval.validate_excerpt(context, authorized_sources,
source_id, excerpt)` exige une entrée de la map `Sx` de la recherche tenant
courante, l'organisation attendue, une identité de passage bornée, un extrait
non vide de 1–2000 caractères, non sensible et présent après normalisation NFC
et conversion CRLF/CR vers LF. Casse, espaces et ponctuation restent exacts.
La méthode retourne la référence stable du passage ; elle ne certifie aucun sens
qu'un texte libre pourrait exprimer.

La génération documentaire utilise désormais l'argument `response_format` existant
pour demander `answerable` et jusqu'à cinq couples `source_id`/`excerpt` stricts.
Le serveur rejette les propriétés dupliquées et les formes malformées, revalide
les passages et les permissions, garde distinctes les sources lues et les
citations retenues, et ne rend que les extraits vérifiés. L'abstention utilise le
message neutre partagé par la projection d'historique. Les refus de secrets,
références inconnues, erreurs fournisseur et bornes demeurent distincts. Il n'y a
ni paraphrase affichée ni annexe de documents consultés.

Tests du module de contrat et du validateur (avec frontières d'historique et
payload Ollama simulé) : **53 passed**. La sélection de régression finale a donné
**876 passed, 21 failed**. Tous les échecs sont dans les modules historiques
`test_assistant_v025_citation_retrieval.py` et
`test_assistant_v025_rag_security.py` : leurs générateurs simulés renvoient du
texte libre ou leurs assertions exigent l'ancienne annexe. Aucun de ces fichiers
n'a été modifié. Ce conflit de contrat bloque leur migration contrôlée et donc la
PR ; les autres parcours de la sélection, dont catalogue, ORDER et isolation
tenant, passent.

La preuve de l'état final est `final-structured-regressions.txt`. La qualification
Ollama n'a pas été lancée : les variables dédiées `B2B_TEST_OLLAMA_URL`,
`MONGO_TEST_URI` et `POSTGRES_TEST_URI` sont absentes. Les tests d'intégration
nécessitant ces services sont non exécutés ; aucune cible ni base n'a été contactée.
La branche n'a pas été commitée ni poussée et aucune PR n'a été créée, car les
tests déterministes ne sont pas tous verts. Aucun tag ou déploiement n'a été fait.
## Qualification détaillée des 21 échecs après contrat structuré

Reproduit exactement avec la sélection complète précédente le 16 novembre 2026.
Log brut : `/tmp/stockpilot-rag-contract-reconciliation/classification-rerun.txt`.
Résultat : **876 passed, 21 failed, 0 skipped**. Aucun test n'a été modifié avant
ce classement.

| # | Test / fichier | Entrée simulée ; ancien attendu → obtenu | Invariant réel / cause | Classe |
|---:|---|---|---|:---:|
| 1 | `test_only_actually_cited_sources_have_stable_references_and_consulted_is_separate` — citations | `La livraison prend sept jours [S2].` ; prose + référence → abstention neutre | Mock ignorait `response_format`, aucun extrait structuré transmis | C |
| 2 | `test_multiple_citations_preserve_exact_passages_without_consulted_only_source[Stock quotidien [S1] et livraison sept jours [S2].]` — citations | Prose libre S1/S2 ; deux citations → aucune | Payload simulé hors schéma, avant validation des références | C |
| 3 | `test_multiple_citations_preserve_exact_passages_without_consulted_only_source[Stock quotidien [source: S1], livraison sept jours, document S2.]` — citations | Variante libre S1/S2 ; deux citations → aucune | Même mock texte obsolète, pas une régression de l'allowlist | C |
| 4 | `test_server_consulted_footer_does_not_create_a_model_citation` — citations | Phrase libre sans citation ; footer `[S1]` attendu → abstention sans footer | Ancienne annexe serveur explicitement incompatible avec le rendu structuré | A |
| 5 | `test_unknown_model_reference_cannot_create_proof[Inventé [S99].]` — citations | S99 en prose ; refus de référence → abstention faute de JSON | S99 n'était pas envoyé dans `source_id` structuré | C |
| 6 | `test_unknown_model_reference_cannot_create_proof[Inventé [source: S99].]` — citations | Variante syntaxique S99 en prose ; même attendu → même obtenu | Payload texte ne teste pas la branche inconnue du parseur | C |
| 7 | `test_unknown_model_reference_cannot_create_proof[Inventé selon le document S99.]` — citations | S99 mentionné en prose ; même attendu → même obtenu | Même cause de fixture | C |
| 8 | `test_source_changed_during_generation_blocks_prose_and_all_provenance[deleted]` — citations | Source supprimée pendant génération ; refus → abstention avant revalidation | Mock prose libre S2 court-circuitait le contrôle post-génération | C |
| 9 | `test_source_changed_during_generation_blocks_prose_and_all_provenance[replacement]` — citations | Génération remplacée pendant génération ; refus → même abstention | Extrait structuré S2 requis pour atteindre le test de révocation | C |
| 10 | `test_source_changed_during_generation_blocks_prose_and_all_provenance[content]` — citations | Contenu modifié pendant génération ; refus → même abstention | Même fixture obsolète; contrôle d'identité/hachage non atteint | C |
| 11 | `test_source_changed_during_generation_blocks_prose_and_all_provenance[secret]` — citations | Passage remplacé par une valeur sensible ; refus → même abstention | Même cause; couverture anti-fuite doit rester active après génération | C |
| 12 | `test_legacy_adapter_without_generation_cannot_create_historical_proof` — citations | Texte « sept jours », passage sans `generation_id` ; ancienne prose → abstention neutre | Sans identité génération stable, aucune preuve historique n'est possible | A |
| 13 | `test_concurrent_calls_have_separate_citation_collectors` — citations | Deux réponses texte libres distinctes ; deux collecteurs → deux abstentions | Les deux branches du mock utilisaient l'ancien protocole | C |
| 14 | `test_sensitive_passage_and_metadata_never_reach_generator` — sécurité | « Tous les six mois » ; réponse + footer → abstention | Fixture sûre non structurée; filtrage des passages sensibles n'était pas fautif | C |
| 15 | `test_legitimate_concepts_and_product_questions_are_not_credentials[Comment renouveler une clé API sans révéler sa valeur ?]` — sécurité | « Tous les six mois » libre ; réponse → abstention | Ancien mock texte; la détection de secrets n'est pas une règle de citation | C |
| 16 | `test_legitimate_concepts_and_product_questions_are_not_credentials[Comment protéger les mots de passe ?]` — sécurité | Même sortie libre ; réponse → abstention | Même cause; assertion de génération unique conservée | C |
| 17 | `test_legitimate_concepts_and_product_questions_are_not_credentials[Quelle est la fréquence de maintenance ?]` — sécurité | Même sortie libre ; réponse → abstention | Même cause; question bénigne non classée comme secret | C |
| 18 | `test_legitimate_concepts_and_product_questions_are_not_credentials[Quels sont les secrets de fabrication de cette perceuse ?]` — sécurité | Même sortie libre ; réponse → abstention | Même cause; fixture structurée utilise l'extrait littéral sûr | C |
| 19 | `test_unknown_arbitrary_unlabelled_string_is_not_claimed_detectable` — sécurité | Texte arbitraire non signé ; ancien rendu → abstention neutre | Ancien oracle publiait du texte libre; nouveau contrat l'abstient sans le classifier comme fuite | C |
| 20 | `test_citation_labels_must_exist_and_metadata_cannot_forge_source_labels` — sécurité | S99 et citation en prose ; refus/ancien rendu → abstention JSON malformé | Mock doit porter S99 en `source_id`, puis S1 avec extrait exact; échappement metadata conservé | C |
| 21 | `test_final_response_bound_includes_server_provenance` — sécurité | 8000 `x` ; dépassement du rendu avec annexe → pas de dépassement | Mesurait l'annexe supprimée; le JSON structuré surdimensionné teste la borne fournisseur | A |

Aucun de ces échecs n'indique un contournement d'autorisation, une fuite d'un
passage filtré, une citation d'un autre tenant, une mutation ORDER ou une panne
de dépendance mal traitée. Les tests C utilisent le port de génération et doivent
être adaptés au schéma convenu en gardant leurs vérifications métier. Pour A,
l'ancien attendu est incompatible avec le nouveau rendu; le test est remplacé par
une assertion de contrat structurée équivalente, sans préserver l'annexe.

## Réconciliation et qualification — 16 novembre 2026

Après ce classement seulement, les fixtures des 21 cas ont été converties vers
le format structuré borné (`answerable` et couples référence/extrait). Les tests
historiques de sécurité n'ont pas été supprimés, ignorés ou marqués xfail. Le test
du footer affirme maintenant qu'une abstention ne publie ni prose factuelle ni
annexe; la caractérisation du comportement antérieur est conservée dans le module
`test_rag_citation_grounding_post_v026.py`. Les cas de révocation continuent de
modifier la source pendant la génération et exigent un refus sans provenance.
Les assertions de non-fuite des passages/identifiants sensibles, de réévaluation
des autorisations, de référence inconnue et de limites restent présentes.

Justification des réconciliations :

- Groupe A (tests 4, 12 et 21) : les attentes d'annexe ou de prose libre ont été
  remplacées par les assertions du contrat structuré. Le cas historique sans
  génération stable s'abstient sans preuve. Le test de borne envoie désormais une
  sortie JSON structurée surdimensionnée et vérifie l'erreur fournisseur assainie.
- Groupe C (les 18 autres échecs) : les mocks obsolètes renvoient maintenant des
  payloads structurés déterministes et des extraits exacts. Les assertions de
  citation, d'isolation, de révocation et de sécurité restent vérifiables. Les
  trois paramètres de l'ancien test de référence inconnue sont devenus des formes
  structurées avec source `S99`; l'identité `S1` synthétique correspond au passage
  autorisé dans le fixture.
- Groupe B : aucun des 21 échecs n'a été qualifié de régression réelle. Aucun
  changement applicatif n'a été effectué pendant cette réconciliation; les trois
  modifications applicatives visibles dans le worktree précèdent ce travail.

Le diff complet des deux modules de fixtures est conservé dans le worktree. Il
montre la transformation des générateurs simulés et des assertions de rendu; il
n'y a ni suppression de test ni modification de code pour faire passer ces tests.

### Commandes et résultats après réconciliation

La commande élargie précédente a été relancée à l'identique après la
classification et les ajustements de fixtures. Elle vise les 19 modules listés
dans la section « Commandes et résultats réellement obtenus » ci-dessus.

| Commande / portée | Résultat | Preuve |
|---|---:|---|
| Sélection élargie exacte (citations, sécurité, routage documentaire, autorisations tenant, catalogue, stock et ORDER) | **897 passed, 0 failed, 0 skipped**, 8,09 s | `/tmp/stockpilot-rag-contract-reconciliation/final-after-test-reconciliation.txt` |
| Citations, sécurité RAG, nouveau contrat, validation des extraits, historique, stock, ORDER status (hors test bloquant) et cycle de vie ORDER | **271 passed, 0 failed, 1 deselected**, 2,13 s | sortie pytest de la session, code 0 |
| Historique final/ORDER | **65 passed**, 0,93 s | commande ciblée du module |
| Contrats stock tenant | **61 passed**, 0,37 s | commande ciblée du module |
| Cycle de vie ORDER | **38 passed**, 0,80 s | commande ciblée du module |

Deux modules complémentaires n'ont pas été qualifiés complètement :
`tests/test_tenant_bff_context.py` expire à 35 s sur
`test_valid_bff_context_allows_its_explicitly_owned_resources`; le diagnostic
verbose confirme le blocage dès l'entrée dans ce premier test. Dans
`tests/test_order_status_workflow.py`, les 17 premiers tests passent, puis
`test_status_graph_never_calls_generation_stock_or_write_use_cases` ne termine
pas sous 18 s. Les deux commandes ont été interrompues par `timeout` (code 124),
et ne sont comptées ni comme PASS ni comme FAIL. Dans la sélection ORDER combinée,
ce seul test a été explicitement désélectionné; la raison est consignée ici.

`git diff --check` passe. État des trois variables vérifié sans afficher de
valeur : `B2B_TEST_OLLAMA_URL`, `MONGO_TEST_URI` et `POSTGRES_TEST_URI` sont
absentes. Le runbook `docs/testing.md` exige les deux URI d'intégration dédiées,
leur vérification par `scripts/ci/check_targets.py`, puis une URL Ollama explicitement
choisie pour le test réel. Rien n'indique de cible isolée déjà configurée et aucune
valeur n'a été inventée. Ni Compose, ni base, ni serveur Ollama n'ont été démarrés
ou contactés. Qualification réelle Ollama et tests marqués `integration` :
**non exécutés**, prérequis manquants.

### Verdict borné

**GREEN DÉTERMINISTE** pour la sélection élargie effectivement exécutée (897/0/0)
et la sélection ciblée (271/0/1 explicitement désélectionné). Ce verdict ne
qualifie pas les deux modules expirés, l'intégration contre des bases dédiées, ni
la qualité réelle d'un modèle Ollama. Aucune qualification de déploiement, PR,
commit, push ou opération de production n'a été effectuée.

## Suite — diagnostic des deux timeouts

Les deux timeouts complémentaires sont désormais expliqués et levés dans
l'environnement d'exécution autorisé : la sandbox refusait le `send()` sur
la paire de sockets de réveil asyncio (`EPERM`). Le diagnostic a capturé
les piles et les tâches en attente ; les modules RAG modifiés ne sont pas
importés par ces tests. Aucun changement applicatif, de fixture ou d'oracle
n'a été nécessaire.

Les deux tests passent isolément, puis la sélection élargie repasse à
**897 PASS / 0 FAIL / 0 SKIP**, les modules BFF à **140 PASS**, ORDER_STATUS à
**55 PASS**, et les régressions RAG à **310 PASS**. Aucune désélection dans ces
validations. Le blocage déterministe précédent est levé ; les qualifications
Ollama et intégrations réelles restent distinctes et non exécutées.
Voir [le diagnostic complet et les commandes](rag-timeout-diagnostics.md).
