# Budget de génération documentaire structurée — post #90

Base `main` vérifiée : `60ebddf97fd8260d6024922528a773e6376ef94c`.
Branche `fix/rag-structured-generation-budget`. Aucun déploiement, publication,
changement de modèle, prompt, migration ou configuration de production.

## Diagnostic et correction

La composition `infrastructure/identity/bff_assistant.py` construisait un seul
`OllamaAdapter(config, max_output_tokens=512)`, partagé entre le classificateur,
les autres appels de l'assistant et le retrieval documentaire. Le port `LlmPort`
retourne du texte et accepte déjà un schéma JSON facultatif ; il ne transporte
pas de budget par appel. Aucune configuration documentaire distincte n'existait.
L'adaptateur transmet `num_predict` et `temperature=0` à `/api/generate`.

La correction crée uniquement un second adaptateur à **1024 tokens** pour
`TenantRetrieval`. Le premier reste à **512** pour tous les autres parcours.
Embeddings, modèle, timeout fournisseur 60 s, consultation globale 75 s, borne
d'entrée 24 Kio et port LLM restent inchangés. Le prompt, les cinq extraits
maximum, les bornes 2000 caractères/extrait et 8000 caractères de réponse,
les filtres tenant et les validations de citations ne changent pas.

Lorsqu'une génération structurée reçoit `done_reason=length`, l'adaptateur
vérifie que le texte borné contient un JSON complet. Un résultat incomplet lève
l'erreur existante `ModelResponseDecodeError`, sans publier de préfixe ni
réparer le JSON. La phase et le code `response_decode_error` sont observables
et persistés par le mécanisme existant de consultation échouée. La réponse
publique est `ASSISTANT_UNAVAILABLE`, pas une abstention documentaire.
Un JSON complet au plafond reste soumis à toutes les validations RAG existantes.
Les abstentions explicites complètes et les refus de sécurité restent inchangés.
Cette détection traite la troncature signalée par le fournisseur ; elle ne
redéfinit pas le traitement historique de toutes les sorties malformées non
signalées comme atteignant la limite. Aucun retry automatique ajouté.

Diff applicatif : **16 lignes ajoutées, 1 supprimée dans deux fichiers**.
La ligne remplacée injecte le générateur documentaire à la place de l'instance
partagée ; toutes les autres références restent en place. Aucun composant supprimé.

## RED / GREEN

La fixture `tests/fixtures/rag_dense_budget.json` conserve exclusivement les
passages et réponses synthétiques réellement observés sur RTX 5060/Ollama 0.40.16 :
JSON tronqué à 512 tokens, JSON complet à 676 tokens sous plafond 1024.
Elle ne contient ni question historique en clair, ni document métier.
Le transport HTTP est contrôlé dans les tests déterministes ; il ne simule pas
un tokenizer ni une mesure réelle de performance.

| Tests finaux identiques | Base main / RED | Correctif / GREEN |
|---|---|---|
| Composition, payload fournisseur, cas dense, limites et observabilité | 3 FAIL / 5 PASS | 8 PASS |
| Composition réelle et Mongo/PG dédiés, échec durable ou historique cité, isolation | 2 FAIL | 2 PASS |

Les assertions finies ont les mêmes empreintes entre RED et GREEN. Deux erreurs
initiales du nouveau harnais d'intégration ont été corrigées : la phase est dans
l'enveloppe privée persistée, et l'erreur publique existante est
`ASSISTANT_UNAVAILABLE` plutôt qu'une réponse nulle. La séquence RED/GREEN a été
rejouée ensuite avec ces assertions exactes. Aucun test de régression préexistant
ni oracle métier n'a été affaibli.

Les tests vérifient le `num_predict=1024` effectivement envoyé, les payloads
inchangés du classificateur et des embeddings, l'absence de citations dans
l'échec, la persistance du code/phase, le rendu exact et l'accès inter-tenant refusé.

## Qualification réelle du nouveau code sur RTX 5060

Le harnais utilise **la véritable `compose_assistant` corrigée**, avec migrations
déjà appliquées exclusivement dans les bases de test. Son observateur délègue
au générateur obtenu de cette composition : il ne fixe pas le budget.
Le corps HTTP observé contient `num_predict=1024, temperature=0`.

Corpus inchangé : 24 documents synthétiques org-a et 12 org-b, dix étapes par
passage dense, trois passages retenus. Prompt 2836 octets, contexte 1743 caractères,
869 tokens entrants. Empreinte du prompt identique aux essais CPU/GPU antérieurs :
`b2576dd41b0fb21d4d74a3d644f214e543cf3ce3aa2f92b952a649bef25a0dc9`.
Schéma identique : `0a0e8aad0c475ffa9e404413023dadde628d5134fd378f60d18b4f3486b34d10`.

| État observé | Tour complet | Chargement | Génération | Sortie | Résultat |
|---|---:|---:|---:|---|---|
| Modèle absent avant génération | 15.139 s | 2.076 s | 10.338 s | 676 tokens, `stop` | JSON, 3 extraits / 3 citations, historique PASS |
| Modèle déjà chargé | 12.719 s | 0.003 s | 10.483 s | 676 tokens, `stop` | JSON, 3 extraits / 3 citations, historique PASS |

Deux conversations/tours indépendants. Le premier est un **chargement réel du
modèle naturellement absent**, pas un redémarrage froid de Windows ou du cache.
Aucun déchargement, arrêt partagé, paramètre keep_alive ou modification réseau.
RTX 5060 Laptop GPU, Ollama 0.40.16, Qwen 100 % GPU, contexte 4096.
Digests : Qwen `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`,
nomic `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`.
Pic échantillonné 5129 Mio utilisés, minimum 2771 Mio libres ; mesures globales
du GPU, pas une garantie de pic absolu ou de capacité sous concurrence.
Les tours incluent le relais SSH d'observation ; les temps fournisseur l'excluent.

Le relais local limité au loopback utilise l'empreinte SSH connue et le réseau
BFF existant vers `ollama.stockpilot.internal`. Aucun appel métier de production.
Les trois bases de test sont des conteneurs tmpfs dédiés au projet
`stockpilot-rag-budget-20261117`, ports loopback 27027/27028/45432, identités et
réseau exclusif vérifiés avant les fixtures. PostgreSQL 55432 n'est pas réaffecté.

## Régressions exécutées

| Suite | Résultat |
|---|---|
| Backend déterministe | 5925 PASS + 370 sous-tests ; 824 désélections ; 93.65 s |
| Intégrations obligatoires Mongo/PG/pgvector/LangGraph | 823 PASS ; 555.91 s |
| RAG / sécurité ciblés | 141 PASS, dont les nouveaux tests unitaires |
| Évaluations déterministes enrichies | 122 PASS |
| Frontend | 642 PASS ; typecheck, lint, build PASS |
| Dépendances et diff | `pip check`, `git diff --check` PASS |

La suite d'intégration comprend ORDER, catalogue/stocks, concurrence, autorisations,
historique et RAG. L'intégration optionnelle de cycle DEMO est exclue selon la CI
canonique. Les désélections ne sont jamais comptées comme PASS. Un avertissement
PyJWT sur une clé de test courte ; aucun changement de dépendance.
Le frontend n'est pas modifié ; les contrôles canoniques ont néanmoins été exécutés.
Pas de nouvelle recette navigateur complète, de benchmark de concurrence GPU,
de sauvegarde/restauration de production ou de qualification de release.

## Traçabilité et décision

Preuves privées : `/home/waelko/projects/stockpilot-rag-budget-evidence/20261117`.
Harnais, XML/logs, réponses synthétiques, mesures GPU, empreintes des tests et
revue d'auteur conservés. Les logs ne contiennent pas de question historique
en clair ni de secret. La revue d'auteur vérifie l'isolation du budget, les
frontières de citations et les erreurs ; elle n'est pas une approbation indépendante.
Les six services v0.2.4 conservent leurs IDs/digests/healthchecks/redémarrages et
les modèles leurs digests. Aucune conversation de production créée ou rejouée.

**READY FOR REVIEW** pour ce correctif ciblé, en PR Draft vers `main`.
La CI distante du commit poussé doit être contrôlée séparément de ces résultats
locaux. **Pas de READY FOR DEMO DELIVERY avant fusion validée et CI main conforme.**
Aucun merge, tag, publication GHCR ou déploiement autorisé par ce rapport.
Les deux anciennes exceptions ORDER restent de cause historique inconnue ; les
succès actuels ne les expliquent pas. Le refus v0.2.7 n'est pas attribué
rétrospectivement au seul budget. Les citations prouvent l'appartenance des
extraits et les droits, pas une complétude sémantique universelle.

Commandes principales :

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
# URI de test explicites et cibles vérifiées avant toute fixture :
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -m 'integration and not integration_optional' tests
python3 tmp/run_gpu.py 1024
# L'argument du wrapper nomme le rapport ; le générateur provient du code composé.
(cd frontend && npm ci && npm test && npm run typecheck && npm run lint && npm run build)
git diff --check
```
