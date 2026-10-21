# StockPilot AI — retrouver une référence, vérifier le stock, préparer une commande

**Fiche de présentation — kit commercial, réconcilié le 21 octobre 2026.**

## Pour qui ?

Grossistes, distributeurs et responsables de comptoir dans l'outillage, avec un
catalogue de références bien identifiées et des demandes répétitives en français.
Le premier usage à explorer est une installation isolée pour une seule entreprise.

## Quel problème veut-on résoudre ?

Lorsqu'un client cherche un produit ou une alternative, l'équipe consulte parfois
plusieurs fichiers, vérifie le stock puis ressaisit la demande. StockPilot propose
de réunir recherche, vérification et préparation dans une conversation.
**Le temps gagné reste à mesurer avec votre équipe ; aucun gain client n'est validé.**

## Ce que permet le parcours historique

Fonctions à vérifier sur la copie de répétition autorisée ; le nouveau parcours
B2B Keycloak/BFF/Vue reste à assembler, voir le [guide canonique](../b2b-demo.md).

- Retrouver des produits par catégorie, budget, nom ou référence, puis consulter
  les prix et stocks présents dans le catalogue.
- Comparer une référence avec des alternatives sur les données disponibles,
  ajuster un budget ou demander moins de résultats dans la même conversation.
- Préparer une commande puis demander une confirmation explicite :
  **le conseil et la préparation ne réservent rien ; la confirmation réserve le stock.**
- Consulter ses commandes et demander leur annulation lorsque le statut le permet.
- Pour l'administrateur autorisé : consulter catalogue et tableau de bord, tracer
  des ajustements, prévisualiser puis importer de nouvelles références par CSV
  avec stock initial zéro. Configuration et contrôles préalables nécessaires.

## Ce qu'il faut savoir avant une démonstration

Les recherches couvrent un vocabulaire d'outillage limité ; une demande ambiguë
peut demander une précision. Prix et stocks sont des instantanés. Sans données
techniques vérifiées, le logiciel ne choisit pas « la plus puissante ».
Le modèle produit ne garantit ni devise ni TVA.

Aucune intégration ERP, synchronisation Excel continue, facturation ou paiement
n'est fournie. L'accès client demande une préparation technique ; il n'y a
pas encore de parcours Vue B2B complet. Keycloak/BFF, rôles et stockages isolés
existent en composants internes, sans démo unifiée validée ni gestion complète
des comptes prête pour un client.
**Ce n'est pas un SaaS prêt pour une ouverture publique.**

## Prochaine étape proposée

Un entretien de 20 minutes sur une tâche réelle, suivi si utile d'une démonstration
sur données fictives. Un pilote accompagné de **2 à 4 semaines** peut ensuite être
discuté : une entreprise, une installation isolée, un catalogue limité et des
critères convenus. Périmètre, accès, sauvegardes, support et budget sont à approuver.
Aucun achat ni engagement n'est déclenché par cette fiche.

Sources et détails : [positionnement](positionnement.md),
[démonstration](demonstration.md), [proposition pilote non contractuelle](offre-pilote.md).
