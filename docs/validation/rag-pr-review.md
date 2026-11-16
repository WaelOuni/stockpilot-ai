# Revue finale du contrat documentaire RAG — 16 novembre 2026

**READY FOR REVIEW** pour une PR Draft vers `main`, sans autorisation de fusion
ou de déploiement. Cette revue porte sur la branche
`fix/rag-citation-grounding-post-v026`, issue de
`57be3df363f535bd3e330dca71ba5e853907173a` ; le SHA distant de `main` correspond
encore à cette base au moment de l'audit. Elle ne remplace pas la revue humaine
ni les contrôles GitHub du commit publié.

## Cause et contrat retenu

L'ancien générateur pouvait retourner une affirmation libre sans source citée.
Le serveur y ajoutait les documents consultés, ce qui ne démontrait pas le
soutien de l'affirmation. Un refus fondé sur la seule absence de marqueur aurait
aussi rejeté des abstentions légitimes. Le contrat choisi est donc la publication
d'extraits attribués, avec abstention explicite lorsque les preuves sont
insuffisantes. Il ne repose pas sur une liste de formulations d'abstention.

Le parcours vérifié est : composition BFF dans
`infrastructure/identity/bff_assistant.py` → `TenantAssistant._documentary_reply`
→ `TenantRetrieval` → adaptateurs d'embeddings/PG/Ollama existants → validation
serveur → journal et projection HTTP existants. Seuls trois modules applicatifs
changent ; ni repository, migration, schéma HTTP, frontend, ni logique ORDER ne
sont modifiés.

| Frontière / invariant | Contrôle examiné | Preuve pertinente |
| --- | --- | --- |
| Le modèle propose, sans conférer d'autorité | Port `generate(prompt, response_format)` existant ; objet `answerable` et liste `excerpts` ; aucune synthèse libre rendue | Nouveaux tests de contrat et adaptateur ; scénario Ollama réel |
| Référence rattachée au passage autorisé | Table Sx construite après sélection tenant et filtrage ; contrôle organisation/document/génération/chunk/hash ; relecture des sources actives après génération | Tests d'extraits, citations et intégrations de révocation |
| Extrait vérifiable | Sous-chaîne exacte après NFC et normalisation CRLF/CR vers LF uniquement ; casse, espaces et ponctuation conservés | Cas exact, absent, vide, trop long, tenant étranger, source filtrée |
| Publication construite par le serveur | Rendu `[Sx] « extrait »` exclusivement ; JSON malformé, propriétés supplémentaires ou clés dupliquées ne créent pas de réponse factuelle | Tests de contrat, tests de limites et injections après génération réelle |
| Sources citées distinctes des sources consultées | `cited_sources` reprend, dans l'ordre publié, les références validées des seuls extraits rendus ; `_rag_read_sources` reste distinct et privé | Tests de citations multiples, concurrentes, de suivi et preuve PG réelle |
| Refus de sécurité | Contrôles de secrets avant le modèle et après génération ; références inconnues refusées ; contexte/permission relus | Tests historiques de sécurité, filtrage multi-chunks, permissions révoquées |
| Abstention neutre | `answerable=false` sans extraits ou preuve insuffisante ; message serveur fixe, aucune source citée ; message conservé dans l'historique public | Tests d'abstention et scénario réel sans preuve |
| Isolation et ownership | Requêtes PG scoppées avant classement ; contrôle défensif tenant ; revalidation des droits ; conversations privées existantes | Deux tenants avec même ID documentaire ; autre utilisateur ; source remplacée/retirée ; session révoquée |
| Limites et disponibilité | Question 4 000 caractères, extrait 2 000, cinq extraits maximum, sortie fournisseur et réponse 8 000 ; une génération par appel, aucune nouvelle boucle de retry | Tests de limites ; vraie erreur fournisseur et vrai timeout client isolé |
| Historique, catalogue, stock et ORDER | Aucun changement de DTO persistant, migration ou commande ; abstention ajoutée à la projection de messages serveur ; revalidation historique conservée | Suite déterministe complète et intégrations de résilience/replay |

`cited_sources` est conservé par le service et le journal. La whitelist HTTP
existante expose le texte mais n'expose pas ce champ interne : cette PR ne change
pas ce contrat API. Les anciennes conversations ne sont ni réécrites ni migrées.
La garantie d'extraits concerne les nouvelles générations ; elle ne constitue
pas une qualification sémantique rétroactive des anciens transcripts.

## Audit des tests réconciliés

Les changements de fixtures historiques suivent le contrat arbitré, sans
suppression, skip ou xfail ajouté pour obtenir un PASS. La classification avant
adaptation et les sorties initiales sont conservées dans les rapports et les
preuves privées.

- Les exemples positifs en texte libre deviennent des objets JSON avec extraits
  littéraux. Les assertions de source, de génération et de contenu restent
  effectives ; le rendu est désormais comparé au texte exact attendu.
- Les cas de référence inconnue utilisent `source_id=S99`. S1 reste accepté
  lorsqu'il désigne effectivement le passage autorisé. La prose libre et les
  marqueurs d'annexe ne sont pas convertis artificiellement en citations.
- Les contrôles de secrets fictifs, de filtrage des métadonnées et des chunks,
  d'injection de délimiteurs, de permissions et de révocation restent présents.
  Le texte arbitraire sans étiquette déclenche l'abstention documentaire, sans
  prétendre avoir été identifié comme un secret.
- Le test de taille couvre la sortie JSON surdimensionnée ; la limite de rendu
  serveur reste dans le code. Les assertions d'annexe supprimée sont remplacées
  par la séparation explicite des sources citées et simplement consultées.
- Le test de perte de connexion advisory utilise le garde officiel d'identité PG
  au lieu du nom littéral `stockpilot_ci`. La vérification du PID précis, le
  fencing du worker, le retry et l'absence de mutation métier sont conservés.

## Correspondance des preuves et validations

À l'entrée de cet audit, les **74 empreintes** du manifeste local de
qualification correspondaient aux fichiers présents et le diff suivi était
identique à `tracked-final.diff`. Les trois fichiers applicatifs, les fixtures
réconciliées et le script Ollama n'ont pas changé depuis les validations.
Seuls les rapports ont été clarifiés/assainis et la docstring du nouveau module
de régression a été actualisée. Une comparaison AST confirme que ses imports,
fixtures, corps de tests et assertions sont inchangés.

| Validation | Résultat retenu | Exécution pendant la finalisation |
| --- | --- | --- |
| Backend déterministe complet | 5 893 PASS, 0 FAIL, 0 SKIP ; 370 sous-tests PASS | Non répétée, preuves correspondantes |
| Intégrations RAG/résilience sélectionnées | 100 PASS, 0 FAIL, 0 SKIP | Non répétées, preuves correspondantes |
| Scénarios Ollama/embeddings/PG réels | 13 PASS, 0 FAIL, 0 SKIP, dont quatre injections contrôlées | Non répétés, preuves correspondantes |
| Évaluations déterministes enrichies | 122 cas PASS | Non répétées, preuves correspondantes |
| `npm test` | 642 PASS, 41 fichiers, 0 FAIL | Exécuté, code 0 |
| `npm run typecheck` | PASS | Exécuté, code 0 |
| `npm run lint` | PASS | Exécuté, code 0 |
| `npm run build` | PASS | Exécuté, code 0 |
| `git diff --check` | PASS | Exécuté |

Les **819 intégrations désélectionnées** par la commande backend par défaut ne
sont pas qualifiées par ce résultat. Les 100 intégrations sont leur propre
sélection explicitement exécutée ; aucune réussite globale des intégrations CI
n'est affirmée. Les mesures réelles conservées sont 6,93 s pour le premier
parcours à froid et 2,23 s à chaud, sans extrapolation en SLA.

Les quatre gates frontend prescrits par `AGENTS.md` manquaient aux preuves
précédentes. Ils ont été exécutés après `npm ci --no-audit --no-fund`, avec un
cache local sous `tmp/`. Aucun fichier de dépendances ou fichier frontend suivi
n'a changé. Ces tests jsdom et le build ne sont pas une recette navigateur/OIDC.

## Données publiables et périmètre du commit

Le commit est limité aux trois fichiers applicatifs RAG, aux sept modules de
tests historiques adaptés, aux deux nouveaux modules déterministes, au script
manuel de qualification et aux quatre rapports de validation associés.
Les fichiers temporaires, logs, sorties JSON/JUnit, configurations privées,
caches et artefacts Docker/frontend restent hors Git. Aucune dépendance nouvelle
n'est ajoutée.

L'URL privée Ollama et les détails de routage hôte ont été retirés du rapport
publiable, tout en conservant son original dans les preuves locales. La revue
des chaînes et le scan ciblé n'ont identifié aucun secret, token réel, URL privée
sensible, dump ou artefact volumineux dans les fichiers destinés au commit.
L'en-tête de clé privée d'un test existant est explicitement suivi de
`FICTITIOUS` ; les marqueurs de credentials sont des fixtures synthétiques, pas
des identifiants utilisables. Les retirer affaiblirait les tests de sécurité.

Les preuves de finalisation restent sous
`tmp/rag-pr-finalization-20261116/` : manifeste avant audit, originaux des
rapports, contrôle de correspondance, scan assaini et sorties frontend. Elles
ne sont pas ajoutées au commit. Le SHA publié et les contrôles GitHub sont à
consulter sur la PR Draft qui porte ce commit.

## Limites et risques résiduels

Un extrait exact prouve sa provenance, pas sa pertinence sémantique pour la
question, l'exhaustivité de la sélection ou la vérité du document. Le corpus de
production n'est pas qualifié. Aucune synthèse libre ou validation sémantique
LLM n'est ajoutée ; les réponses peuvent s'abstenir davantage et conserver la
langue du passage, même pour une question dans une autre langue.

Le fournisseur réel a été qualifié sur données synthétiques et stores jetables.
Le parcours navigateur, reverse proxy et OIDC réel n'est pas couvert par cette
recette ; les gates frontend ajoutés ne comblent pas cette limite. Les protections
de contenu restent des protections déterministes bornées, pas une détection
universelle de secrets arbitraires non étiquetés.

La compatibilité des interfaces et la persistance restent inchangées, mais
revenir à l'ancienne logique applicative rétablirait aussi son rendu documentaire
libre : ce n'est pas un rollback de qualité acceptable sans arbitrage. Aucun
nouvel état durable ni migration ne sont requis par ce correctif.

La revue a été conduite avec les skills QA, architecture et sécurité du dépôt,
sans avis indépendant inventé. La CI et la revue humaine de la PR restent les
prochaines vérifications ; ce verdict n'autorise ni fusion, release, déploiement
ou intervention sur les données existantes.
