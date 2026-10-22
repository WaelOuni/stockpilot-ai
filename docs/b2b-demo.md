# StockPilot AI B2B Demo — cible canonique future

**Statut : cible officielle future ; validation jetable distincte de toute promotion.**
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
jetables pour catalogue/commandes/stock/imports. L’[assistant HTTP/Vue V13.8.3](security/b2b-assistant.md) est intégré par #53. V13.8.4 prépare sa composition canonique et les preuves de
restauration jetables ; aucune installation persistante n’est autorisée.
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

## Validation canonique V13.8.4 — point d'entrée unique

La topologie est définie dans [compose.b2b-demo.yml](../docker/compose.b2b-demo.yml).
Le seul opérateur canonique est [scripts/b2b_demo.py](../scripts/b2b_demo.py) :
il sépare **validate jetable** et le **cycle interactif local V13.9** décrit ci-dessous.
Aucune commande ne promeut ou déploie un SaaS public.
Une campagne terminée détruit uniquement ses conteneurs, réseaux, volumes et
tags de build identifiés. Elle ne crée pas une quatrième installation durable.

```sh
# Dans un worktree isolé, avec son propre venv, node_modules et Chromium existant.
# Vérifier aussi la capacité et la disponibilité de l'instance d'inférence.
STOCKPILOT_DEMO_OLLAMA_URL=http://ADRESSE_PRIVEE_HOTE:11434 \
STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL=http://ADRESSE_PRIVEE_DOCKER:11434 \
  .venv/bin/python scripts/b2b_demo.py validate --regressions
```

Le script n’utilise aucun .env implicite et désactive les profils hérités.
Les ports de gestion source/copie sont fixés et ne peuvent être remplacés par
une variable pointant sur un port historique.
Il refuse un Docker distant, des ports occupés, un tag de build existant
ou moins de10Gio libres sur Ubuntu/C:. Il n'installe aucun modèle. Les modèles
qwen2.5:7b et nomic-embed-text doivent déjà être présents. Les deux URLs privées sont explicites car WSL et Docker Desktop peuvent avoir
des routes différentes ; elles doivent désigner le fournisseur autorisé.
Le contrôle de capacité est une marge de recette, pas une garantie de capacité commerciale.
Le sous-réseau est comparé aux réseaux Docker et routes WSL/Windows accessibles ;
cela ne certifie pas toutes les routes internes du moteur Docker Desktop ou d'un VPN.

### Composition et confidentialité réseau

| Composant | Surface / stockage |
|---|---|
| Proxy + Vue construite | HTTPS127.0.0.1:57914 ; assets et /b2b-app seulement ; aucune route API historique |
| OIDC public | HTTPSauth.stockpilot.test:57915 ; /realms/stockpilot-dev/ et /resources/ seulement ; /admin et diagnostics refusés |
| BFF | Réseaux privés, aucun port publié ; confiance au seul IP du proxy ; headers réécrits, jamais hérités du navigateur |
| Keycloak26.8.1 | Réseau edge + identity ; PostgreSQL15 dédié sur identity internal ; aucun admin bootstrap |
| Mongo8 replica set | Volume propre au projet, accès de gestion **loopback57916** pour la recette |
| PostgreSQL16/pgvector | Volume propre au projet, accès de gestion **loopback57918**, port interne55432 |
| Mongo authentifié de régression | Profil regression seulement, tmpfs/loopback57919, vérification des credentials read-only ; absent du parcours de démo |
| Ollama | Endpoint privé explicite déjà disponible ; pas de publication, téléchargement, remplacement ou arrêt |

Les images d'infrastructure sont épinglées par digest. Les deux images applicatives
sont construites depuis le worktree et identifiées par leur ID exact ; aucun tag
historique n'est remplacé. Les réseaux n'attachent aucun conteneur protégé.
Le bridge application permet le NAT de gestion loopback nécessaire à Docker Desktop ;
identity est internal. Les bases ne sont jamais publiées sur0.0.0.0.
La suppression des ports de gestion, l'authentification/les rôles minimaux des bases
et la politique réseau définitive sont obligatoires avant installation durable.

Le DNS auth.stockpilot.test est un alias Docker et une résolution de processus
de test/Chromium, sans modification des hosts Windows/WSL. Un certificat éphémère
est explicitement approuvé par les clients de recette : CA du BFF et HTTPX,
empreinte SPKI Chromium limitée à cette campagne. Aucune désactivation TLS distante.
La configuration réelle exige deux autorités HTTPS, certificats et DNS approuvés ;
la boucle locale de test ne devient pas une configuration Internet par changement d'URL.

Les secrets sont générés à l'exécution et restent hors Git dans tmp/v1384-*/
(répertoire0700, fichiers privés). Les données sont synthétiques.
Les callbacks n'exportent aucun token à Vue. Origin/Host exacts, CSRF lié à la
session et PKCE/state/nonce restent ceux du BFF existant.
production_saas reste fermé ; b2b_business_test conserve ses garde-fous de noms
et ports de bases. Le cycle V13.9 conserve ces garde-fous pour des données synthétiques locales ;
il ne crée aucun mode de production.

### Ordre opérateur et provisioning

1. Contrôler moteur local, espace, ports, routes et provenance ; construire les images.
2. Créer uniquement les quatre bases/services d'infrastructure propriétaires.
3. Vérifier replica set, readiness et discovery OIDC HTTPS.
4. Appliquer explicitement Mongo0001/0002/0003 et PG0001/0002 sur les cibles
   jetables identifiées ; créer les index d'identité/session explicitement.
5. Provisionner les fixtures synthétiques sur ces bases fraîchement créées,
   puis vérifier les identités A/B et le refus initial d'un utilisateur inconnu.
6. Démarrer BFF et frontend/proxy, tester le parcours et les invariants durables.
7. Répéter la restauration indépendante, conserver les rapports privés puis
   retirer les seules ressources de recette attestées.

Le provisioning actuel est celui du harness contrôlé : deux subjects Keycloak
stables, deux organisations/memberships, acheteurs explicites, SKU DRILL-001
aux prix 49/99 EUR et stocks 10/2, documents ALPHA/BETA distincts et identifiants
documentaires identiques. Il refuse la répétition non contrôlée par les contraintes
uniques sur une cible qui doit être fraîche ; il n'efface pas une cible occupée.
Les callbacks ne provisionnent jamais une identité ou un privilège.
Ce mécanisme de fixtures n'est pas un outil de provisioning client de production.

### Sauvegarde et restauration appariées

La campagne ferme le proxy, draine le BFF (arrêt gracieux de ses seuls workers),
puis arrête son seul Keycloak. Aucun autre écrivain applicatif n'est admis.
Elle inspecte tout journal assistant pending : une opération inconnue bloque ;
le scénario de panne explicitement injecté conserve sa clé et son résultat Mongo vérifié.

Trois archives privées, noms fixes : mongodump --db de toute la base B2B
(collections, validateurs et index inclus), pg_dump custom de la base applicative,
pg_dump custom de la base Keycloak complète. Les valeurs logiques avant/après
doivent être identiques ; un changement invalide la sauvegarde.
Le manifeste contient empreintes SHA256, tailles, images et IDs source, hashes
des données/index/options et liste explicite des opérations pending.
Ce protocole **quiescent** n'est ni une preuve de sauvegarde à chaud concurrente,
ni un snapshot distribué atomique. Mongo TTL et tout écrivain externe restent à
contrôler ; une divergence constatée interdit de déclarer un backup cohérent.

La cible de restauration doit être un **autre projet**, avec autres IDs physiques,
images identiques, volumes propriétaires et bases vides. Les noms/commandes/URI
ne proviennent jamais du manifeste. Aucun --drop, --clean ou écrasement de cible
occupée ; checksum incorrect ou doute de provenance = refus.
Mongo restaure le namespace dédié ; PG utilise pg_restore --single-transaction
--exit-on-error --no-owner --no-privileges. Les rôles initiaux sont recréés
explicitement par les conteneurs de base, pas déduits d'un dump utilisateur.

Avant de démarrer les applications restaurées, la recette rapproche toutes les
collections/documents/index/options Mongo, les tables/lignes PG et Keycloak,
puis valide les historiques/checksums et structures des migrations existantes.
L'issuer HTTPS et les subjects Keycloak sont conservés, sans réécriture des
utilisateurs/memberships. La clé BFF de la copie est tournée : ancien cookie
refusé, reconnexion obligatoire. Cela ne prétend pas révoquer la session SSO source.

Mongo reste autoritaire pour les réservations : après restauration d'un commit
Mongo suivi d'un échec de checkpoint PG, resume consulte la même opération et
reconstruit la projection, sans nouvelle réservation. Le rejeu utilise la même clé ;
l'annulation libère au plus une fois. Aucune transaction distribuée n'est simulée.
Le test démontre que la source reste réservée pendant que la copie est annulée.

Les archives contiennent identités, hashes de mots de passe, clés Keycloak,
documents privés et sessions : **données sensibles même synthétiques**.
Elles restent privées, jamais jointes à la PR. Avant données réelles :
chiffrement avec clés distinctes, coffre des secrets/certificats, sauvegarde hors
machine, accès/rétention testés et restauration régulière sont des prérequis.
Les empreintes ne fournissent pas une signature contre un opérateur local malveillant.
Aucun RPO/RTO commercial n'est annoncé par cette petite répétition.

### Exploitation et reprise

- Chaque conteneur a une limite CPU/RAM et logs5Mo ×2 ; pas de seconde stack Grafana.
  Mongo est plafonné à2Gio, avec cache WiredTiger explicite0,5Gio ; le plafond
  initial768Mio a échoué par OOM pendant la suite complète, pas pendant le parcours démo.
- BFF émet uniquement catégorie fixe, statut, durée et request_id ; aucun corps,
  URL callback, token, cookie, identité, prompt ou passage documentaire.
- Access/error logs du proxy désactivés pour éviter la fuite de code/state ;
  probes HTTP et logs BFF structurés apportent le diagnostic de disponibilité.
- /health BFF est une liveness interne, jamais une preuve de parcours fonctionnel.
  Health Mongo/PG vérifient primary/readiness ; Keycloak expose sa probe ready
  au seul port interne9000. Discovery/login réels vérifient le parcours OIDC.
- Échecs OIDC/CSRF/permission se suivent par catégories/statuts corrélés,
  les timings RAG/embedding/génération par preuves synthétiques. Avant exploitation,
  il reste à définir alertes, rétention et export des métriques sans données privées.
- Admission8 requêtes BFF,600/min, un LLM par worker ; ces limites sont locales,
  pas des quotas distribués. Corps64Kio, CSV256Kio/100lignes, pagination bornée,
  prompts24Kio et génération512tokens restent ceux des contrats existants.
- Échantillons docker stats pendant la campagne et mesures d'espace sont des
  observations locales, pas une capacité client ni un pic garanti.
- restart=no est volontaire pour la recette. Après autorisation durable, arrêter/
  démarrer explicitement et choisir une politique adaptée, sans migration au startup.

Incident : fermer l'admission de l'installation autorisée, conserver les clés
d'opération et les preuves, identifier Mongo comme vérité ORDER, vérifier les
journaux PG puis réconcilier. Timeout ne signifie jamais abort.
Ne pas lancer un nouveau numéro d'opération pour compenser une réponse perdue.
Ne pas appliquer de DDL/réparation d'index à l'aveugle ; les runners doivent refuser
le drift ou l'historique incohérent. Restaurer une **copie** avant toute bascule.
Les procédures existantes de [migrations Mongo](mongodb-migrations.md) et
[PostgreSQL](postgresql-migrations.md) restent les références de schéma.

### Mise à jour et rollback futurs — après autorisation

Avant changement, relever SHA/digests, paramètres, versions de schéma et état des
opérations ; créer un backup apparié et prouver sa restauration sur copie.
Fermer l'admission et drainer l'installation autorisée, examiner le plan des
migrations, puis construire/lancer le SHA approuvé. Vérifier login, isolation et
invariants durables avant réouverture.

Un rollback d'image n'est acceptable que si le schéma reste compatible.
Aucun downgrade automatique. Sinon, restaurer une copie et décider d'une bascule
contrôlée, avec reconnexion et réconciliation des clés originales.
Ne jamais mélanger un ancien dump PG avec un Mongo récent sans rapprochement,
ni écraser un serveur ayant reçu de nouvelles commandes après la sauvegarde.
Les fenêtres de perte possibles doivent être arbitrées avant données réelles ;
cette recette ne fournit pas de RPO/RTO commercial.

### Promotion et périmètre conservé

La validation de cette PR n'autorise ni persistance ni accès public. La décision
humaine devra approuver local/serveur, capacité, DNS/certificats, secrets/roles DB,
chiffrement/rétention, sauvegarde externe/restauration, opérateur et rollback,
puis le SHA effectivement construit. Aucun P0/P1 non traité sur le périmètre
choisi ne peut être accepté implicitement.
Les anciennes compositions et scripts restent historiques ou des outils de CI
encore référencés : aucun gain démontré ne justifie leur suppression ici.
Leur retrait physique reste une autre autorisation après sauvegarde/restauration.


Références des limites des outils :
[MongoDB mongodump](https://www.mongodb.com/docs/database-tools/mongodump/),
[PostgreSQL pg_dump](https://www.postgresql.org/docs/16/app-pgdump.html),
[Keycloak reverse proxy](https://www.keycloak.org/server/reverseproxy).
Un hostname-admin distinct ne remplace pas le blocage réseau/proxy des routes admin.

## Cycle interactif local V13.9 — après autorisation d’installation

Cette PR fournit les commandes ; elle **n’installe pas** la démo persistante.
Une seule source Compose est conservée. L’opérateur en rend une configuration
privée `compose.json`, avec les mêmes composants, sans ports DB publiés et sans
le service Mongo de régression. Ce fichier généré n’est pas une seconde variante
à maintenir. `validate` garde ses ressources jetables et son nettoyage propres.

### Prérequis et premier lancement

Utiliser un checkout dédié du SHA revu, Python3.12/venv et Docker Desktop/WSL
déjà disponibles, avec au moins 10 Gio libres sur Ubuntu et C:. Les images sont
construites avec des tags uniques et leurs IDs conservés ; aucune image
historique n’est remplacée. Ollama privé doit déjà contenir qwen2.5:7b et
nomic-embed-text ; aucun modèle n’est téléchargé.

Dans WSL, **uniquement après autorisation humaine d’installer** :

~~~sh
# Les deux URLs doivent désigner l’instance privée d’inférence autorisée.
export STOCKPILOT_DEMO_OLLAMA_URL=http://ADRESSE_PRIVEE_WINDOWS:11434
export STOCKPILOT_DEMO_OLLAMA_CONTAINER_URL=http://host.docker.internal:11434
.venv/bin/python scripts/b2b_demo.py init
~~~

`init` crée l’état privé sous `~/.local/share/stockpilot/b2b-demo/` (0700),
le projet unique `stockpilot-b2b-local`, quatre volumes dédiés, les secrets et
certificats stables. Il démarre ses bases, applique explicitement les migrations
Mongo0001/0002/0003 et PG0001/0002, provisionne les fixtures, vérifie le BFF puis
**arrête ses services**. Il ne lance aucun test destructif ou navigateur automatique.

Une répétition cohérente d’init constate l’installation sans la modifier.
Un état partiel/incohérent est refusé, jamais réinitialisé. Ne pas supprimer son
manifeste ni relancer un seed pour réparer : conserver les preuves et diagnostiquer.

### Actions Windows manuelles : DNS et confiance HTTPS

L’application utilise exactement :
`https://127.0.0.1:57914/b2b-app/`.
L’identité utilise `https://auth.stockpilot.test:57915/realms/stockpilot-dev`.
Les ports ne sont liés qu’à 127.0.0.1. Keycloak Admin/master/diagnostics sont refusés
au proxy. Les bases n’ont aucun port publié dans le rendu interactif.

Deux actions distinctes restent à effectuer par l’opérateur Windows :

1. Ajouter explicitement `127.0.0.1 auth.stockpilot.test` au fichier hosts Windows
   avec les droits nécessaires, sans écraser les autres entrées.
2. Examiner le certificat public `ca.crt` de cette installation puis l’approuver
   dans les autorités racines de l’utilisateur Windows autorisé. Ne jamais
   importer, copier ou publier `ca.key`, `tls.key` ou les fichiers JSON privés.

Exemple PowerShell, **à exécuter manuellement seulement après vérification** :

~~~powershell
# Remplacer UTILISATEUR_WSL ; seul le certificat PUBLIC est importé.
$stockpilotCa = '\\wsl.localhost\Ubuntu\home\UTILISATEUR_WSL\.local\share\stockpilot\b2b-demo\ca.crt'
Get-PfxCertificate -FilePath $stockpilotCa | Format-List Subject, Thumbprint, NotAfter
Import-Certificate -FilePath $stockpilotCa -CertStoreLocation Cert:\CurrentUser\Root
~~~

L’opérateur ne modifie ni hosts ni le magasin Windows. Son préflight les lit :
`start/resume` refusent une résolution étrangère, une CA non approuvée ou un
certificat expiré. La CA dure trois ans, le certificat serveur un an ; aucune
rotation silencieuse n’est effectuée. La rotation future doit être contrôlée et
peut invalider les sessions. Firefox peut utiliser un magasin distinct : le
préflight vise Windows/Chrome/Edge, pas tous les navigateurs possibles.

### Commandes usuelles

~~~sh
.venv/bin/python scripts/b2b_demo.py start
.venv/bin/python scripts/b2b_demo.py status
.venv/bin/python scripts/b2b_demo.py stop
.venv/bin/python scripts/b2b_demo.py resume
~~~

`start` et `resume` sont équivalents : contrôle des empreintes privées, IDs des
conteneurs/images/réseaux, identité des volumes et schémas avant ouverture du BFF.
Ils démarrent les conteneurs existants sans recréer un volume absent, refaire le
provisioning ou appliquer une migration. Déjà actif = aucun redémarrage.
Les protections de `b2b_business_test` restent inchangées, avec des namespaces
synthétiques dédiés persistés entre sessions ; ce n’est pas un mode SaaS public.

`status` montre services/health, URLs, version, migrations, disponibilité Ollama,
espace et noms des comptes. Bases arrêtées = contrôle vivant des schémas
indisponible, explicitement indiqué. Aucun mot de passe/token n’est affiché.
Si l’adresse Windows vue depuis WSL change, fournir de nouveau
`STOCKPILOT_DEMO_OLLAMA_URL` à start/status pour la sonde hôte seulement :
cela ne change ni les secrets ni l’endpoint privé du BFF enregistré à init.

`stop` ferme l’ingress avec nginx quit, envoie TERM au BFF et attend son arrêt
avant de vérifier les journaux durables puis arrêter Keycloak et les bases.
Il n’envoie aucun SIGKILL. Si le drain dépasse 110s ou si une sortie anormale est
détectée, les services restants sont conservés pour diagnostic. Les résultats
pending ne deviennent jamais une preuve d’abort ; garder les clés originales.
Un second stop est sans effet. Aucun down -v, retrait de volume ou reset.

### Comptes et fonctionnalités

Les identifiants sont dans `credentials.json` (0600), hors repository, uniquement
pour l’opérateur autorisé. Consulter ce fichier **localement**, sans le coller dans
une conversation, un log, une capture ou un rapport public.

| Code entreprise | Compte | Rôle et usage |
|---|---|---|
| org-a | demo-a-manager | MANAGER : commandes d’organisation et ajustements |
| org-a | demo-a-user | USER : commandes own=created_by, conversations privées |
| org-a | demo-a-admin | ORGANIZATION_ADMIN : imports CSV et droits organisationnels |
| org-b | demo-b-user | USER : démonstration de séparation A/B |

DRILL-001 existe dans A/B avec prix 49/99 EUR et stocks 10/2. Le document Garantie
porte un contenu ALPHA/BETA distinct. Les acheteurs sont liés explicitement.
Aucun compte plateforme global ni rôle Keycloak implicite n’est ajouté.

Catalogue, stock, assistant/RAG, conversations, préparation/confirmation,
réconciliation/annulation et administration autorisée restent les parcours Vue
existants. Un MANAGER n’a pas import:execute ; utiliser le compte A admin pour
les CSV. Sessions BFF : 30min absolues, 10min d’inactivité, sans refresh ;
reconnexion nécessaire après expiration. Logout BFF n’est pas une promesse de
logout SSO Keycloak. Les droits et ownership sont toujours revalidés côté serveur.

### Persistance, sauvegarde et incident

Préparation de mise à jour V13.9.3 : [audit daté et verdict BLOCKED](reports/v13.9.3-persistent-upgrade-readiness.md),
[runbook proposé](runbooks/v13.9.3-persistent-upgrade.md). La sauvegarde privée
de configuration réalisée par cet audit ne contient pas les bases. Ces documents
n'autorisent aucun arrêt, redémarrage ou upgrade de l'installation persistante.

Les quatre volumes, tous les secrets/certificats et les clés d’opération restent
identiques lors de stop/resume. Retrouver commandes, reserved, conversations,
checkpoints et comptes est vérifié sur installations **temporaires**.
Une absence de volume, un fichier privé changé, une migration incomplète ou un
endpoint étranger ferme le démarrage. Il n’existe aucune restauration automatique.

Avant une sauvegarde manuelle, fermer l’admission et drainer le BFF/Keycloak ;
ne pas recopier des fichiers MongoDB vivants. Réutiliser le protocole quiescent
V13.8.4 décrit plus haut : mongodump authentifié, pg_dump applicatif et Keycloak,
manifestes/checksums, contrôle des opérations pending, puis restauration dans
une **copie distincte**. Adapter les outils aux credentials dédiés via fichiers
privés/flux protégés, jamais en arguments visibles ou logs. Les routines V13.8.4
de test refusent volontairement les ressources interactives : elles ne sont pas
un outil implicite de restauration de cette installation. L’automatisation de
backup interactif est un point d’extension, pas une commande livrée ici.
La copie restaurée doit recréer explicitement les utilisateurs Mongo et rôles/
grants PostgreSQL dédiés, sans réutiliser les secrets d’une installation historique.

Conserver hors machine, chiffrer et tester la rétention avant des données réelles.
Aucun snapshot distribué, révocation atomique inter-stores, haute disponibilité,
SLA, mise à jour automatique ou réparation de schéma n’est revendiqué.
La clé Mongo du replica set est vérifiée et conservée dans son volume config ;
les secrets de conteneurs restent accessibles à l’administrateur Docker local.

Les anciennes démos, images de rollback et preuves restent protégées. Cette
installation synthétique locale ne devient la démo officielle promue qu’après
acceptation humaine des preuves, de l’hébergement et des responsabilités.

### Preuves V13.9 et limites de validation

Campagne du 21 octobre 2026 sur cette branche, uniquement sur ressources jetables :

| Contrôle | Résultat |
|---|---|
| Backend unitaire | 3258 réussis, puis 3 nouveaux cas de sortie anormale réussis ; 80 tests dédiés au cycle de vie au total |
| Intégrations MongoDB/PostgreSQL | 498 réussies via validate --regressions ; 1 intégration optionnelle non sélectionnée |
| Frontend | 502 Vitest ; typecheck, lint et build réussis |
| Évaluations déterministes | 122 réussies |
| Cycle interactif réel | 9 groupes opérateur et 15 groupes Chrome Windows réussis ; comptes, réservations, clés et checkpoints conservés |
| Recette canonique historique | 48 contrôles réussis, dont Ollama réel et restauration Mongo/PG/Keycloak indépendante |
| Qualité | Ruff/format, pip check, syntaxe Python, liens locaux et recherche de signatures de secrets réussis |

Les preuves privées sont conservées dans tmp (ignoré par Git) :
- cycle interactif : tmp/v139-72fa8e29b8664a40ae7dc664daae7c4f/report.json,
  SHA256 3193f3d9b374515e31a856016ac8c4662e37e4bf931a414db732af626b03b4da ;
- recette historique : tmp/v1384-_au69qkc/report.json,
  SHA256 760c7fbadc0e28db3ccace3983a8fa5651dba17f178d8f802aea63530f9c47d9.

Le Chrome automatique est un processus Windows réel, avec un profil temporaire,
une résolution limitée au processus et la clé publique exacte du certificat de
test. Ce n'est **pas** une validation manuelle du navigateur normalement approuvé :
la résolution DNS et la confiance CA normales restent à configurer explicitement.
Les fichiers hosts et magasins racines Windows ont été mesurés inchangés.

Après nettoyage des ressources de test : 28 conteneurs préexistants, dont les
5 de la candidate actifs, 30 volumes et les réseaux préexistants conservés.
Les campagnes vérifient IDs/états/StartedAt/restarts/images ; fichiers protégés,
métadonnées des secrets, volumes/réseaux et checkout historique ont été comparés.
Cela ne prétend pas prouver l'égalité de chaque donnée d'une base active ni lire
les secrets. L'état canonique persistant n'a pas été initialisé.

Pour reproduire le test de cycle de vie, avec les deux endpoints Ollama privés
déjà configurés comme plus haut :
~~~sh
.venv/bin/python scripts/tests/validate_interactive_demo.py --windows-node /CHEMIN/EXISTANT/vers/node.exe
~~~
Le runner impose un état temporaire et retire seulement ses ressources attestées.
Le chemin Windows Node doit être accessible via /mnt/c ; aucun outil n'est installé
par cette commande. La recette historique reste :
~~~sh
.venv/bin/python scripts/b2b_demo.py validate --regressions
~~~
