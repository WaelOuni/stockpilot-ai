---
name: stockpilot-product-growth
description: "Qualifier les besoins B2B de StockPilot AI, prioriser un backlog par valeur, préparer onboarding pilote, qualification prospect et démonstration de 8 à 10 minutes. Utiliser pour découverte ou progression commerciale ; pas pour implémenter une feature, contacter un prospect ou promettre un SaaS public."
---

# Produit et développement commercial StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md), puis le [kit produit](../../../docs/product/README.md)
et les seuls supports utiles. Identifier public, problème, source de stock,
décideur et niveau de preuve. Les capacités fusionnées, déployées et démontrées
sont trois faits distincts. Les kits historiques doivent être recoupés avec HEAD.

## Workflow
1. Partir d'une tâche récente du prospect et de sa méthode actuelle :
   fréquence, durée avec vérification, erreurs, coût, alternatives et contraintes.
   Utiliser [validation commerciale](../../../docs/product/validation-commerciale.md)
   et [prospection](../../../docs/product/prospection.md). Marquer les réponses
   absentes comme inconnues ; ne pas fabriquer témoignages ou entretiens.
2. Qualifier source/référent des données, sponsor, utilisateur, décideur, budget
   déclaré, délai, environnement, exigences d'accès et signal d'engagement.
   Distinguer intérêt, accord de pilote, paiement et renouvellement.
3. Prioriser dans le format de [roadmap](../../../docs/project/ROADMAP.md) :
   problème/preuve, valeur attendue, effort, risque, dépendances, prochaine
   expérience et critère d'arrêt. Proposer un petit lot mesurable ; ne pas
   ajouter facturation, ERP, codes-barres ou features au seul motif de vendre.
   Modifier le backlog seulement si demandé, sans créer automatiquement des issues.
4. Préparer une démonstration de 8–10 minutes à partir de
   [demonstration.md](../../../docs/product/demonstration.md), en indiquant historique
   ou B2B. Recouper [guide B2B](../../../docs/b2b-user-guide.md) et
   [expérience produit](../../../docs/b2b-product-experience.md).
   Prévoir contexte/besoin, conseil vérifié, préparation, confirmation distincte,
   limites et prochaine décision ; mesurer la répétition et prévoir un repli.
   Ne pas lancer une démo existante ni confirmer une vraie commande implicitement.
5. Cadrer [offre pilote](../../../docs/product/offre-pilote.md) et
   [onboarding](../../../docs/product/onboarding-pilote.md) : copie autorisée,
   accès, restauration prouvée, formation, support, mesure avant/après, sortie
   et responsable. Chiffrer les coûts avec [pricing](../../../docs/product/couts-et-pricing.md)
   en distinguant hypothèses, devis et prix accepté.

## Sécurité, tests et acceptation
Ne pas publier de données prospect/client dans Git. Aucun contact ou message
externe sans instruction explicite ; produire des brouillons seulement.
Ne pas annoncer SaaS prêt pour Internet, SLA, gain financier ou protection
universelle contre les hallucinations sur la seule base de la CI.
Vérifier chaque affirmation par code/rapport daté/démo autorisée ; comptabiliser
échecs et coût du support autant que gains. Aucun test applicatif pour une simple
rédaction ; une démonstration réelle requiert les préconditions QA/SRE adaptées.

## Livrable et arrêt
Fournir note de découverte, backlog priorisé ou script demandé, hypothèses,
preuves manquantes, prochaine expérience et critères GO/NO-GO du pilote.
Si la promesse dépend d'un accès, d'une restauration ou d'une fonctionnalité
non prouvés, bloquer cette promesse et expliciter la décision attendue.
Ne pas convertir une idée commerciale en travail applicatif non demandé.

## Exemple
« Prépare un entretien de qualification et une démonstration B2B de neuf
minutes pour un distributeur, sans contacter le prospect. »
