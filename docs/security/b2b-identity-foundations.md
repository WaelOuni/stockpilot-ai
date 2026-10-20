# ADR V13.7.1 - Fondations B2B, inactives par defaut

Decision validee par la demande V13.7.1 : Keycloak auto-heberge fournira
l'identite OIDC humaine, Authorization Code + PKCE S256, avec BFF FastAPI et
sessions serveur. StockPilot controle ses organisations, memberships et droits.
Cette PR ne livre que les contrats et leur politique testable. Elle ne rend
aucune installation multi-tenant ou publiquement exploitable.

## Responsabilites et frontiere de confiance

```text
futur adaptateur OIDC / session BFF (injecte uniquement par le serveur)
  -> HumanAuthentication.authenticate(contexte opaque)
  -> cle exacte ExternalIdentity(issuer, subject)
  -> IdentityRepository : utilisateur pre-provisionne et actif
  -> MembershipRepository : organisation explicitement selectionnee et autorisee
  -> OrganizationRepository : organisation active
  -> Principal humain immuable, permissions calculees
  -> require : nouvelle verification authentification + stores + portee + proprietaire
```

Le domaine ne connait ni FastAPI, ni Keycloak, ni JWT. ExternalIdentity est une
cle de recherche, pas une preuve d'authentification et ne porte aucun flag verified.
Le service public resolve appelle le port d'authentification de confiance :
une cle externe, un role ou un dictionnaire fourni par le navigateur ne remplace
pas ce port. Aucun adaptateur de production ni endpoint ne le compose ici.
L'adaptateur futur doit verifier issuer, audience, signature, duree, type humain
et session avant de retourner cette cle ; il ne doit pas accepter une identite machine.

User utilise un ID interne et (issuer, subject), jamais l'email. Organization et
Membership ont leurs propres IDs, statuts et dates conscientes du fuseau horaire.
Une invitation n'est pas active. PLATFORM_ADMIN est reserve et absent de Role :
aucun role n'autorise implicitement un acces global.
customer_id reste le proprietaire acheteur historique des commandes.
Il n'est ni user_id, ni organization_id ; aucune correspondance automatique,
migration ou organisation par defaut n'est creee.

## Permissions initiales

| Role | Permissions |
|---|---|
| READ_ONLY | catalogue:read, stock:read, order:read_own |
| USER | READ_ONLY + order:create, order:cancel_own |
| MANAGER | USER + stock:adjust, order:read_organization, order:cancel_organization |
| ORGANIZATION_ADMIN | MANAGER + import:execute, organization:manage_members |

Ce choix initial limite l'import et la gestion des membres a l'administrateur
d'organisation. Modifier cette matrice exige une revue metier et de non-regression.

La permission seule ne suffit jamais. Toute decision exige ResourceScope issu du
stockage de confiance dans l'organisation active ; les permissions *_own exigent
en plus owner_user_id == Principal.user_id. L'absence de portee/proprietaire refuse
l'acces. Pour une creation/liste, le use case futur construit la portee depuis le
contexte serveur et filtre lui-meme la requete. L'identifiant d'organisation du
navigateur reste un choix candidat valide par membership, jamais une autorisation.

require re-authentifie le contexte et reconstruit le principal depuis les stores
pour refuser toute copie falsifiee, role devenu obsolete, session changee ou
membership suspendue. Le domaine ne pretend pas proteger contre un code serveur
malveillant. L'integration future doit conserver la decision et le filtrage jusqu'a
la transaction pour traiter les changements concurrents de permissions.

## Contrats de persistance et erreurs

Aucun adaptateur runtime : les doubles memoire resident uniquement dans les tests.
Ils ne sont pas une solution de production. Pas de collection/index ajoute au
demarrage, pas d'ecriture, provisioning ou modification des stores metier existants.

La persistance future doit garantir user_id et (issuer, subject) uniques,
organization_id et membership_id uniques, et au plus une membership active
(user_id, organization_id). Les ports renvoient tous les candidats d'identite et
les memberships pour que le service refuse doublons et incoherences sans prendre
le premier enregistrement. Une lookup d'organisation ambigue doit lever
IdentityRepositoryError. Les erreurs techniques se propagent ; elles ne deviennent
ni un acces autorise, ni un utilisateur inconnu silencieux.

IdentityResolutionError et AuthorizationDenied sont des refus metier.
IdentityRepositoryError represente un echec technique/contrat de persistance.
L'adaptateur HTTP futur devra traduire identite non authentifiee en 401,
permission/membership insuffisante en 403 et indisponibilite en 503 sans fuite
de credentials ou details des stores.

## Compatibilite et V13.7.2

Aucune integration a main.py, route, configuration, login Vue, callback, JWKS reseau,
refresh token ou session BFF. JWT HS256 client, cookies/CSRF admin, host/origin,
admission LLM, ORDER et production_saas fail-closed restent inchanges.
La portee des produits, commandes, RAG et checkpoints existants n'est pas modifiee.

Avant V13.7.2 : choisir le provisionnement explicite, implementer et tester
l'adaptateur Keycloak/BFF (discovery/JWKS et rotation, state/nonce/PKCE,
cookies et CSRF, expiration/revocation), choisir un stockage identite avec
contraintes, puis definir le lien metier user/customer sans remapper l'historique.
L'ouverture publique et l'isolation des stores sont des chantiers distincts.
Le rollback consiste a retirer ces modules inactifs ; aucune donnee a restaurer.

## Validation

Tests unitaires purs : refus d'identites et memberships inconnues/ambigues,
statuts inactifs, choix entre deux organisations, usurpation de principal,
permissions et propriete, erreurs techniques, immutabilite et dates.
Suites metier/securite, evaluations deterministes et CI sur la PR #44 completent
ces contrats ; elles ne prouvent ni integration OIDC ni isolation multi-tenant.
