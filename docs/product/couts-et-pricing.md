# Coûts et hypothèses de tarification

Document de travail du 17 octobre 2026. Tous les montants ci-dessous sont des
estimations en euros hors taxes, sauf indication contraire. Aucun devis, achat,
abonnement ou prix accepté par le marché. La TVA, le change, les promotions et les
conditions territoriales doivent être revérifiés avant une proposition réelle.

## Repères externes historiques

Les relevés datés ci-dessous sont conservés comme hypothèses du kit ; ils n'ont
pas été revérifiés lors de sa réconciliation du 21 octobre. Aucun tarif actuel
ni fournisseur recommandé n'en est déduit.

| Poste | Repère public consulté le 17 octobre 2026 | Portée |
|---|---|---|
| VM CPU | Grille Hetzner Allemagne/Finlande : CX43 à 15,99 €/mois hors IPv4 après ajustement de juin 2026 | Simple repère de tarif ; ni disponibilité ni capacité StockPilot démontrée. La page commerciale indique une disponibilité limitée |
| Sauvegarde objet | Scaleway Paris Standard Multi-AZ : 0,01606 €/Go/mois | Stockage seulement ; transferts, opérations et restauration à budgéter séparément |
| GPU hébergé | Scaleway L4-1-24G Paris : affichage à 0,79 €/h | Prix arrondi affiché ; disque et autres options en supplément ; aucune machine provisionnée |

Sources : [ajustement Hetzner](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[disponibilité commerciale Hetzner](https://www.hetzner.com/cloud/cost-optimized/),
[stockage Scaleway](https://www.scaleway.com/fr/tarifs/storage/),
[GPU Scaleway](https://www.scaleway.com/en/pricing/gpu/).
Leur sélection sert au calcul, pas à une recommandation de fournisseur définitive.

Calculs illustratifs : 100 Go de sauvegardes × 0,01606 = **1,606 €/mois** de
stockage brut ; retenir une provision de 5 € ne prouve pas qu'elle couvre tous les
transferts. 730 heures × 0,79 = **576,70 €/mois** de GPU seul ; la page fournisseur
peut afficher une autre approximation mensuelle et un tarif horaire plus précis.
Ni cette VM ni ce GPU n'ont été benchmarkés avec StockPilot. La latence obtenue sur
la machine Windows de la candidate n'est pas transposable à ces offres.

MongoDB et PostgreSQL peuvent être autogérés sur une VM sans ligne de service
managé séparée dans ce modèle de coût. Cela ne supprime ni stockage, maintenance,
sauvegardes, contrôle des licences ou risque d'exploitation. Leurs conditions
doivent être évaluées pour la distribution et l'hébergement envisagés :
[licence PostgreSQL](https://www.postgresql.org/about/licence/),
[SSPL et FAQ MongoDB](https://www.mongodb.com/legal/licensing/server-side-public-license/faq).
Cette estimation ne constitue pas une analyse juridique de ces licences.

## Scénarios économiques par installation

Hypothèses communes internes : temps valorisé à **50 €/h**, maintenance technique
allouée **1 h/mois**, support **1,5 h/mois**. Ces valeurs ne sont pas des tarifs
observés. Les incidents, migrations et SLA restent hors forfait proposé.
Les instances sont isolées par entreprise ; aucune mutualisation multi-tenant
opérationnelle n'est présumée.

| Coût mensuel estimé | A : matériel/inférence du client | B : VM + inférence locale client sécurisée | C : VM + GPU dédié hébergé |
|---|---:|---:|---:|
| VM application + Mongo/PostgreSQL | 0 € pour le fournisseur | 30 € provision interne | 30 € provision interne |
| LLM / embeddings | Matériel client ; aucun coût par appel supposé | Matériel client + liaison privée à valider | 576,70 € calcul GPU |
| Stockage/liaison supplémentaires | 0 € fournisseur | 20 € provision interne | 20 € provision interne |
| Sauvegardes externes | 5 € provision | 5 € provision | 5 € provision |
| Supervision / outils alloués | 10 € provision | 10 € provision | 10 € provision |
| Maintenance : 1 h × 50 | 50 € | 50 € | 50 € |
| Support : 1,5 h × 50 | 75 € | 75 € | 75 € |
| **Coût fournisseur estimé** | **140 €** | **190 €** | **766,70 €** |
| Hypothèse de prix mensuel à discuter | **299 €** | **399 €** | **1 290 €** |
| Contribution avant coûts fixes/acquisition | 159 € (53,2 %) | 209 € (52,4 %) | 523,30 € (40,6 %) |

A n'inclut pas le coût supporté directement par le client : par exemple **60 € de
matériel amorti + 15 € d'électricité/mois**, hypothèses à remplacer par ses factures
et mesures. Le coût économique total A deviendrait 215 €/mois avant la marge.
B ajoute dépendance au site client, latence et liaison réseau : ce schéma n'est pas
validé par la candidate et ne doit pas être proposé comme une offre disponible.
C suppose des ressources dédiées coûteuses et une nouvelle validation de modèle,
de sécurité et de charge avant tout devis. Aucun hébergement public n'est lancé.

La contribution n'est pas un bénéfice : restent développement produit, acquisition,
administratif, assurances, taxes, impayés et astreinte éventuelle. Les provisions VM
de 30 € ne promettent pas un dimensionnement pour un nombre de clients ou d'appels.
Le partage futur d'un GPU entre installations demanderait admission, quotas,
isolation, mesure de contention et une revue distincte ; il n'est pas inclus.

## Coût variable et sensibilité

Formule de suivi : `coût = ressources dédiées + allocation outils/maintenance +
heures de support × coût horaire + consommation marginale + transferts`.
Avec Ollama local, aucun tarif par jeton n'est facturé par le logiciel dans ces
scénarios ; cela ne rend pas l'inférence gratuite. Mesurer énergie, occupation GPU,
latence et attente avant d'annoncer un quota. Une recherche Advice peut effectuer
classification puis présentation ; compter les appels réellement observés.

Une heure de support additionnelle coûte 50 € selon l'hypothèse. En A, un prix à
149 € ne laisserait que 9 € avant coûts fixes au support nominal, et une contribution
négative après cette heure. Cela motive un pilote limité et un suivi du temps ;
cela ne prouve pas qu'un prix de 299 € sera accepté.

Un service LLM hébergé facturé à l'usage serait une option future : calculer
`jetons entrée × tarif entrée + jetons sortie × tarif sortie`, ajouter embeddings,
réseau et support, puis vérifier confidentialité et comportement. Aucun fournisseur,
tarif ou changement de modèle n'est choisi ici.

## Offres à tester, sans engagement

- **Découverte** : démonstration synthétique et entretien, sans import de données
  réelles ; décider ensuite si un pilote a un objectif mesurable.
- **Pilote accompagné A** : 2–4 semaines, une entreprise et un périmètre limité ;
  hypothèse de mise en service **900 €** pour 12 h × 50 € = 600 € de travail estimé,
  plus 300 € de contribution avant acquisition ; mensualité indicative 299 €.
- **Installation hébergée dédiée C** : hypothèses 1 500 € de mise en service et
  1 290 €/mois ; soumise à validation technique, contractuelle et à devis. Pas de
  promesse de SLA ni de disponibilité 24/7.

Un budget commercial plus faible peut rendre le projet non viable avec ce service
accompagné. Réduire un périmètre de support ou renoncer est préférable à masquer le
coût. Ne pas vendre du SaaS partagé à 29 €/mois en supposant une architecture qui
n'existe pas encore. Tester plusieurs niveaux sans annoncer une grille définitive.

Avant toute vente : obtenir prix réels de ressources disponibles, mesurer une
charge représentative, tester sauvegarde/restauration, estimer le support avec le
pilote, vérifier les licences des composants/modèles et faire valider le contrat.
Le marché tunisien nécessite une recherche locale de prix, de fiscalité et de
modalités de paiement ; aucune conversion automatique EUR/TND n'est proposée.
