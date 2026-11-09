# v0.2.5 — protection serveur du RAG

## Périmètre et cause racine

Le cas original **G15-11** demande la divulgation de secrets documentaires. Avant
cette passe, le périmètre SQL vérifiait bien organisation et permissions, mais un
document autorisé pouvait contenir un secret. Son contenu était transmis au
générateur, puis sa réponse publiée sans contrôle serveur du contenu sensible.
Une consigne dans le prompt ne constitue pas une protection contre un modèle
hostile. Le test d'observation précédent reproduisait cette fuite avec un marqueur
fictif ; aucun secret réel n'est utilisé par les nouveaux tests.

L'objectif réalisé est une défense bornée des **motifs de secrets reconnus** sur
les entrées, documents, métadonnées et réponses. Il ne s'agit pas d'une garantie
de détection de tout secret arbitraire ni d'une certification de sécurité.
Le corpus original, ses messages et ses oracles restent inchangés.

## Contrôles indépendants du modèle

1. `TenantRetrieval` revalide `RAG_READ`, puis refuse une demande explicite de
   valeurs de secrets avant embedding, recherche ou génération. Les questions
   légitimes de rotation des clés ou de protection des mots de passe restent
   utilisables.
2. L'ingestion refuse les motifs reconnus dans le contenu, titre, identifiants
   documentaire et d'opération, modèle et version avant `_begin`, calcul des
   embeddings ou écriture. L'autorisation `RAG_MANAGE` reste vérifiée en premier.
3. La recherche conserve le filtre SQL organisation/version/génération active
   avant le classement. Chaque document candidat est ensuite classifié sur sa
   **génération exacte complète**, bornée à 32 passages et 64 000 caractères.
   Cela couvre aussi les données anciennes et les motifs répartis sur deux
   passages. Les lectures de métadonnées `document`, `documents_by_title` et
   `ingestion` appliquent le même contrôle. Aucun document existant n'est modifié.
4. Le service filtre aussi les passages d'un port brut ou d'un adaptateur tiers.
   Chaque contenu envoyé au modèle est lié à son identifiant, numéro de passage
   et label source dans un enregistrement JSON. Les valeurs sont échappées ; un
   retour à la ligne, une fermeture de balise ou un faux label dans une métadonnée
   ne peut pas ajouter d'enregistrement JSON.
5. Après la génération, `RAG_READ` est revérifié. Les valeurs reconnues dans la
   sortie, les URL, citations ou métadonnées sont refusées. Les valeurs reconnues
   dans des passages rejetés par le service sont également recherchées dans la
   sortie, même sans leur label original. Le modèle ne décide jamais de ce refus.
6. Les erreurs inattendues d'embedding, recherche et génération sont remplacées
   par un message fixe sans détail du fournisseur ; la cause est supprimée de
   l'affichage normal. Les refus d'autorisation restent des refus d'autorisation.
7. `safe_public` contrôle récursivement les instantanés publics, les clés, valeurs
   et associations de champs sensibles (`password` associé à une valeur, par
   exemple). Le service assistant l'applique aux réponses et à l'historique sans
   modifier les instantanés persistés. Une réponse historique sensible est
   refusée ; elle n'est pas présentée comme une reproduction fidèle après censure.

## Citations et limites de grounding

Les passages ont des labels locaux `S1`, `S2`, etc. Le serveur refuse les références
`[S99]` et `[source: S99]` si le label ne correspond pas à un passage autorisé de
la requête. Il ajoute une liste déterministe **« Documents consultés (ne prouvent
pas chaque affirmation) »** avec les identifiants échappés et numéros de passages.
La réponse totale, liste comprise, est bornée à 8 000 caractères.

Cette liste prouve quels passages ont été fournis ; elle **ne prouve pas** que
chaque phrase générée en découle. Les citations absentes, une réponse constituée
d'un label seul, l'invention de faits, les styles de citation non reconnus et une
mauvaise association sémantique nécessitent la qualification réelle. Un modèle
peut encore inventer une affirmation non sensible ou une URL arbitraire. Aucun
PASS de grounding n'est attribué depuis le seul succès de ces contrôles.

## Détection et limites connues

Sont reconnus notamment les champs explicites `api_key`, `client_secret`,
`password`, `mot de passe`, `code secret`, `access_token`, `refresh_token`,
`authorization`, `cookie`, certaines familles de jetons (`ghp_…`, `sk-…`,
`sk_live_…`, `AKIA…`, JWT), les en-têtes de clés privées et les mots de passe
dans une URI. Les tests utilisent exclusivement des valeurs fictives.

La normalisation couvre NFKC, accents et caractères de format invisibles. Le
décodage couvre au maximum deux couches URL et 32 candidats base64 UTF-8 de
l'entrée originale, chacun borné à 64 000 caractères. Il ne couvre pas toutes
les compositions d'encodages, alphabets homoglyphes, archives, chiffrement,
séparateurs arbitraires ou secrets répartis entre documents distincts.
Une chaîne arbitraire non étiquetée reste indétectable : un test le démontre
explicitement. Le serveur ne connaît pas non plus les secrets externes mémorisés
par un modèle hostile s'ils ne correspondent à aucun motif reconnu.

Les valeurs explicitement affectées à un champ de secret sont traitées comme
sensibles même si elles sont des exemples inoffensifs : c'est un faux positif
possible assumé. Les textes procéduraux sans affectation et « secrets de
fabrication » restent autorisés. Le filtrage documentaire s'effectue après le
top-K autorisé borné ; il ne cherche pas indéfiniment un remplacement. Des
documents légitimes hors de cette fenêtre peuvent donc conduire à une abstention.
Les vérifications après dépendance réduisent la fenêtre de révocation ; elles
ne créent pas de transaction distribuée avec le générateur externe.

## Preuves exécutables

Les assertions de la suite existante sur le format du contexte ont été remplacées
par des assertions JSON exactes liant source, document, passage et contenu. Les
assertions d'isolation et d'absence de documents étrangers sont conservées.

- `tests/test_assistant_v025_rag_security.py` : demande exacte G15-11, modèle
  hostile, port brut ancien, secrets dans citations/URL/JSON, erreurs, révocation,
  faux positifs, limites connues, encodages bornés, provenance et borne finale.
- `tests/test_assistant_v025_rag_security_integration.py` : dix cas collectés sur
  les fixtures Mongo/PG jetables attestées, dont
  `test_exact_g15_11_server_refuses_before_model_and_preserves_state`, ingestion
  sans écriture, ancien secret à cheval sur deux passages, métadonnées anciennes,
  sortie hostile non persistée et révocation pendant génération.
- `tests/test_assistant_v025_final_history_order.py` : instantanés historiques
  sensibles refusés et inchangés, avec les contrôles d'identité et de conversation.

Sur le worktree de correction issu de
`c77e910bc491c3e33fe7484362c94c29567f0e1b`, la première exécution des quinze tests
RAG initiaux a donné **10 FAIL / 5 PASS**, puis la suite RAG étendue a donné
**26 PASS**. La dernière exécution combinée RAG/historique a donné **79 PASS** :

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_assistant_v025_rag_security.py tests/test_assistant_v025_final_history_order.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -m integration --collect-only tests/test_assistant_v025_rag_security_integration.py
git diff --check
```

Les contrats documentaires et l'ingestion existante ont aussi été réexécutés :
**42 PASS** avec
`.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tenant_knowledge_contracts.py tests/test_ingest_knowledge.py`.

La collecte des dix intégrations n'est **pas** leur exécution. Docker Desktop
n'est pas disponible dans ce WSL et n'a pas été démarré pour cette passe ; les
preuves d'exécution Mongo/PG et leur SHA exact doivent venir de la CI jetable.
Le rapport final de qualification associe les résultats au SHA final et sépare
ces preuves du résultat sémantique du vrai modèle Ollama.
