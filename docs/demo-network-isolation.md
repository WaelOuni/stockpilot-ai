# DEMO : préparation réseau (V11.2, lot 1)

Ce lot prépare les configurations ; il ne donne accès à aucun testeur. Aucun
connecteur Cloudflare n'est installé ou lancé. Le lot 2 devra ajouter Access,
identités nominatives, clé JWT DEMO distincte et autorisations serveur avant toute
ouverture. Le lanceur local `bash scripts/run-demo.sh` reste inchangé.

## Préflight observé le 11 octobre 2026

WSL2 utilise NAT, eth0 172.18.84.66/20 (adresse dynamique). Apache2 PID 235,
service actif/enabled, écoute sur le port 80 avec le VirtualHost par défaut et
DocumentRoot `/var/www/html`. Il n'appartient pas au lanceur StockPilot et n'a pas
été arrêté. Windows relaie localhost avec wslrelay ; Docker Desktop publie les
bases sur toutes les interfaces IPv4/IPv6. L'accès LAN/Internet n'est pas validé.

MongoDB : `stockpilot-mongodb`, volume `stockpilot-ai_mongodb_data` sur `/data/db`
et volume anonyme existant sur `/data/configdb`. Replica set rs0, membre
`mongodb:27017`. PostgreSQL : `stockpilot-postgres`, volume
`stockpilot-ai_postgres_data`. DEV/DEMO/E2E partagent ces instances mais des bases
distinctes. Les publications préparées sont 127.0.0.1:27017 et 127.0.0.1:5432 ;
les commandes serveur, réseau Compose et volumes sont conservés. Les clients WSL
utilisent loopback/directConnection pour Mongo ; les conteneurs du réseau Compose
continuent à utiliser mongodb:27017 et postgres:5432, jamais leur propre localhost.
Les conteneurs de test release indépendants et l'observabilité ne sont pas ciblés.

Ollama Windows écoute sur `::`:11434. La lecture du pare-feu Windows a été refusée
sans élévation : aucune règle n'a été changée. Ces deux points restent des prérequis
avant toute démo distante. Aucun changement de binding actif n'a été appliqué.

## Passerelle locale préparée

`docker/demo-gateway.conf` est indépendant de Vite et de son proxy DEV. Il sert les
fichiers Vue compilés de l'image web existante, écoute sur **127.0.0.1:8086** et
n'autorise que GET/HEAD des routes SPA client et des assets JS/CSS compilés,
GET `/healthz`, et GET `/api/health` vers **127.0.0.1:8003/health**.
La sonde API n'est pas une preuve d'identité de base ; le préflight DEMO reste requis.

Toutes les API métier sont provisoirement refusées : l'identité distante et la
séparation des clés JWT ne sont pas encore établies. Cela inclut catalogue,
assistant, commandes client et mutations techniques. Admin, backoffice, Swagger,
chat/classify, fichiers Vite, sourcemaps et chemins inconnus sont refusés. La
liste méthodes/routes est fermée et sensible à la casse. Les chemins bruts avec
encodage %, double slash, backslash ou segment commençant par un point sont
refusés avant le choix de route normalisée. Les query strings ne sont pas des
routes et ne servent pas à transporter des credentials.

La sonde ne transmet aucun header client, cookie, JWT ou identité Cloudflare/
forwarded, ni corps de requête ; les Set-Cookie amont sont supprimés. Les logs
access sont désactivés et les diagnostics de requêtes Nginx ne sont pas conservés
(pour éviter les URL sensibles). Les réponses portent no-store/nosniff/no-referrer.
Il n'y a aucun endpoint de génération/récupération JWT dans la passerelle.
Le bundle Vue commun contient encore les composants administrateur : les refus
HTTP empêchent leurs accès serveur, mais ne retirent pas leur code JavaScript.
La navigation client peut afficher un composant ; aucune API administrative ne
devient accessible pour autant. Un build client dédié pourra être étudié au lot 2.

`docker/compose.demo-gateway.yml` ne démarre rien par défaut (profil explicite
local-preparation). Il utilise l'image frontend de production, sans plugin DEV,
sans montage de données, sans capability, filesystem readonly et tmpfs `/tmp`.
Le mode réseau host est nécessaire pour joindre le FastAPI WSL sur loopback.
**Sur Docker Desktop, il nécessite la fonctionnalité host networking activée et
un test effectif du chemin Docker → WSL :8003. Cette activation n'a pas été faite.**
Ne pas remplacer l'amont par le backend DEV :8000, ni élargir l'écoute pour résoudre
un problème Docker Desktop. Alternative à valider : Nginx natif dans WSL.
Les tests HTTP utilisent network=none et un amont simulé dans le même conteneur ;
ils ne valident pas ce chemin réel Docker Desktop. Aucun lancement de cette
passerelle sur le réseau host n'a été effectué.

## Application aux bases : fenêtre d'interruption obligatoire

Préparer et valider d'abord les sauvegardes et la restauration isolée selon
[le lot 1A](demo-backup-recovery.md). Les captures Docker ne sont pas des backups.

**Ne pas exécuter les commandes d'application avant accord explicite.** Changer
les ports publiés nécessite de recréer les conteneurs Mongo et PostgreSQL ; DEV,
DEMO et E2E seront indisponibles pendant cette fenêtre. Arrêter proprement les
applications et tests avant la capture finale. Ne jamais utiliser `down`, `down -v`,
`rm`, `prune`, `--renew-anon-volumes`, reset, seed ou réinitialisation du replica set.

1. Vérifier les sauvegardes existantes selon `docs/demo-runbook.md`. Une capture
   d'identité n'est pas une sauvegarde des données. Choisir un répertoire privé
   nouveau hors Git (le fichier rollback contient les credentials existants).
2. Capturer en lecture les images exactes, tous les montages (y compris le volume
   anonyme configdb), identité Mongo/PG et compteurs des collections DEV/DEMO/E2E :

```bash
.venv/bin/python scripts/demo_network_snapshot.py /tmp/stockpilot-network-before-approved
```

Le répertoire est créé 0700, les fichiers 0600. Ne pas les partager ni en faire des
artefacts CI. `loopback.compose.json` et `rollback.compose.json` utilisent les
images locales exactes et déclarent chaque volume existant external : aucune
création implicite de volume et aucun pull d'image. Le script n'écrit dans aucune
base et masque les erreurs d'infrastructure pouvant contenir des credentials.

3. Examiner les configurations en privé et vérifier le nom du projet existant
   `stockpilot-ai`, les mounts, les images, commandes et réseaux. Puis, après accord :

```bash
docker compose -p stockpilot-ai -f /tmp/stockpilot-network-before-approved/loopback.compose.json config --quiet
docker compose -p stockpilot-ai -f /tmp/stockpilot-network-before-approved/loopback.compose.json up -d --no-deps --no-build --pull never mongodb postgres
```

4. Attendre le primaire Mongo et PostgreSQL disponible. Avant reprise, comparer :

```bash
.venv/bin/python scripts/demo_network_snapshot.py /tmp/stockpilot-network-after-approved --compare /tmp/stockpilot-network-before-approved/snapshot.json
ss -lnt
docker ps --format '{{.Names}}\t{{.Ports}}'
```

Le comparateur exige mêmes images, mounts, replicaSetId/membres, system_identifier
PostgreSQL et compteurs Mongo. Il refuse une modification concurrente des
compteurs : effectuer ces vérifications durant la fenêtre sans activité métier.
Il ne prouve pas l'identité byte pour byte des données PostgreSQL/Mongo.
Confirmer en lecture les connexions TCP locales, API DEV /health, préflight DEMO
sans initialisation, puis fonctionnement des applications après reprise autorisée.
Vérifier depuis une seconde machine que 27017/5432/11434 sont inaccessibles.
Ne pas lancer DEMO si elle est absente/incompatible et ne pas confirmer INITIALIZE.

## Retour arrière sans suppression

En cas d'échec, garder les applications arrêtées, vérifier les volumes et appliquer
la capture originale **avec accord** :

```bash
docker compose -p stockpilot-ai -f /tmp/stockpilot-network-before-approved/rollback.compose.json config --quiet
docker compose -p stockpilot-ai -f /tmp/stockpilot-network-before-approved/rollback.compose.json up -d --no-deps --no-build --pull never mongodb postgres
.venv/bin/python scripts/demo_network_snapshot.py /tmp/stockpilot-network-rollback-checked --compare /tmp/stockpilot-network-before-approved/snapshot.json
```

Ce retour arrière recrée les conteneurs avec les mêmes volumes/images et rétablit
les anciennes publications réseau larges : il n'est pas une solution de sécurité
durable. Aucun fichier de données n'est supprimé. Si les identités divergent,
arrêter l'investigation avant toute écriture ; ne pas essayer de reseeder. Garder
les captures privées jusqu'à validation de la reprise. Toute future commande
Compose doit conserver le même projet et les mêmes volumes, notamment configdb.

## Windows/Ollama : contrôle manuel administrateur

Dans PowerShell **administrateur**, sans modifier les règles :

```powershell
Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 80,27017,5432,11434 }
Get-NetFirewallPortFilter | Where-Object { $_.LocalPort -in '27017','5432','11434','Any' } | Get-NetFirewallRule | Select-Object DisplayName,Enabled,Direction,Action
Get-NetFirewallApplicationFilter | Where-Object { $_.Program -match 'ollama|docker' } | Get-NetFirewallRule
Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction
netsh interface portproxy show all
```

Examiner aussi les plages de ports, adresses/interfaces autorisées et politiques
GPO ; un filtre de port exact seul ne suffit pas. Identifier les règles larges
Ollama/Docker avant toute modification. Faire confirmer la politique puis limiter
11434 au chemin Windows↔WSL nécessaire, sans autoriser LAN/Internet. En NAT,
`dev-env.sh` utilise l'adresse du host Windows : passer Ollama à 127.0.0.1 sans
adapter/valider ce chemin casserait l'accès WSL. Ne pas changer automatiquement
le mode WSL, les règles ou OLLAMA_HOST. Valider avec les modèles locaux existants,
sans exposition réseau ni requête de génération pour un simple test de connexion.
Apache2 : confirmer son utilité avec l'administrateur avant de limiter/arrêter
le service ; aucun arrêt n'est inclus dans ce lot.

## Validation locale sans données réelles

```bash
docker compose config --quiet
docker compose -f docker/compose.demo-gateway.yml --profile local-preparation config --quiet
# Image locale frontend de production construite avec le Dockerfile existant :
docker build -f frontend/Dockerfile -t stockpilot-web:v112-gateway-test .
DEMO_GATEWAY_TEST_IMAGE=stockpilot-web:v112-gateway-test DEMO_GATEWAY_HTTP_TESTS=1 .venv/bin/python -m pytest -q tests/test_demo_network_isolation.py
git diff --check
```

Les tests Nginx n'utilisent aucun port publié, réseau externe, volume de base ou
secret réel. Ils couvrent routes/méthodes, variantes encodées/casse, headers,
cookies, no DEV login et refus par défaut. Le mock de santé ne touche pas FastAPI.
