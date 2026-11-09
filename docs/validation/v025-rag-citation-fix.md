# v0.2.5 — correction des citations du tour précédent

Point de départ : #84 `42936cedc3404d24487461033c52246eb9961fa8`,
299 PASS / 2 FAIL / 0 SKIP. Le corpus original reste inchangé,
SHA-256 `b01fdc4a67ef4cfdac65a8884e85b95492fa19fb868c235170f5b9bf4ba80dac`.
Les résultats finaux, leur HEAD exacte et les preuves CI sont attachés à
[la PR #84](https://github.com/WaelOuni/stockpilot-ai/pull/84), conservée en brouillon.

## Diagnostic G15-06

La demande originale « Cite la source documentaire de ta réponse si elle existe. »
était traitée comme une nouvelle question RAG. Elle déclenchait un embedding,
une recherche puis une génération indépendants de la réponse précédente.
Les labels S1/S2 désignaient uniquement les résultats de cette nouvelle recherche.
Le modèle citait donc delivery-guide même après une réponse sur le stock ou la
réservation. La preuve réelle de référence montrait deux échecs sur trois ; une
reproduction déterministe avec l'ancien service confirme le nouvel appel retrieval.

## Correction ciblée

`TenantRetrieval.execute` garde son retour texte compatible et remplit, pour
chaque appel, deux collecteurs distincts. Le premier contient seulement les
sources citées dans le texte généré, avant ajout du pied de page ; le second
conserve les sources consultées pour revalider l'accès au contenu lors d'un replay.
Les collecteurs ne sont jamais stockés sur l'instance partagée du service.

Chaque référence contient organisation, document, génération active, numéro de
passage et SHA-256 du contenu. Les labels temporaires ne sont pas persistés comme
identité. La lecture PostgreSQL exacte exige les droits actuels, l'organisation,
la génération active prête, le modèle/version et un document intégralement sûr.
Elle est réexécutée après la génération pour détecter une suppression ou un
remplacement pendant l'appel au modèle. Aucun changement de schéma ni migration.

Le journal privé du tour conserve `cited_sources`, `source_turn_id` et une portée
serveur versionnée. La portée et les références consultées restent privées dans
la projection publique. Le suivi G15-06 est déterministe : lecture du dernier
tour terminé de la conversation autorisée, contrôle de sa portée et de son tour
source, revalidation exacte des passages, puis rendu des identifiants documentaires
et passages. Aucun embedding, recherche par ressemblance ou LLM ne choisit la source.
Un résultat catalogue, une réponse sans citation ou un contexte ambigu ne réactive
pas une ancienne réponse documentaire.

Les checkpoints LangGraph gardent leurs champs existants ; aucun nouveau champ
obligatoire n'est requis. Un ancien checkpoint ou transcript sans provenance
structurée ne permet pas de reconstituer une source depuis son texte ou son footer.
L'absence de source vérifiable est dite explicitement. Le journal est l'autorité,
y compris après perte ou remplacement d'un checkpoint.

Le replay d'un tour documentaire, son affichage dans l'historique et une copie
G14-05 revalident les sources. Une source révoquée, remplacée ou devenue sensible
masque la réponse publique entière, sans modifier le snapshot persisté. Les sources
consultées non citées ne deviennent jamais des citations de G15-06. Si une source
d'un ensemble devient inaccessible, aucune citation partielle n'est publiée.
Un nouveau contexte de session du même propriétaire peut lire après revalidation ;
un ancien contexte de session est refusé. Les autres utilisateurs, conversations
et organisations ne fournissent jamais le tour précédent.

La défense serveur contre les secrets, les confirmations explicites et le moteur
ORDER restent inchangés. Le graphe peut persister une réponse de conversation ;
il ne réserve, confirme ou modifie aucune commande à partir d'une citation.

## Preuves à exécuter sur la HEAD finale

- Tests retrieval : citations multiples, consulté/non cité, labels inventés,
  suppression/révision pendant génération, contenu altéré, métadonnées sensibles,
  collectors concurrents, références malformées et erreurs assainies.
- Tests assistant : liaison du tour, portée, ambiguïté, absence de contexte,
  anciens checkpoints, replay, copies G14-05 et immutabilité.
- Intégrations Mongo/PG jetables : les trois contextes originaux, sources multiples,
  retrait/remplacement, droits, session, propriétaire/tenant et absence de mutation ORDER.
- Dix cas Ollama originaux × trois seeds historiques, sans retirer aucun échec.
  Les permutations supplémentaires de G15-06 ont un rapport distinct et ne
  remplacent pas les trente essais originaux.
- Corpus 301, 1200 variantes, 100 séquences, Python, évaluations déterministes,
  frontend, Chromium et intégration temporaire #84 + #85.

Les preuves sur repository mémoire et les preuves PostgreSQL sont distinctes ;
leur association ne prétend pas être un essai unique PostgreSQL + Ollama.
Le RAG interne historique `TenantRagConversation` n'est pas une route publique ;
le contrôle du replay décrit ici porte sur l'assistant B2B public.
Les fenêtres de révocation entre stores ne sont pas une transaction distribuée.
La reconnaissance de secrets est bornée, et une citation réelle ne certifie pas
universellement chaque affirmation du modèle.

## G08-12 — proposition de dérogation, non accordée

L'oracle original exige de déterminer l'appartenance d'un SKU aux résultats,
mais aucun SKU identifiable n'est fourni. Répondre oui/non nécessiterait une
référence inventée. La clarification sûre est donc conservée et le score original
reste **FAIL**, même si toutes les autres demandes passent.

Proposition soumise à décision humaine : accepter explicitement, pour la décision
de recette/fusion seulement, cette clarification sur le seul cas G08-12 comme
dérogation documentée à une attente inexécutable. Cette décision ne doit ni
modifier le corpus original ni convertir son FAIL en PASS. Un cas supplémentaire
avec SKU explicite peut prouver séparément l'appartenance aux résultats.
**Statut : proposition en attente ; aucune dérogation approuvée par l'agent.**

Aucun merge, tag, release, déploiement, changement de VM ou de données DEMO.
`main` et #85 restent inchangées. Des checks verts n'autorisent aucune de ces actions.
