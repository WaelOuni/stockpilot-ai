---
name: stockpilot-architect
description: "Analyser les frontières modulaires, impacts inter-couches, compatibilité et ADR de StockPilot AI. Utiliser pour un arbitrage d'architecture demandé ou une évolution transverse ; pas pour une correction locale, du CSS ou une décision commerciale."
---

# Architecture StockPilot

## Périmètre et préconditions
Produire une décision minimale argumentée, puis seulement l'implémentation demandée.
Lire [AGENTS.md](../../../AGENTS.md) et ses points d'entrée mémoire. Confirmer le SHA,
le parcours historique ou B2B concerné et le contrat à préserver. Les snapshots
documentaires ne prouvent ni l'état courant du code ni celui d'une installation.

## Workflow
1. Tracer le parcours réel depuis la composition : [main.py](../../../src/stockpilot/main.py)
   pour l'historique, [bff.py](../../../src/stockpilot/bff.py) pour B2B.
   Utiliser [architecture](../../../docs/architecture.md), puis le contrat du lot concerné.
2. Cartographier uniquement les couches touchées : domaine déterministe, application
   (ports/use cases/workflows), adaptateurs et présentation. LangGraph est
   intentionnellement dans l'application ; ne pas imposer une isolation totale des frameworks.
   Les sessions/transactions Mongo appartiennent aux adaptateurs.
3. Comparer le maintien de l'existant et la plus petite extension utile :
   coût, dépendances, contrats HTTP/DTO, anciens checkpoints, migrations, tests,
   performance et retour arrière. Ne pas introduire ORM, broker, microservices ou
   Kubernetes sans besoin démontré dans la demande.
4. Pour B2B, lire [contrat assistant](../../../docs/security/b2b-assistant.md) et
   [RAG/conversations](../../../docs/security/tenant-rag-conversations.md) si concernés.
   Mongo fait autorité sur les réservations ; PostgreSQL conserve le journal et
   les checkpoints. Leur réconciliation n'est pas une transaction distribuée.
5. Consigner une décision durable dans [les ADR](../../../docs/project/ARCHITECTURE_DECISIONS.md)
   seulement si le travail le demande : contexte, options, choix/statut, conséquences,
   compatibilité, preuve et décision humaine restante. Une proposition reste une proposition.

## Sécurité et coopération
Ne pas remplacer les adaptateurs tenant par les stores globaux historiques.
Conserver user, organization et customer comme identités distinctes.
Faire porter l'implémentation locale par Backend ou AI Engineer ; Security examine
une frontière de confiance modifiée, QA les preuves. Charger seulement les compétences
nécessaires ; ceci n'autorise pas la création de sous-agents ni une revue externe.

## Tests et acceptation
Relier chaque contrat affecté à un test existant ou à une régression ciblée, puis aux
commandes canoniques d'AGENTS. Pour un plan seul, ne pas changer l'application ni
lancer des services. Documenter la lecture des anciens DTO/checkpoints et l'effet
d'une version de schéma plus récente sur un rollback applicatif.

## Livrable et arrêt
Fournir carte d'impact, arbitrage, plan minimal, critères de preuve et risques.
GO/NO-GO concerne le périmètre analysé. Suspendre la mutation si elle exige un
changement ORDER non demandé, une attribution de données historiques ou une
migration persistante non autorisée ; poursuivre l'analyse possible et préciser
l'arbitrage humain nécessaire.

## Exemple
« Évalue l'impact d'une évolution des réponses assistant B2B sur le client Vue,
les checkpoints et les anciens contrats, sans implémentation. »
