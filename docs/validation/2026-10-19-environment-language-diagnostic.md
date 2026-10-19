# Diagnostic 5175 / candidate 5193 / main — 19 octobre 2026

## Périmètre et provenance

Inspection des processus, Git, labels/images et lectures catalogue seulement sur
les installations existantes. Aucune requête `/assistant` sur la candidate : elle
écrit des checkpoints même lorsqu'elle ne réserve aucun stock. Reproductions et
confirmations exécutées exclusivement dans des services jetables distincts.

`git fetch origin main` a donné
`01f15662f0e2b9e6a4c141fdd0f43506f25ffbd6` (PR #40). Le checkout initial est
propre et **65 commits derrière** cette référence ; aucun changement de ce
checkout, arrêt du lanceur ou remplacement de ses processus pendant ce diagnostic.

| Environnement | Processus et chaîne HTTP | Commit application | Stores effectifs |
|---|---|---|---|
| HTTP 5175 | Vite PID **94543**, cwd `/home/waelko/projects/stockpilot-ai/frontend`, `API_PROXY_TARGET=http://127.0.0.1:8003`, retrait de `/api` ; Uvicorn PID **94522**, parent lanceur **94429**, cwd `/home/waelko/projects/stockpilot-ai` | `6f0949676e33a21c640b2ccc2f5877368a4dd0cb` ; `v0.1.7-5-g6f09496`, merge #25 | Mongo `stockpilot-mongodb`, localhost:27017, base **stockpilot_demo**, rs0 ; PostgreSQL `stockpilot-postgres`, 127.0.0.1:5432, base **stockpilot_demo**, également checkpoints |
| HTTPS 5193 | `stockpilot-v133-candidate-web-1` : 127.0.0.1:5193 → 8443 ; `stockpilot-v133-candidate-api-1`, projet `stockpilot-v133-candidate` | **`79368b1316b082f00861d5b5eb6b17b07505481d`**, merge #38, labels OCI web/API concordants ; **5 commits derrière** main | Mongo `stockpilot-v133-candidate-mongo-1`, `mongo:27017`, base **stockpilot_candidate_v133**, rs0 ; PostgreSQL `stockpilot-v133-candidate-postgres-1`, `postgres:5432`, même base/checkpoints |
| DEMO v017, protégée | Projet `stockpilot-demo-v017`, accès web sur **5191** | Label API `9d9787f03c5b0448545a78cc780ebb5b04ac6af1` | Bases **stockpilot_demo_v017** ; distinctes des deux lignes précédentes |
| Reproduction jetable | Uvicorn **8019**, propre venv/package éditable du nouveau worktree `stockpilot-env-diagnostic-20261019` | baseline **01f1566**, puis diff de cette PR | Projet Compose **stockpilot-envdiag-20261019**, Mongo loopback **27027** en tmpfs, bases `test_envdiag_<aléatoire>` ; PG loopback **55432**, base **stockpilot_ci**, schémas `stockpilot_checkpoint_test_<UUID>` |

L'environnement du **processus API**, et non celui du parent lanceur, établit la
base `stockpilot_demo`. Son package éditable pointe vers le checkout 6f09496.
L'API 5175 utilise Ollama `http://172.18.80.1:11434`, modèle `qwen2.5:7b`.
La candidate passe par `http://relay:8080` ; le relais conserve une image
`c760d407713c`, ce qui n'est pas le commit de son API.

Identifiants Docker partiels, relevés en lecture seule : API candidate
`3b85b9990f28`, web `cb0354d2aaf8` ; API v017 `0e58f25822d4`.
API candidate : image
`sha256:b115b6ae693e9ea0fb66240507fef0528113a696f80b23dfcb07e6f5793ac69a`.
Le SHA-256 du module Advice lu dans cette image est
`97ccc1f39e559614558e14d190245a4d46814372e2ab051db1dbacffb13c23f4`,
identique au fichier Git de 79368b1. Les labels sont ainsi recoupés avec le code.

## Catalogues différents

| Lecture Mongo | 5175 | Candidate 5193 |
|---|---|---|
| Produits / commandes / mouvements au relevé | **554 / 2019 / 3041** | **512 / 2009 / 3000** |
| SKU `DEMO-DRILL-0476` | nom `Northline Tools 18V Brushless Drill 0476`, prix **52,69**, stock **74** | même SKU/nom, prix **197,06**, stock **100** |
| Identifiant du dataset de ce SKU | `stockpilot-demo-v1-seed-20261010` | `stockpilot-demo-v1-seed-20261017` |
| Trois premières perceuses disponibles par prix/SKU | deux anciennes entrées `DEMO-TEST-…-RUNTIME` à **42,50**, puis `DEMO-DRILL-0476` à **52,69** | `D-A` **29,90**, `D-B` **39,90**, `D-C` **49,90** |

Le catalogue local contient des produits/mutations historiques en plus du seed.
Ils existaient avant ce diagnostic et ont été conservés. Le seed immuable affiché
par `run-demo.sh` (**500 / 2000 / 3000**) n'est donc pas le compte vivant.
Les trois premiers prix de la candidate sont tous inférieurs à 50 : constater
seulement ces trois cartes ne prouverait pas qu'un filtre de budget fonctionne.
Aucune copie des données de déploiement n'a été utilisée pour les tests.

## Reproduction avant correction

HTTP `/assistant`, vrai FastAPI/LangGraph, MongoDB 8, PG16/checkpoints persistants,
vrai Ollama et seed synthétique **20261010** généré à neuf. Threads frais par
requête, JWT éphémère. Les six requêtes ont retourné HTTP 200 ; les erreurs sont
sémantiques. Catalogue identique avant/après, zéro commande créée.

| Demande exacte | Observation utilisateur sur ancien 5175 | Baseline main 01f1566, isolée | Conclusion |
|---|---|---|---|
| Quelle perceuse est disponible ? | 3 produits | Advice `NO_RESULTS`, référence parasite `quelle perceuse est` | **Régression réelle** du parseur de référence introduit par #32 : copule singulière non retirée. Le même parseur existe dans 5193. |
| Quelle perceuse est disponible moins 50 ? | 52,69 inclus | Advice `NO_RESULTS` ; `maximum=None`, référence contenant toute la demande | **Lacune toujours présente**, mais symptôme différent. #39 traite « moins de », « sous », etc., pas cette forme elliptique « moins 50 ». |
| Peux-tu me conseiller sur perceuse Northline Tools 18V Brushless Drill 0476 ? | 3 produits génériques | Advice `RESULTS`, **une seule fiche DEMO-DRILL-0476**, prix52,69/stock74 | **Déjà corrigé dans main** et chemin de ciblage présent dans la candidate. Décalage de version du 5175. |
| Commande-moi 1 Northline Tools 18V Brushless Drill 0476 | Quantité ambiguë | ORDER `INVALID_QUANTITY`, classificateur ayant bien extrait **1** | **Refus trop large toujours présent** : 18V est déjà protégé comme mesure ; le nombre isolé **0476** déclenche le garde. |
| Quelle perceuse est disponible à moins de 50 ? | Contrôle supplémentaire | plafond50 strict reconnu, mais `NO_RESULTS` à cause de la référence `quelle perceuse est` | Reconnaître le budget seul ne suffit pas à réparer cette formulation. |
| Commande-moi 1 DEMO-DRILL-0476 | Contrôle supplémentaire | `PENDING_CONFIRMATION`, quantité1, SKU et document exacts | Distingue le défaut de quantité par nom du stock, de l'authentification et du chemin SKU. |

Sur 5193, seuls les parseurs purs ont été interrogés : mêmes deux références
parasites, absence de plafond pour « moins50 », référence complète conservée
pour le conseil nommé, refus de quantité0476. Aucun scénario mutable exécuté.

La correction #39, commit `451ae35` puis merge `bf2329b`, est bien dans main
mais absente des images 5193. Elle corrige notamment « Rechercher… sous100 » et
ne couvre pas ces trois défauts restants. Le fichier de preuve ORDER était
identique entre 6f09496 et main : le refus du numéro0476 n'est pas une nouvelle
régression de classification ou un changement de modèle.

## Correction minimale préparée

Advice : deux changements de grammaire partagée. Accepter la copule `est` dans
une question générique ; reconnaître `moins` suivi d'un montant comme borne
stricte, sans capturer « moins cher ». Les noms inconnus restent des références,
les bornes inclusives « maximum » et le contrôle des budgets contradictoires
restent en place. Aucune sélection par nom n'est remplacée par trois alternatives.

ORDER : conserver le parseur strict existant en première intention. Seulement
après son refus, résoudre la demande littérale dans le catalogue ; un nom complet
résolu de façon unique et présent en fin de demande peut attester son seul
dernier token entier comme numéro de modèle. Une quantité explicite doit le
précéder ; les autres nombres, expressions lexicales, fractions et marqueurs du
nom restent examinés par tous les gardes existants. Aucun masque global des
nombres à quatre chiffres, aucun
nom suggéré par le modèle faisant autorité. La preuve conserve le message
original, le span de **1**, et le nom vérifié ; elle est recalculée et liée au nom
préparé à confirmation. Le SKU, le document Mongo et l'opération restent liés à
la proposition explicite. Les anciennes preuves gardent leur forme et validation.

Le même contrôle est utilisé lors d'une nouvelle préparation déjà armée.
L'héritage d'une preuve liée à un nom exige de relire ce nom dans le catalogue.
Si le produit change, cette preuve ne peut pas armer une proposition différente :
une nouvelle quantité explicite est demandée. Une classification contradictoire
Advice/stock/statut reste refusée ; la conversion historique des demandes à
quantité invalide vers le chemin de refus ORDER conserve ce conflit dans un
champ d'état réinitialisé à chaque classification.
Les quantités négatives, nulles, fractionnaires, lexicales non prises en charge,
les extractions18/476, les variantes/suffixes inconnus, les identités ambiguës et
les preuves altérées restent refusés. Pas de modification des prompts, modèles,
réservation atomique, finalisation transactionnelle ou règles d'ownership.

## Preuves de validation

Avant correction : **8 échecs / 12 succès** dans le nouveau fichier unitaire,
et **8 échecs d'intégration** (la préparation valide préalable était refusée).
Ces assertions ont été écrites et exécutées avant le changement applicatif.

La première revue indépendante a détecté un masque trop large du nom catalogue,
un héritage de preuve incompatible avec un nouveau produit et une perte de
l'intention contradictoire initiale. **12 assertions rouges** ont reproduit ces
cas avant révision du correctif, puis deux assertions supplémentaires ont imposé
une quantité explicite devant le nom numérique. Aucune de ces versions
intermédiaires n'a été poussée ni déployée.

Après correction, les six requêtes réelles Ollama ont été rejouées sur le même
seed jetable : trois perceuses52,69/70,88/71,79 sans budget ; **zéro sous50** ;
une fiche ciblée52,69 ; commande par nom et par SKU **PENDING_CONFIRMATION**,
quantité1. Le catalogue généré ne possède aucune perceuse disponible sous50,
ce qui est normal et distinct des deux anciennes entrées42,50 du catalogue local.
Zéro confirmation pendant cette mesure réelle ; stocks identiques, zéro commande.

Qwen observé : `qwen2.5:7b`, digest
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
Embedding disponible : `nomic-embed-text:latest`, digest
`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`.
Ces six requêtes ne constituent pas une mesure exhaustive de qualité du modèle.

Les nouveaux tests déterministes utilisent des prix49,99 /50 /52,69 pour vérifier
la frontière stricte et une rupture à40. Les tests HTTP/Mongo/PG confirment la
persistance de la preuve, la confirmation après reconnexion, une seule réservation
et un seul ordre malgré le rejeu, et le désarmement durable après quantité invalide.
Les fixtures vérifient les URI explicites avant toute écriture ; schémas et bases
de test sont propres à chaque exécution.

Commandes depuis le nouveau worktree et son propre venv :

```sh
docker compose -f docker/compose.ci.yml -p stockpilot-envdiag-20261019 up -d --wait
export MONGO_TEST_URI='mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true'
export POSTGRES_TEST_URI='postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci'
.venv/bin/python scripts/ci/check_targets.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -m 'integration and not integration_optional' tests
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
(cd frontend && npx playwright test --config playwright.advice-synthetic.config.ts)
git diff --check
```

Résultats locaux finaux :

- Backend : **2533 PASS**, **222 intégrations désélectionnées**, 114,61 s.
- Intégrations : **221 PASS**, **2534 désélectionnés**, 80,04 s ; une intégration
  optionnelle de cycle de vie DEMO reste exclue car elle exige d'autres services.
- Évaluations déterministes enrichies : **122 cas, zéro échec**.
- Frontend : **428 PASS**, typecheck/lint/build PASS.
- Navigateur Advice synthétique : **11 PASS**, 7,1 s ; aucun store ni Ollama.
- Six requêtes HTTP/Ollama réelles sur le diff final : comportement attendu,
  catalogue intact, zéro commande créée.
- `git diff --check` : PASS. Un avertissement JWT de clé courte de fixture et les
  avertissements de sérialisation d'enums LangGraph préexistent au changement.

Seconde revue indépendante **favorable**, sans blocage résiduel : **298 tests
ciblés PASS**, 15 noms catalogue adverses refusés, six parcours de conflit puis
récupération/confirmation vérifiés avec catalogue/checkpointer/finaliseur mémoire.
Le reviewer n'a pas exécuté Mongo/PG ou Ollama ; leurs preuves sont celles des
exécutions séparées ci-dessus. L'état CI est contrôlé sur la PR après push.
Les métriques déterministes ne sont pas des mesures de qualité d'Ollama réel.

Comparaison avant/après des **13 conteneurs protégés** : mêmes identifiants,
images, dates de démarrage, états, ports et ensembles de montages. L'ordre des
montages renvoyé par Docker a été normalisé par destination avant comparaison.
Les PID94522/94543 et le checkout initial propre sont également conservés.
Les services tmpfs du seul projet de test créé ici sont retirés après validation ;
aucun volume persistant ni store de déploiement n'est supprimé.

Limite volontaire : une demande par nom avec suffixe numérique sans quantité
explicite reste à clarifier ; utiliser « Commande-moi 1 … » ou le SKU. Les
anciennes demandes par SKU ou nom simple conservent leur quantité par défaut
quand le parseur strict les prouve déjà. Le fallback peut ajouter des lectures
catalogue sur les demandes refusées, mais aucun appel LLM ni mutation.

## Utilisation sûre et limites de livraison

Ni `main` local ni `dev` ne désignent automatiquement l'environnement récent.
Une fusion GitHub ne met pas à jour les processus Vite/Uvicorn ni les images.
**5193 ne contient pas #39**, et changer son URL ne suffit pas à obtenir cette
correction. Ne pas mettre à jour en place le checkout utilisé par5175 : Vite
relirait des fichiers alors que Python garderait des modules déjà chargés.

Après revue/fusion humaine éventuelle, utiliser un nouveau worktree sur le SHA
validé, son propre venv et frontend, des ports libres et des stores synthétiques
nouveaux explicitement vérifiés. Ne pas reprendre les secrets ou les bases des
installations protégées. Tester le budget avec un produit sous50, un à50 et un
au-dessus ; vérifier un produit nommé et une préparation quantité1 par SKU et
nom. Confirmer seulement dans cette installation jetable. Toute promotion ou
mise à jour de5175/5193 demande une opération séparée explicitement autorisée.

Aucun merge, tag, release, déploiement ou changement des deux DEMO par ce travail.
