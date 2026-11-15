# Consultations : erreurs, budgets et reprise explicite

Ce contrat concerne les nouveaux messages `read_only=true`, sans action métier.
L'interface propose un choix explicite « Consultation uniquement ». Le choix
reste désactivé par défaut : une intention ORDER ambiguë ne devient jamais une
autorisation de mutation. Les garanties et prompts ORDER restent inchangés.

## États durables et compatibilité

Le journal SQL conserve ses deux statuts historiques. Seule une finalisation
aboutie passe de `pending` à `completed`. Un objet privé borné `_read_attempt`
dans le JSON existant distingue les tentatives de consultation ; il n'est pas
une réponse réussie et n'est pas exposé au navigateur. Aucune migration.

L'API ajoute `execution` :

| État | Signification | Suite autorisée |
| --- | --- | --- |
| `running` | Verrou advisory de cette conversation observé à la lecture | Relire ; aucune reprise concurrente |
| `failed` | Échec connu persisté, sans résultat completed | Reprise explicite si `retryable=true` |
| `unknown` | Pas d'exécution observée, issue non finalisée ou legacy | Relecture et vérification avant reprise explicite |
| `completed` | Résultat final durable | Rejeu retourne le résultat sans inférence |

`running` est une observation instantanée, pas un heartbeat ni la preuve qu'un
modèle progresse. Une panne de processus ou de stockage peut laisser `unknown`.
Une issue de commit inconnue ou une révocation d'accès n'est jamais renommée en
échec certain. Un échec d'entrée/validation non reprenable impose une nouvelle
conversation ; aucune suppression ou modification silencieuse de l'ancien tour.

Un doublon POST messages ne reprend pas un tour pending. Pour un tour marqué,
POST retry exige `expected_attempt` obtenu dans la dernière lecture autorisée.
Chaque tentative reçoit une nouvelle identité aléatoire ; une requête conservant
l'ancienne identité reçoit un conflit. L'identité du message et son entrée sont
conservées. Après une erreur, le navigateur relit une fois le journal, sans retry
ni resume automatique. Les vieux pending sans marqueur acceptent une première
reprise explicite sans `expected_attempt`, selon leur contrat historique.

La finalisation, l'échec et les écritures de checkpoints vérifient dans leur
transaction le propriétaire, `read_only=true`, l'absence d'action, le statut
pending et l'identité de tentative. Un ancien worker qui perd sa connexion de
verrouillage ne peut pas écraser une tentative plus récente. Les écritures de
checkpoint déjà parties dans un thread sont drainées avant de libérer l'admission.
Les droits courants restent vérifiés ; aucune écriture de secours privilégiée.

Les anciens binaires peuvent lire le JSON et le statut pending, mais ils ne
connaissent pas le nouveau verrouillage par tentative. Cette lisibilité ne
qualifie pas un fonctionnement mixte ancien/nouveau. Une livraison ou un retour
arrière doit exclure les workers concurrents de versions différentes, contrôler
les tours ouverts et qualifier le contrat de reprise du binaire choisi.

## Budgets et annulation

| Couche | Contrat |
| --- | --- |
| Admission assistant | Une exécution par instance par défaut ; refus 429 immédiat, sans création de tour |
| File du worker BFF | Mesurée ; incluse dans le budget depuis l'entrée de `invoke` |
| HTTPX fournisseur | Délai existant de 60 s par phase d'inactivité, inchangé |
| Classification / embedding / génération | Partagent le budget restant, sans addition de trois nouveaux délais de 60 s |
| Consultation complète | `BFF_ASSISTANT_READ_BUDGET_SECONDS`, 75 s par défaut, configurable de 1 à 75 |
| PostgreSQL | Connexion 5 s, instruction 10 s, attente de verrou 3 s, inchangées |
| Navigateur assistant | 90 s maximum ; abandon et réponse tardive ne valent pas succès |
| Nginx | Configuration non modifiée ; revalider séparément le montage actif avant livraison |

Le budget de consultation est coopératif. Il annule la coroutine de lecture et
draine les écritures déjà engagées, puis tente de persister l'échec. Les 15 s
avant la limite navigateur constituent une marge, pas une garantie temps réel :
un blocage du noyau, un appel synchrone ou une panne de base peuvent la dépasser.
Le client conserve alors l'incertitude et relit le résultat durable. Aucun
changement de timeout ou d'annulation des transactions ORDER n'est introduit.

Un événement thread-safe transmet la déconnexion ASGI au worker. Les annulations
client, serveur et budget ont des codes distincts. Un HTTP 500/503 fournisseur
conserve sa provenance ; il n'est pas appelé timeout. HTTPX distingue connexion,
lecture, écriture et pool. Un TimeoutException de phase inconnue devient
`transport_timeout`, jamais un ReadTimeout supposé.

Après consommation bornée du corps par la boundary et parsing des routes typées,
un seul watcher attend directement `http.disconnect`. Le polling
`Request.is_disconnected()` peut manquer cet événement à travers une réception
AnyIO, car son contexte est immédiatement annulé. Les enveloppes inutilisées
GET/resume sont consommées ; le watcher est annulé et attendu avant le retour.
Une réponse streaming ou un corps lu tardivement nécessiterait une nouvelle revue.

Le transport navigateur utilise `referrerPolicy: strict-origin`. Avec
`mode: same-origin`, `no-referrer` peut produire `Origin: null` et faire refuser
un POST légitime. La nouvelle politique transmet seulement l'origine HTTPS
déjà connue du BFF ; ni chemin privé ni query. Les gardes Origin exact, CSRF,
URL même origine, redirections refusées et TLS restent inchangées. Voir
[l'algorithme Fetch](https://fetch.spec.whatwg.org/#append-a-request-origin-header)
et [la politique strict-origin](https://w3c.github.io/webappsec-referrer-policy/#referrer-policy-strict-origin).

## Observations sans contenu

`stockpilot.assistant.runtime` émet des JSON à champs autorisés : corrélation
aléatoire, UTC, temps écoulé, phase, durée, résultat, code d'erreur et HTTP réel
lorsqu'il a été reçu. Les phases couvrent admission, verrou, begin, classification,
connexion/en-têtes/corps fournisseur, embeddings, retrieval, génération,
validation, checkpoints, finalisation et requête totale. Les durées de chargement
et d'évaluation viennent des champs Ollama lorsqu'ils existent ; leur absence
sur une erreur reste une absence de mesure, pas zéro seconde.

Le navigateur expose un seul relevé en mémoire et l'événement
`stockpilot:assistant-timing`, avec durée, HTTP, corrélation et résultat transport.
Cet événement ne certifie pas un succès métier. Aucun prompt, document, cookie,
token, URL, identifiant de conversation ou texte d'exception n'y figure. Pas
d'export externe ajouté. Les opérateurs doivent préserver cette liste de champs.

## Réconciliation d'un ancien pending : intervention distincte

1. Revalider l'identité de l'installation et la sauvegarde actuelle restaurable.
   Lire la référence privée du tour via les canaux existants, sans la publier.
2. Confirmer le même propriétaire/tenant, message et empreinte, `read_only=true`,
   `action=NULL`, aucune opération ORDER, et les checkpoints/writes attendus.
3. Prouver l'arrêt de l'ancienne exécution ; vérifier les verrous et l'absence de
   workers anciens encore capables d'écrire. Arrêter si l'issue reste incertaine.
4. Reproduire la capture sur des bases isolées. Restaurer et vérifier les
   empreintes, identités, droits et données métier avant toute reprise.
5. Avec la session autorisée du propriétaire, lire l'historique. `resume` seul
   ne relance pas le classifieur d'une consultation. Choisir explicitement retry
   du **même** conversation_id/turn_id, avec `expected_attempt` si présent.
6. En cas de transport incertain, relire avant toute nouvelle décision. Vérifier
   completed, les checkpoints, l'identité de message et l'absence de mutation
   métier. Un replay completed doit retourner le résultat sans nouvel appel modèle.

Jamais d'UPDATE manuel, suppression de tour, remise à zéro de checkpoint ou
retry automatique. La qualification synthétique d'une restauration représentative
ne constitue pas une autorisation d'exécuter cette procédure en production.

## Conditions de livraison corrective

Exiger les cinq checks CI sur le SHA revu, la revue indépendante, la matrice de
pannes et les contrôles métier/tenant. Garder les 301 prompts et leurs oracles,
ainsi que la dérogation G08-12, sans transformer celle-ci en PASS.

Qualifier ensuite le trajet réel navigateur/BFF/proxy/VM/fournisseur, à froid
et à chaud, dans un environnement isolé représentatif. Les mesures via un autre
transport ne qualifient pas ce trajet. Relever modèle, poids, version, contexte,
CPU/RAM/GPU et saturation. Un petit échantillon et un cache runtime vide ne
prouvent ni un SLA ni un cache disque froid.

Un warm-up supervisé et un `keep_alive` borné peuvent être évalués séparément,
avec mesure de VRAM des deux modèles et contrôle de leur expiration. Ne pas
changer le serveur partagé. Ni hausse arbitraire à 120 s, ni reroutage des
intentions ambiguës, ni changement de prompt/modèle ORDER ne font partie de ce
correctif. Le HTTP 500 historique reste d'origine bas niveau indéterminée tant
qu'il n'est pas reproduit avec ses traces par phase.

Références : [timeouts HTTPX](https://www.python-httpx.org/advanced/timeouts/),
[métriques de génération Ollama](https://docs.ollama.com/api/generate),
[embeddings Ollama](https://docs.ollama.com/api/embed),
[mémoire et concurrence Ollama](https://docs.ollama.com/faq).
