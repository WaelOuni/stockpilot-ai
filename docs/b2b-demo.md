# StockPilot AI B2B Demo — cible canonique future

**Statut : cible produit, non opérationnelle et non validée de bout en bout.**
Ce guide est le point d'entrée canonique de la future démonstration officielle.
Il ne crée pas d'installation et ne remplace pas aujourd'hui les trois démos locales.

## Architecture cible

Navigateur Vue → HTTPS/reverse proxy → BFF FastAPI/session serveur → ports métier
tenant-scoped. Keycloak fournit l'identité humaine ; StockPilot contrôle organisation,
membership, permissions et ownership. MongoDB replica set pour catalogue/stock/
ORDER/imports, PostgreSQL/pgvector pour documents et checkpoints, Ollama derrière
le réseau privé. Observabilité sans tokens, prompts ou documents sensibles.

B1–B4 fournissent les stores internes. V13.8.1 fournit une [composition HTTP de
test B1/B2/B3](security/b2b-http-api.md), opt-in ; auth_test reste auth/session seul.
V13.8.2 ajoute un [parcours Vue B2B](b2b-user-guide.md) validé sur services
jetables pour catalogue/commandes/stock/imports. L’assistant HTTP V13.8.3 et la
validation unifiée avec RAG/Ollama/restauration restent à réaliser.
Un VPS Docker Compose isolé suffit comme première hypothèse ; Kubernetes/K3s
nécessite un besoin mesuré et une décision séparée, pas un prérequis IAM.

## Scénarios requis avant promotion

Avec deux organisations synthétiques et des SKU/titres/threads externes identiques :
1. Connexion Keycloak, session BFF sécurisée et organisation active vérifiée.
2. Catalogue et stock propres au tenant ; aucun prix ou stock étranger.
3. Conseil documentaire avec passages RAG scoppés avant le LLM.
4. Conversation privée du créateur, historique/reprise sans checkpoint étranger.
5. Préparation sans réservation, confirmation explicite, réservation atomique.
6. Rejeu sans double réservation, annulation autorisée au plus une fois.
7. Ajustement physique refusé sous reserved, mouvement atomique et traçable.
8. Import : aperçu sans mutation, validation, application atomique et historique.
9. Permissions USER/manager/admin et révocations ; accès étrangers refusés.
10. Reprise après redémarrage, panne contrôlée et preuve de restauration.

Les tests internes de stockage ne prouvent pas ce parcours navigateur complet.

## Conditions de promotion

- PR revues et CI verte sur le SHA réellement construit ; aucune promotion implicite
  après fusion de code.
- Routes BFF dédiées, CSRF/Origin, quotas, autorisations et erreurs testés sans proxy
  générique vers l'API historique globale.
- UX V13.8.2, organisation active, états de session/erreurs et tests E2E isolés.
- Fenêtres de révocation inter-stores arbitrées ; aucun rôle Keycloak implicite.
- HTTPS/domaine, secrets dédiés, bases/Ollama non exposés, séparation des rôles DB.
- Provisioning explicite d'identités, memberships et acheteurs, sans mapping deviné.
- Sauvegarde/restauration Mongo/PG éprouvée, politique de données/rétention,
  responsabilité opérateur, observabilité et procédure de reprise.
- Installation dédiée autorisée dans une mission ultérieure, sans réutiliser
  les données/secrets/ports des démos. Aucun client réel avant cette validation.

## Historique préservé et retrait ultérieur

| Installation | Port | Usage conservé |
|---|---|---|
| Stable v017 | 5191 | Référence et rollback local |
| Candidate V13.3 | 5193 | Validation historique, dépendance stockpilot-v133-validation |
| Historique | 5175/8003 | Ancien parcours, checkout à6f09496 |

[Runbook historique](demo-runbook.md), [scénario commercial historique](product/demonstration.md)
et rapports datés restent utiles pour reprise/traçabilité ; ils ne décrivent pas
une connexion B2B Vue actuelle. Ne pas appliquer leurs commandes aux installations
protégées pendant un chantier de développement.

Retrait futur uniquement après promotion validée de cette cible, inventaire des
dépendances, sauvegardes restaurables, rétention des preuves et **nouvelle
autorisation explicite** pour chaque installation/image/volume concerné.
Aucune suppression d'installation, quatrième environnement persistant ou
déploiement n'est réalisé par B4.
