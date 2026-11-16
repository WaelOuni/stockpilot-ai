# Diagnostic ciblé des deux timeouts — 16 novembre 2026

Rapport historique de cette étape. La qualification réelle, alors non exécutée,
a depuis été réalisée séparément : voir la
[qualification finale](rag-live-qualification.md). Le diagnostic et les preuves
ci-dessous sont conservés ; ils n'ont pas été relancés pour préparer la PR.

Worktree : `/home/waelko/projects/stockpilot-rag-citations` ; branche
`fix/rag-citation-grounding-post-v026`. Le SHA testé est conservé dans
`tmp/rag-timeout-diagnostics-20261116/head.txt`.

**Verdict : GREEN DÉTERMINISTE pour le périmètre demandé.** Les deux tests,
la sélection élargie, les modules BFF, ORDER_STATUS et les régressions RAG
passent hors sandbox. Zéro FAIL, SKIP ou désélection dans ces validations.
Le code applicatif, les fixtures, les oracles et les délais applicatifs sont
inchangés pendant ce diagnostic. La réconciliation des 21 tests n'a pas été reprise.

## Cause démontrée

La sandbox refuse `send()` sur la paire de sockets locale servant à réveiller
la boucle asyncio depuis un autre thread. L'erreur est visible en activant
le diagnostic asyncio :

```text
/usr/lib/python3.12/asyncio/selector_events.py:152 in _write_to_self
    csock.send(b'\0')
PermissionError: [Errno 1] Operation not permitted
```

`_write_to_self()` absorbe cet `OSError` et ne le journalise qu'en mode debug.
Le callback est bien ajouté à la file des tâches prêtes, mais `epoll` ne reçoit
pas le signal qui devrait le réveiller. Le thread attend donc malgré `ready=1`.
Un essai indépendant limité à `socket.socketpair()` puis `left.send(b'x')`
reproduit `EPERM` dans la sandbox et envoie un octet hors sandbox. Il n'effectue
aucune connexion externe et n'importe aucun code StockPilot.

| Test | Phase observée | Pile/tâches pertinentes | Conclusion |
|---|---|---|---|
| `tests/test_tenant_bff_context.py::test_valid_bff_context_allows_its_explicitly_owned_resources` | Fixture `env` terminée ; blocage pendant le test, dans `setup_bridge → signed_in → begin → TestClient.post` | `AnyIO.run_sync_from_thread → Future.result → threading.wait`; thread de boucle dans `selectors.select`; callback `AsyncIOBackend.run_sync_from_thread.<locals>.wrapper` prêt, portail attendant `sleep_until_stopped` | Le réveil du portail ASGI est interdit par la sandbox ; aucun défaut de fixture, fournisseur externe ou contrôle d'autorisation démontré. |
| `tests/test_order_status_workflow.py::test_status_graph_never_calls_generation_stock_or_write_use_cases` | Pendant `asyncio.run(graph.ainvoke(...))`, avant les assertions et avant le teardown pytest | Tâches `langgraph.pregel.main.ainvoke` et `langchain_core.runnables.config.run_in_executor`; callback `_chain_future.<locals>._set_state` prêt ; worker d'executor inactif | Le travail synchrone est terminé, mais sa notification à la boucle est bloquée. Le blocage n'est pas dans une opération métier ou une requête réseau. |

Une variante avec `PYTHONASYNCIODEBUG=1` progresse jusqu'à
`asyncio.run → Runner.close → shutdown_default_executor` avant de rencontrer
le même réveil refusé (`_set_result_unless_cancelled` reste prêt). Il s'agit
du nettoyage de la boucle à l'intérieur du test, toujours dans la phase pytest
`call`, et non du teardown pytest. Cette variation de timing ne change pas la cause.

Les captures à quatre secondes indiquent, pour chacun des deux tests :

```text
RAG_IMPORT tenant_retrieval=False
RAG_IMPORT tenant_rag_safety=False
RAG_IMPORT tenant_assistant_citations=False
```

Ces trois modules modifiés ne sont pas importés par ces tests. Les tests et
leurs composants BFF/graphe sont inchangés par rapport à HEAD. Le défaut
d'environnement est reproductible indépendamment du correctif RAG ; il n'y a
aucune preuve d'une régression RAG, LangGraph ou d'un client externe non simulé.

## Reproduction bornée et correction

Chaque test a été lancé isolément avec `-vv -s --setup-show --full-trace`,
`faulthandler_timeout=5` et `timeout --signal=INT --kill-after=5s 20s`.
La limite initiale est 20 secondes ; un processus qui ne répond pas à SIGINT
est arrêté à 25 secondes. Les premiers essais des deux tests expirent ainsi
avec code 137. Les timeouts sont des interruptions diagnostiques, pas des
assertions pytest en échec.

Un runner local ignoré par Git observe les phases pytest, les tâches asyncio
et les callbacks prêts après quatre secondes. Il ne modifie ni le scheduling
ni le code testé. La variante debug ORDER_STATUS expire avec code 124 ;
les autres captures de tâches expirent avec code 137.

La correction concerne l'exécution : utiliser, avec l'autorisation obtenue,
l'environnement hors sandbox où ces sockets locales fonctionnent. Aucun
correctif applicatif ni de fixture n'est nécessaire. Aucune assertion n'a été
changée, aucun test n'a été désélectionné, et aucun délai de production augmenté.

## Résultats réels

| Exécution | PASS | FAIL | SKIP | Désélection | Résultat / durée |
|---|---:|---:|---:|---:|---|
| BFF isolé, sandbox initiale | 0 | 0 | 0 | 0 | TIMEOUT, code 137, limite 20 + 5 s |
| ORDER_STATUS isolé, sandbox initiale | 0 | 0 | 0 | 0 | TIMEOUT, code 137, limite 20 + 5 s |
| BFF isolé, premier essai hors sandbox | 1 | 0 | 0 | 0 | PASS, 0,64 s |
| ORDER_STATUS isolé, premier essai hors sandbox | 1 | 0 | 0 | 0 | PASS, 1,05 s |
| BFF isolé, validation finale hors sandbox | 1 | 0 | 0 | 0 | PASS, 0,75 s |
| ORDER_STATUS isolé, validation finale hors sandbox | 1 | 0 | 0 | 0 | PASS, 1,24 s |
| Sélection élargie complète (19 modules) | 897 | 0 | 0 | 0 | PASS, 6,12 s |
| BFF complet déterministe (4 modules) | 140 | 0 | 0 | 0 | PASS, 4,62 s |
| ORDER_STATUS complet déterministe (3 modules) | 55 | 0 | 0 | 0 | PASS, 2,23 s |
| RAG concerné (7 modules) | 310 | 0 | 0 | 0 | PASS, 2,17 s |

Les 310 tests RAG sont inclus dans les 897 ; les runs ne doivent pas être
additionnés comme des tests distincts. Chaque validation finale a un code
de sortie 0. Aucune utilisation de `-k`, `skip` ou `xfail` dans ces commandes.
`pyproject.toml` exclut toujours les intégrations réelles par défaut ; aucun
module d'intégration n'est inclus dans les sélections déterministes ci-dessus.

## Commandes exécutées

Les commandes suivantes sont les validations finales réellement exécutées,
depuis le worktree et hors sandbox avec autorisation. Les commandes initiales
et les variantes instrumentées figurent intégralement dans
`tmp/rag-timeout-diagnostics-20261116/commands.json`.

```bash
set -o pipefail
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 20s .venv/bin/python -m pytest -vv -s -p no:cacheprovider -ra --tb=long --full-trace --setup-show -o pythonpath=src -o faulthandler_timeout=5 tests/test_tenant_bff_context.py::test_valid_bff_context_allows_its_explicitly_owned_resources 2>&1 | tee tmp/rag-timeout-diagnostics-20261116/bff-final.txt
```

```bash
set -o pipefail
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 20s .venv/bin/python -m pytest -vv -s -p no:cacheprovider -ra --tb=long --full-trace --setup-show -o pythonpath=src -o faulthandler_timeout=5 tests/test_order_status_workflow.py::test_status_graph_never_calls_generation_stock_or_write_use_cases 2>&1 | tee tmp/rag-timeout-diagnostics-20261116/order-status-final.txt
```

```bash
set -o pipefail
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 60s .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src tests/test_rag_citation_grounding_post_v026.py tests/test_tenant_retrieval_excerpt.py tests/test_model_resource_limits.py tests/test_assistant_v025_citation_retrieval.py tests/test_assistant_v025_rag_security.py tests/test_assistant_v025_citation_boundaries.py tests/test_assistant_documentary_routing.py tests/test_b2b_assistant_contract.py tests/test_tenant_knowledge_contracts.py tests/test_tenant_authorization.py tests/test_tenant_catalogue_contracts.py tests/test_tenant_orders_contracts.py tests/test_catalogue_advice.py tests/test_catalogue_advice_v13c1.py tests/test_catalogue_advice_v1331.py tests/test_order_quantity_security.py tests/test_order_confirmation_context.py tests/test_order_product_identity.py tests/test_order_transaction.py 2>&1 | tee tmp/rag-timeout-diagnostics-20261116/selection-897-final.txt
```

```bash
set -o pipefail
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 60s .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src tests/test_bff_auth.py tests/test_bff_renewal.py tests/test_bff_mongo.py tests/test_tenant_bff_context.py 2>&1 | tee tmp/rag-timeout-diagnostics-20261116/bff-suite-final.txt
```

```bash
set -o pipefail
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 60s .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src tests/test_get_order_status.py tests/test_order_status_api.py tests/test_order_status_workflow.py 2>&1 | tee tmp/rag-timeout-diagnostics-20261116/order-status-suite-final.txt
```

```bash
set -o pipefail
PYTHONPATH=/home/waelko/projects/stockpilot-rag-citations/src PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 60s .venv/bin/python -m pytest -q -p no:cacheprovider -ra --tb=short -o pythonpath=src tests/test_rag_citation_grounding_post_v026.py tests/test_tenant_retrieval_excerpt.py tests/test_model_resource_limits.py tests/test_assistant_v025_citation_retrieval.py tests/test_assistant_v025_rag_security.py tests/test_assistant_v025_citation_boundaries.py tests/test_assistant_documentary_routing.py 2>&1 | tee tmp/rag-timeout-diagnostics-20261116/rag-suite-final.txt
```

## Preuves et fichiers

Répertoire local ignoré par Git : `tmp/rag-timeout-diagnostics-20261116/`.
Il conserve les logs complets, le runner d'observation `diagnostic_pytest.py`,
`commands.json`, les 897 identifiants collectés et les diffs avant/après.

- `bff-sandbox.txt`, `order-status-sandbox.txt` : piles et états des fixtures.
- `bff-sandbox-tasks.txt`, `order-status-sandbox-tasks.txt` : EPERM et tâches en debug.
- `order-status-sandbox-tasks-default.txt` : tâches avec le mode asyncio habituel.
- `bff-final.txt`, `order-status-final.txt` : validations isolées finales.
- `selection-897-final.txt`, `bff-suite-final.txt`, `order-status-suite-final.txt`,
  `rag-suite-final.txt` : sorties complètes des suites demandées.
- `before.diff` et `after-code.diff` : comparés identiques par `cmp` (code 0).

Fichiers de documentation ajoutés/modifiés par ce diagnostic : ce rapport et
un renvoi ajouté dans `rag-citation-contract-reconciliation.md`. Aucune
modification supplémentaire de l'application ou des tests. `git diff --check`
passe ; les preuves brutes et scripts locaux restent ignorés par Git.

La qualification déterministe demandée est complète. Les tests d'intégration
avec bases dédiées et Ollama réel restent non exécutés ; ce diagnostic
n'apporte aucune qualification de déploiement. Aucun service de production,
base réelle, VM, volume, tour historique, tag ou release n'a été modifié.
Aucun commit, push ou PR n'a été créé.
