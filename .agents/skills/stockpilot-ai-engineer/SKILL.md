---
name: stockpilot-ai-engineer
description: "Diagnostiquer ou améliorer l'assistant StockPilot AI, LangGraph/Ollama, la recherche naturelle, ADVICE, le RAG pgvector et ses évaluations. Utiliser pour routage, grounding ou qualité des réponses ; pas pour du CSS, un CRUD indépendant ou un changement ORDER non explicitement demandé."
---

# Assistant et évaluations StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md). Identifier le graphe historique
[workflows](../../../src/stockpilot/application/workflows/) ou
[assistant B2B](../../../src/stockpilot/application/services/tenant_assistant.py).
Fixer le cas utilisateur, l'attendu vérifiable, le SHA et les modèles configurés.
Un changement de prompt/modèle partagé pouvant affecter ORDER exige une demande
ORDER explicite ; une demande ADVICE n'en tient pas lieu.

## Workflow
1. Reproduire avec un cas synthétique borné et lire le contrat :
   [catalogue Advice](../../../docs/catalogue-advice.md),
   [assistant B2B](../../../docs/security/b2b-assistant.md),
   [RAG](../../../docs/rag.md). Distinguer compréhension, résolution de référence,
   lecture du catalogue, retrieval et formulation avant de changer un prompt.
2. Garder SKU, prix, stock et statut issus des stores autorisés. ADVICE ne réserve
   rien ; ambiguïté = clarification, données manquantes = limite explicite.
   Ne pas transformer une carte historique en stock courant ni inventer
   devise/spécifications absentes ; les prix/devises B2B ont leur propre contrat.
3. Pour le RAG tenant, lire [TenantRetrieval](../../../src/stockpilot/application/services/tenant_retrieval.py),
   [store PG](../../../src/stockpilot/infrastructure/persistence/postgres/tenant_knowledge.py)
   et [contrat B4](../../../docs/security/tenant-rag-conversations.md).
   Organisation/génération/modèle/version sont filtrés avant classement et LLM.
   Aucun fallback global. Valider dimension et valeurs des embeddings.
   Documents et questions restent des données non fiables, jamais des permissions.
4. Préserver conversations/checkpoints privés org+user, revalidation des droits,
   journal PG avant mutation Mongo et réconciliation à clé constante.
   En B2B, le texte « oui » ne confirme pas : la confirmation HTTP explicite reste
   distincte du graphe. Ne pas fabriquer un second moteur de commande.
5. Choisir le plus petit changement et mesurer ses effets : erreurs de routage,
   faux faits, ambiguïtés, panne fournisseur, latence et taille du contexte.
   Une nouvelle architecture exige Architect ; une frontière tenant ou un flux
   vers le modèle modifié exige l'analyse Security.

## Tests et acceptation
Utiliser [ai-evals](../../../docs/ai-evals.md) et [référence des evals](../../../evals/README.md),
puis les commandes canoniques d'AGENTS.
Tester le graphe avec classifieur hostile et corpus synthétique :
[Advice](../../../tests/test_catalogue_advice_evals.py),
[assistant B2B](../../../tests/test_b2b_assistant_contract.py),
[échecs knowledge](../../../tests/test_tenant_knowledge_failure_integration.py).
Le mode déterministe vérifie le framework et les gardes métier, pas Ollama réel.
Pour conclure sur le modèle, utiliser le mode local documenté sur corpus d'éval
dédié et fournisseur autorisé ; relever modèle, configuration, hashes, échantillon,
échecs et latence. Ne jamais promouvoir automatiquement la baseline.

## Livrable et arrêt
Fournir diagnostic par étape, changement minimal, cas avant/après, preuves
déterministes et modèle réel séparées, limites et GO/NO-GO sur le comportement visé.
Si fournisseur/corpus isolé manque, continuer l'analyse et annoncer la validation
réelle non exécutée. Arrêter toute exposition de données ou modification ORDER
hors périmètre ; ne pas assouplir les goldens pour masquer une régression.

## Exemple
« Analyse une recherche naturelle B2B ambiguë et propose une correction bornée
avec tests de non-fuite RAG, sans toucher à ORDER. »
