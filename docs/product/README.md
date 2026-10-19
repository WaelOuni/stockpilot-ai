# Préparation d'un premier pilote StockPilot

Kit commercial V15.1 préparé le 19 octobre 2026 sur `85df5c4`, réconcilié le
**21 octobre 2026** avec main `394bd7c` (fusion #50). Ce nom historique du kit
ne constitue pas une version technique succédant à V13.7. Aucune installation,
prise de contact ou validation client n'est réalisée par ce chantier.
Fonctions du code, version déployée et résultats
commerciaux restent trois preuves différentes.

Parcours simple : lire la fiche produit, personnaliser un message sans l'envoyer,
mener l'entretien après autorisation, répéter la démonstration sur une copie
autorisée, puis discuter le pilote et mesurer la valeur.

| Document | Usage |
|---|---|
| [Fiche produit — une page](fiche-produit.md) | Présentation courte à un responsable de magasin |
| [Deux messages de prospection](prospection.md) | Brouillons, aucun envoi sans autorisation |
| [Proposition pilote](offre-pilote.md) | Cadre non contractuel de 2–4 semaines et budget hypothétique |
| [Positionnement et présentation](positionnement.md) | Public envisagé, message commercial, limites et comparaison honnête |
| [Script commercial historique de 9 minutes](demonstration.md) | Parcours JWT reproductible sur une copie synthétique autorisée |
| [Démo B2B future canonique](../b2b-demo.md) | Cible Keycloak/BFF/Vue, non opérationnelle ; conditions de promotion |
| [Onboarding du pilote](onboarding-pilote.md) | Conditions d'entrée, données, exploitation et sortie |
| [Coûts et hypothèses de prix](couts-et-pricing.md) | Calculs vérifiables, scénarios et seuils économiques |
| [Validation et objections](validation-commerciale.md) | 12 questions, grille avant/après et décisions de poursuite |

Le parcours historique accessible par Vue utilise un catalogue global,
MongoDB, Ollama et les checkpoints PostgreSQL. Un `customer_id` y identifie un
acheteur ; ce n'est ni un utilisateur ni une organisation isolée.
Les fondations internes B2B V13.7.1–B4 sont intégrées : identité Keycloak/BFF,
permissions, stores Mongo/PG scoppés, commandes, imports, RAG et conversations.
Le BFF expose seulement auth/session : ces services ne sont pas encore assemblés
dans un parcours métier HTTP/Vue. Le [guide canonique](../b2b-demo.md) fait autorité
pour la cible future, sans remplacer aujourd'hui les installations historiques.
Le mode SaaS demeure fermé par défaut. Aucun de ces
documents ne demande de l'activer ni de partager une installation entre entreprises.

Les preuves techniques restent dans les rapports de validation correspondants et
les tests ; le [contrat Advice](../catalogue-advice.md), le
[contrat ORDER](../order-flow.md), le [contrat de données](../database.md) et la
[frontière SaaS](../security/saas-auth-architecture.md) font autorité.
Une présentation doit identifier le commit réellement lancé et le résultat des
tests récents, plutôt que reprendre des comptes de tests historiques.

Priorité humaine : sélectionner un problème répétitif chez un prospect volontaire,
mesurer son coût actuel, puis convenir d'un pilote isolé limité. Aucun prospect n'a
été contacté et aucun service n'a été acheté dans le cadre de cette préparation.

## Prêt et encore à décider

Supports rédigés : script de 8–10 minutes avec secours, fiche courte, 12 questions,
cadre pilote, grille de mesure et deux messages. La préparation documentaire
n'atteste pas une répétition avec Ollama, une capture de secours disponible, une
installation autorisée, un prospect intéressé ou une acceptation du prix.

Le responsable humain doit choisir le destinataire et autoriser le contact,
approuver l'environnement de répétition, puis convenir du périmètre, des données,
des accès, des responsabilités, du support et du budget avant un pilote.
Les DEMO v017 et V13.3 restent protégées. Voir la [roadmap](../project/ROADMAP.md).
