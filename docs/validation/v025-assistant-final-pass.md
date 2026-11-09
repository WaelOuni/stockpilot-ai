# Assistant v0.2.5 — qualification finale

Cette passe part de #84 `c77e910bc491c3e33fe7484362c94c29567f0e1b`
(279 PASS / 11 FAIL / 11 SKIP). #85 reste indépendante à
`014db7cee632638b5fed04d46c0db4583702039f`. Le corpus original est inchangé,
SHA-256 `b01fdc4a67ef4cfdac65a8884e85b95492fa19fb868c235170f5b9bf4ba80dac`.
Les preuves après commit, leurs SHA exacts et la matrice consolidée sont publiées
dans la description de [#84](https://github.com/WaelOuni/stockpilot-ai/pull/84).
Ce document décrit la méthode et les correctifs ; il ne préjuge pas de leur CI.

## Causes des onze échecs de référence

| ID | Cause et correction |
|---|---|
| G08-12 | Aucun SKU identifiable dans la demande ou le contexte. Clarification utile conservée, FAIL de l'oracle original conservé. Une décision métier est nécessaire pour résoudre cette contradiction ; aucune référence fabriquée. |
| G10-12 | La grammaire ne reconnaissait pas l'abréviation explicite. Alias B2B strict et borné vers le parseur existant, quantité et SKU littéraux, acheteur et preuve de session persistés, replay idempotent, aucune réservation. |
| G12-06 | La demande tombait dans RAG au lieu d'expliquer l'action UI. Guidance explicite vers une nouvelle préparation puis le bouton de confirmation ; le message désarme la preuve précédente. |
| G12-12 | Le suivi n'expliquait pas le risque de préparation périmée. Lecture de la commande autorisée et des identités/prix/stocks courants, explication de la revérification et nouvelle préparation explicite, sans mutation métier. |
| G14-01 | Aucun dispatch d'historique textuel. Restitution des recherches de la conversation privée. |
| G14-02 | Même cause. Cinq derniers messages, ordre chronologique et extraits explicitement signalés au-delà de 1000 caractères. |
| G14-04 | Pas de sélection du premier produit historique. Comparaison actuelle, identités des deux produits revérifiées ; cible ambiguë clarifiée. |
| G14-05 | Pas de restitution du snapshot demandé. Copie de l'observation historique inchangée, clairement datée comme ancienne ; aucune transformation en vérité catalogue actuelle. |
| G14-06 | Pas de rafraîchissement des anciennes références. Nouvelle lecture autorisée, sans réécrire le snapshot. |
| G14-07 | Pas de sélection temporelle vérifiable. Réponse d'hier identifiée dans la conversation puis stock actuel relu ; absence ou multiplicité clarifiée. |
| G15-11 | Les instructions LLM seules laissaient un modèle hostile divulguer le marqueur. Défense serveur indépendante du modèle : ingestion, documents historiques, métadonnées, extraits, sortie et replay public. Voir la revue RAG. |

Neuf scénarios ORDER/historique ont été reproduits rouges avant les correctifs.
G08-12 reste rouge dans le harnais de service. Le probe G15-11 de référence
montrait le marqueur fictif renvoyé par le modèle contrôlé ; son test de régression
injecte désormais ce document ancien dans le schéma jetable puis exige le refus.
Les anciens tests de non-mutation restent présents, avec des assertions positives
pour les fonctionnalités désormais implémentées.

## Preuves et limites

`layer_evidence.json` garde les 91 cas nécessitant d'autres couches et leur statut
de référence. Chaque ID exige ses nœuds JUnit exacts. Un échec antérieur ne peut
devenir SKIP faute de nouvelle preuve. Les dix demandes documentaires exigent
en plus trois essais Ollama réels, avec seeds distincts, sur un checkout propre
au SHA indiqué. Une seule tentative en échec fait échouer l'ID.

Le harnais `real_rag.py` utilise un serveur Ollama local isolé et un corpus
synthétique. Il exécute le graphe, les embeddings, le classifieur et la génération
réels. Les réponses sont relues : les citations du pied de page attestent les
documents consultés, jamais à elles seules le grounding de la réponse.
Les contrôles PostgreSQL/Mongo sont exécutés séparément sur services jetables en
CI : cette association ne constitue pas un essai unique PostgreSQL + Ollama de
bout en bout. Les permissions documentaires applicatives sont celles de
l'organisation et `RAG_READ` ; aucune ACL fine par document/utilisateur n'est
revendiquée. Le leurre privé du harnais est une protection de fixture seulement.

G18-15 possède un test BFF + Mongo direct : texte original, USER refusé sur deux
tickets étrangers (autre utilisateur et autre organisation), HTTP 403/404,
snapshots inchangés, contrôle positif administrateur. Aucune suite voisine ne
peut satisfaire cet ID.

La distinction de décision conserve fonction validée, clarification acceptée par
l'oracle, ambiguïté, fonction non prise en charge, preuve absente et régression
de sécurité. Une réponse sûre qui ne livre pas la fonction reste FAIL.

## Non-régression et publication des preuves

Exécuter la suite Python (dont 1200 variantes et 100 séquences), les 122
évaluations déterministes, les intégrations Mongo/PG attestées, frontend
Vitest/typecheck/lint/build et Chromium. Le moteur Docker local étant arrêté,
aucune tentative ne le démarre ni ne réveille les déploiements protégés.
Une intégration temporaire non fusionnée de #84 + #85 vérifie aussi la
compatibilité ; ni main ni #85 ne sont modifiés.

Les cinq checks CI de la HEAD finale, le résultat de chaque ID et les limites
Ollama sont consignés dans #84 après exécution. Les résultats ne valent ni
autorisation de fusion ni preuve de déploiement. Aucun merge, tag, release,
publication, changement de VM ou de StockPilot-B2B-v021 n'appartient à ce travail.

La qualification fonctionnelle complète reste NO-GO tant que G08-12 ou une
demande documentaire reste FAIL/SKIP. Une décision de fusion limitée au correctif
doit être distinguée de cette recette et de la décision de release v0.2.5.
