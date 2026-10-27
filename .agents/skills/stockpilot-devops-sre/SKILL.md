---
name: stockpilot-devops-sre
description: "Diagnostiquer CI GitHub Actions/Docker Compose et préparer migrations, restauration, traçabilité ou rollback StockPilot AI. Utiliser pour un besoin d'exploitation concret ; pas pour du métier, une refonte cloud ou un déploiement implicite."
---

# DevOps et SRE StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md), identifier opération, cible, propriétaire,
SHA du code et état de l'autorisation. Lire [CI/CD](../../../docs/ci-cd.md),
[Actions](../../../docs/github-actions.md) ou [démo B2B](../../../docs/b2b-demo.md)
selon le besoin. Une fusion GitHub n'atteste pas le code d'une installation.
Ne pas relancer un audit historique de récupération sans demande.

## Workflow
1. Pour CI, partir de [.github/workflows](../../../.github/workflows/) et des
   [gardes](../../../scripts/ci/). Préserver pins, permissions minimales et
   validation du SHA. Ne pas activer un workflow manuel réel ou un runner privé
   sous prétexte que la suite déterministe est verte.
2. Pour Compose, inspecter la configuration suivie et les contrôles du runner
   avant l'exécution. Identifier projet, labels, ports, montages et services.
   Utiliser une cible jetable propre pour les tests ; aucun port/volume d'une
   installation existante réutilisé. Lire [testing](../../../docs/testing.md).
3. Pour BFF réel, le harnais [validate_bff_keycloak.py](../../../scripts/tests/validate_bff_keycloak.py)
   dispose de B2B_TEST_PORT_SET=isolated ; consulter
   [b2b_test_ports.py](../../../scripts/tests/b2b_test_ports.py) et vérifier les ports
   avant de l'utiliser. Ne pas arrêter un occupant pour libérer un port.
4. Pour migrations, utiliser les runners existants décrits dans
   [Mongo](../../../docs/mongodb-migrations.md) et [PostgreSQL](../../../docs/postgresql-migrations.md).
   Préparer status/plan, versions/checksums, effet et préconditions ; pas d'apply
   persistant, de takeover de verrou ni de modification d'historique implicites.
5. Pour sauvegarde/restauration demandée, définir l'ensemble apparié Mongo/PG/Keycloak,
   identités et écritures à suspendre dans une fenêtre autorisée. Restaurer dans
   une cible indépendante vide ; comparer données/versions et réconciliation.
   Les sources de récupération restent préservées. RPO/RTO sont mesurés, pas supposés.
6. Préparer rollback avec digest/version des images, schémas et backup compatible.
   [Expérience B2B](../../../docs/b2b-product-experience.md) précise que les anciennes
   versions ne valident pas un historique de migration plus récent : remettre
   seulement une ancienne image ne suffit pas. Ne pas inventer de downgrade.
   Consulter [observabilité](../../../docs/observability.md) pour preuves assainies.

## Tests et acceptation
Exécuter les contrôles canoniques adaptés puis la recette isolée prévue par le
runbook de l'opération. Pour une PR CI, vérifier déclencheurs, branches, permissions,
SHA, services et cleanup limité aux ressources créées. Distinguer config inspectée,
image construite, restauration réalisée et disponibilité utilisateur vérifiée.
Un /health réussi ne suffit pas à établir la readiness.

## Livrable et arrêt
Fournir plan opérateur concret, cible/provenance, préflight, procédure, vérification,
rollback et GO/NO-GO. Avant l'action sensible, demander seulement l'autorisation
encore manquante, avec cible et conséquences précises. Autorisation de préparer
n'est pas autorisation de déployer. En cas de cible ambiguë, backup non restaurable
ou commit inconnu, arrêter la mutation et préserver l'état.

## Exemple
« Analyse un échec de CI d'intégration et prépare un plan de correction ; ne
modifie aucune installation. »
