---
name: stockpilot-security
description: "Revoir ou corriger une frontière de confiance StockPilot AI : Keycloak OIDC/PKCE, sessions BFF, CSRF, permissions, isolation tenant, secrets et logs. Utiliser pour un risque ou accès sensible concret ; pas pour toute modification de code ni pour une certification de sécurité générale."
---

# Sécurité StockPilot

## Périmètre et préconditions
Lire [AGENTS.md](../../../AGENTS.md), identifier l'acteur, la ressource, le parcours
et les droits de la demande. Une revue se fait en lecture seule ; corriger seulement
dans le périmètre autorisé. Distinguer JWT client historique, sessions admin
historiques et sessions BFF B2B.

## Workflow
1. Tracer navigateur → route → service d'autorisation → requête/transaction/store,
   ainsi que les données envoyées au modèle. Lire [modèle de menace](../../../docs/security/stockpilot-ai-threat-model.md)
   et le contrat actuel concerné ; les limites datées ne décrivent pas forcément HEAD.
2. Pour l'identité : [Keycloak/BFF](../../../docs/security/keycloak-bff.md),
   [OIDC provider](../../../src/stockpilot/infrastructure/identity/oidc_provider.py),
   [sessions](../../../src/stockpilot/application/services/bff_sessions.py).
   Vérifier issuer/audience/exp/nonce, state lié au cookie et consommé une fois,
   PKCE S256, redirects fixes, rotation/révocation et expiration côté serveur.
   Aucun token fournisseur au navigateur, aucun rôle Keycloak directement traduit
   en permission métier, aucun utilisateur créé implicitement au callback.
3. Pour HTTP : [contrat B2B](../../../docs/security/b2b-http-api.md) et
   [frontière](../../../src/stockpilot/presentation/api/b2b_boundary.py).
   Vérifier cookie Secure/HttpOnly, Origin exact, CSRF et refus des identités
   forgées. Une protection Vue ou un ID organisation dans le body ne suffit pas.
4. Pour les données : [politique tenant](../../../docs/security/tenant-authorization-boundary.md),
   [permissions](../../../src/stockpilot/domain/services/authorization.py) et
   [RAG/conversations](../../../docs/security/tenant-rag-conversations.md).
   Vérifier organisation dans requêtes/écritures/unicités/replays, association
   acheteur explicite et own = créateur. Un manager n'accède pas implicitement
   aux conversations d'autrui. Distinguer 401, refus, introuvable et panne ;
   ressource étrangère et absente restent indistinguables.
5. Chercher les fuites ciblées dans réponses, logs et erreurs : cookies, jetons,
   URIs privées, prompts et données personnelles. Rapporter emplacement et nature,
   jamais la valeur sensible. Ne pas lire/dumper un .env privé pour produire une preuve.
   Évaluer révocation concurrente et limites inter-stores sans promettre atomicité globale.

## Tests et acceptation
Utiliser les suites pertinentes :
[OIDC](../../../tests/test_oidc_provider.py),
[sessions BFF](../../../tests/test_bff_auth.py),
[contexte tenant](../../../tests/test_tenant_bff_context.py),
[autorisation](../../../tests/test_tenant_authorization.py).
Puis gates AGENTS/[testing](../../../docs/testing.md). Pour une frontière de stockage,
ajouter les cas réels dédiés : deux organisations, deux utilisateurs dans une même
organisation, IDs identiques, session expirée/révoquée, CSRF absent/incorrect,
membership modifiée et panne. Un succès unitaire ne prouve pas la sécurité en production.

## Livrable et arrêt
Fournir risques avec gravité, préconditions, chemin/ligne, impact, reproduction
assainie, correction minimale et test qui échouerait avant correction.
Donner GO/NO-GO borné et risques résiduels. Demander l'autorisation manquante
avant rotation de secrets, changement d'accès réel ou activation sensible.
Ne jamais contourner un refus d'isolation pour terminer une recette.

## Exemple
« Revois l'accès aux conversations B2B après un changement de compte dans
le même navigateur et propose les tests de non-fuite. »
