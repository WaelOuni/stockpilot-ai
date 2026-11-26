# StockPilot AI

StockPilot AI aide une équipe B2B à retrouver une référence, vérifier le stock disponible et préparer une commande dans le catalogue de son organisation. Le responsable des stocks dispose aussi d’un tableau de bord pour expliquer une rupture, suivre les réservations et examiner un besoin de réapprovisionnement.

La référence de maintenance **v0.2.9** est le commit `fccef111641c648ea1e9e21211523899d91353e5` de `main`. Cette version utilise le schéma MongoDB **7**. Pour reprendre le développement depuis une machine vierge, suivre le [guide de développement local](docs/LOCAL_DEVELOPMENT.md).

## Parcours métier

| Parcours | Ce que fait le produit |
|---|---|
| Catalogue et stocks | Recherche par SKU, catégorie et disponibilité, comparaison de produits, ajustements et imports CSV selon les permissions. Le stock disponible tient compte de la quantité réservée. |
| Assistant | Recherche en langage naturel, conseil documentaire et consultation des stocks. Les faits de catalogue proviennent des données de l’organisation. Les conversations et leur historique sont privés à leur créateur. |
| Commandes | Préparation avec SKU et quantité vérifiés, puis confirmation explicite. La réservation et l’écriture de la commande partagent une transaction Mongo. La clé d’opération permet de retrouver l’issue d’une confirmation interrompue sans doubler la réservation. |
| StockPilot Intelligence | Le manager ou l’administrateur habilité peut examiner les quantités, la valorisation au tarif courant par devise, les séries de demande documentées, les projections et les scénarios de remise. Le tableau indique les données manquantes et la portée des agrégats paginés. |

Une réservation ne prouve pas une vente. Les projections dépendent des faits commerciaux disponibles et les propositions de réapprovisionnement restent consultatives. [Contrat analytique](docs/validation/product-evolution-intelligence.md)

## Architecture et sécurité

La Vue 3/TypeScript servie sous `/b2b-app/` appelle le backend pour frontend (BFF) FastAPI. Keycloak assure la connexion OIDC avec PKCE. Le serveur conserve les jetons et associe la session à l’utilisateur, à son organisation et à ses permissions. Le navigateur utilise un cookie de session. Le BFF contrôle l’origine et le jeton CSRF des écritures, puis les cas métier vérifient les droits et la propriété des ressources. [Contrat HTTP B2B](docs/security/b2b-http-api.md)

Le domaine et les cas d’usage vivent dans `src/stockpilot/`. Les adaptateurs relient les ports à MongoDB en replica set (catalogue, stocks, commandes, identité applicative), à PostgreSQL/pgvector (documents, recherche vectorielle, conversations, journal et checkpoints) et à Ollama. LangGraph orchestre les échanges. Le journal et la clé d’opération assurent la réconciliation entre PostgreSQL et MongoDB après un résultat incertain. [Architecture](docs/architecture.md) · [Commandes et atomicité](docs/security/tenant-orders-atomicity.md)

Pour une question documentaire, la recherche RAG filtre d’abord les passages autorisés pour l’organisation. Le modèle propose une réponse à partir des extraits récupérés. Le serveur vérifie les citations contre les passages avant restitution et peut refuser une réponse insuffisamment étayée. Le corpus historique de développement se crée via `migrations/000_create_knowledge_chunks.sql`. Le parcours B2B dispose de migrations PostgreSQL distinctes. [RAG B2B](docs/security/tenant-rag-conversations.md) · [Qualification des citations](docs/validation/rag-live-qualification.md)

## Exploitation et reprise

Les images API, Web et Keycloak, les configurations Compose et les contrôles GitHub Actions font partie du dépôt. L’installation B2B utilise également MongoDB, PostgreSQL applicatif et la base de Keycloak. Les modèles Ollama peuvent résider sur un hôte privé distinct. Une vérification d’exploitation couvre la santé des services, l’accès HTTPS, l’identité de l’installation et les données des trois magasins. [CI et images](docs/ci-cd.md) · [Identité des sauvegardes VM](docs/b2b-vm-backup-identity.md)

Une restauration exige un jeu cohérent de données, les identités Keycloak, les secrets et certificats nécessaires, les images identifiées par digest et un opérateur compatible avec le schéma sauvegardé. Conserver les migrations 0001 à 0007. Les anciens runbooks et preuves décrivent la version qu’ils ont qualifiée. La validation sur cible indépendante précède toute bascule.

## Reprise du projet

v0.2.9 fournit un socle B2B exploitable en environnement maîtrisé. La latence des réponses documentaires dépend du modèle, du matériel et de la taille des passages. Les tests déterministes, les parcours navigateur et les essais avec Ollama réel mesurent des propriétés différentes. [Stratégie de tests](docs/testing.md)

La prochaine étape envisagée est la qualification d’un VPS. Elle demande une décision d’hébergement pour l’inférence, un accès privé aux magasins, une sauvegarde hors machine et un essai de restauration sur la cible avant toute ouverture. Le développement stable se poursuit par PR vers `main`.

Le [runbook VPS v0.2.9](docs/VPS_DEPLOYMENT.md) couvre la recette HTTPS avec les images de release, le contrat d’inférence et la restauration isolée. [CLAUDE.md](CLAUDE.md) regroupe les points d’entrée et les contrôles de développement.
