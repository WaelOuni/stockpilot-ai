# Préparation d'un premier pilote StockPilot

État documentaire du 17 octobre 2026, établi à partir de `main`
`b503af9b2fa3c746e8be05eae562a39b66d3ddbe` (V13.2 + V13.C1).
Ces documents préparent une validation commerciale ; ils ne prouvent ni une demande
du marché, ni la conformité d'un déploiement client, ni la disponibilité d'un SaaS.

| Document | Usage |
|---|---|
| [Positionnement et présentation](positionnement.md) | Public envisagé, message commercial, limites et comparaison honnête |
| [Démonstration de 8 minutes](demonstration.md) | Parcours reproductible sur des données synthétiques |
| [Onboarding du pilote](onboarding-pilote.md) | Conditions d'entrée, données, exploitation et sortie |
| [Coûts et hypothèses de prix](couts-et-pricing.md) | Calculs vérifiables, scénarios et seuils économiques |
| [Validation et objections](validation-commerciale.md) | Questions terrain, indicateurs et décisions de poursuite |

Le produit disponible est une application locale à catalogue unique : interface
Vue, catalogue MongoDB, assistant Ollama, commandes avec confirmation et checkpoints
PostgreSQL. Un `customer_id` identifie un client dans cette installation ; ce n'est
pas une organisation isolée. Le mode SaaS demeure fermé par défaut. Aucun de ces
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
