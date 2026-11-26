# Qualifier StockPilot AI v0.2.9 sur un VPS

**GO pour une recette VPS synthétique. NO-GO pour les données réelles.** Le BFF immuable v0.2.9 offre `b2b_business_test`, impose un nom Mongo jetable et, pour l’assistant, une base PostgreSQL de test sur port distinct de 5432. Les runners ont également des gardes de cible. L’accueil de clients exige une version revue avec mode métier durable, admission des migrations et provisioning des identités. Garder les gardes en place. Un changement d’issuer exige une qualification du mapping `(issuer, sub)`. [BffSettings](../src/stockpilot/infrastructure/config/bff_settings.py) · [PostgreSQL](postgresql-migrations.md)

## Ce qui change depuis la VM

| Élément audité | Choix VPS |
|---|---|
| Hyper-V, WSL, hosts/CA Windows, Schannel, redirections 57914/61914, découverte Ollama depuis WSL | Linux amd64 avec Docker Engine et Compose v2.24.4+, DNS, TLS public et endpoint HTTPS explicite. Les scripts Windows et les opérateurs scellés de la VM restent propres à leur installation. |
| API, Web, Keycloak GHCR privés | Trois digests publiés pour v0.2.9, commit `fccef111641c648ea1e9e21211523899d91353e5`. Nginx de l’image Web sert à la fois Vue et les deux hôtes TLS. Entrypoint BFF distinct de l’API historique. Toutes les images du Compose sont épinglées. |
| Réseau et identité | Une publication 443/tcp sur l’adresse choisie. Proxy à IP fixe hors pool dynamique, pairs de confiance exacts. Mongo authentifié, rs0 à un membre. PG16/pgvector et PG15 Keycloak sur réseaux privés dédiés. Les rôles runtime sont distincts des opérateurs DB. |
| Persistance et reprise | Quatre volumes nommés par projet, config et fichiers secrets privés, arrêt gracieux et `unless-stopped`. Un VPS et un membre rs0 forment un seul domaine de panne. |
| Migration et sauvegarde VM | Le worker historique contient des hypothèses de schéma et des identités de démo. Le kit VPS part des runners embarqués, valide Mongo 7 et les deux versions PG, et restaure les magasins à froid. Le procédé VM conserve son propre runbook. |

[Compose VPS](../docker/vps/compose.yml) · [Topologie de démo](b2b-demo.md) · [Identité des sauvegardes VM](b2b-vm-backup-identity.md)

## Préparer l’hôte et les paramètres

1. Réserver un VPS Linux amd64 avec horloge synchronisée, disque chiffré, console de secours, marge disque pour les volumes et une restauration complète. Vérifier le noyau avec le digest Mongo retenu. La CI DB utilise Ubuntu 22.04 et un noyau 6.8 en raison d’une incompatibilité Mongo connue sur des noyaux récents. Vérifier CPU/RAM/IO sous charge synthétique, capacité libre et alertes disque, expirations TLS et âge du dernier backup restaurable.
2. Créer un compte d’exploitation nominatif avec clé SSH, vérifier sa connexion et son sudo dans une seconde session. Configurer `PubkeyAuthentication yes`, `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no` (adapter si MFA). Vérifier `sshd -t` et la configuration effective avec `sshd -T` avant reload. Restreindre SSH au VPN/CIDR d’administration au pare-feu hébergeur, refuser le reste en IPv4 et IPv6. Docker confère des privilèges équivalents à root. [OpenSSH Ubuntu](https://ubuntu.com/server/docs/how-to/security/openssh-server/)
3. Pour l’ouverture de la **recette**, autoriser 443/tcp aux destinataires prévus. Vérifier les ports depuis un autre réseau, y compris IPv6 si un AAAA existe. Tenir compte du filtrage des ports publiés par Docker. Garder privés 27017, 5432, 55432, 8000, 8080, 9000 et 11434. Filtrer la sortie du BFF vers l’inférence autorisée via le VPN ou le pare-feu. [Pare-feu Docker](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
4. Choisir deux FQDN distincts, l’IP publique, le sous-réseau Docker libre et l’IP fixe du proxy. Vérifier routes VPN/LAN et réseaux Docker existants. Obtenir un certificat public valide couvrant les **deux** SAN, par exemple via ACME DNS-01 avec un droit DNS limité. Le défi DNS évite l’ouverture de 80/tcp. Gérer les renouvellements sur l’hôte. [Certbot](https://certbot.eff.org/instructions?ws=other&os=pip)
5. Accorder au VPS la lecture des trois packages GHCR. Connecter Docker à GHCR avec un credential helper et un jeton limité à la lecture via stdin. Garder une copie chiffrée hors machine des images/digests si le RTO doit couvrir une panne GHCR. Figer le SHA du kit opérateur séparément du SHA applicatif.

Sur un hôte **neuf**, depuis le checkout revu, ouvrir un shell sans variables Compose/StockPilot héritées. Le dossier d’état doit appartenir au compte opérateur. Les commandes suivantes supposent Bash, Python 3 et curl. Adapter le fichier exemple avant le premier démarrage. Il contient uniquement les paramètres de site.

```sh
state=/etc/stockpilot-vps
install -d -m 700 "$state"   # avec le propriétaire opérateur si créé via sudo
install -m 600 docker/vps/.env.example "$state/settings.env"
# Éditer settings.env. RUNTIME_GID doit valoir id -g de l’opérateur.
cfg="$state/settings.env"
project=stockpilot-vps
dc() { docker compose --env-file "$cfg" -p "$project" -f docker/vps/compose.yml "$@"; }
sitecheck() (
  set -a
  . "$cfg"
  if [ "$HTTPS_BIND" = 127.0.0.1 ]; then
    python3 docker/vps/verify.py --loopback-port "$HTTPS_PORT"
  else
    python3 docker/vps/verify.py
  fi
)
(set -a; . "$cfg"; python3 docker/vps/prepare.py)
dc config --quiet
dc --profile operator pull
```

Le préparateur crée un répertoire privé neuf, sans utilisateur de démonstration. Les montages sont individuels et en lecture seule. Installer la chaîne complète dans `private/tls/fullchain.pem` (0644) et la clé dans `private/tls/privkey.pem` (0640, groupe RUNTIME_GID). Le répertoire TLS doit être traversable par ce groupe. Le BFF utilise le bundle CA système copié à la préparation. Renouveler avec un deploy-hook qui installe une paire concordante, exécute `dc exec -T proxy nginx -t` puis `dc exec -T proxy nginx -s reload`. Vérifier les deux hôtes après renouvellement et tester le renouvellement ACME à blanc. Garder les clés ACME et DNS dans le coffre opérateur.

## Premier démarrage sur données synthétiques

Vérifier que projet, réseaux et volumes sont neufs et que le port est libre. Utiliser un nom de projet distinct pour chaque essai. Déclarer un seul opérateur pendant bootstrap, sauvegarde et reprise. Démarrer les bases puis initialiser rs0 **une seule fois** après le lancement définitif de mongod. La sonde Mongo attend déjà un primaire.

```sh
set -e
dc up -d mongo postgres keycloak-db
# L’initialisation de l’image démarre d’abord un mongod temporaire.
for i in {1..60}; do
  if dc exec -T mongo mongosh --quiet --eval 'if (!db.hello().isreplicaset) quit(1)' >/dev/null 2>&1; then break; fi
  sleep 1
done
dc exec -T mongo mongosh --quiet --file /dev/stdin < docker/vps/rs-init.js
dc up -d --wait mongo postgres keycloak-db
dc run --rm --no-deps qualify init-empty
dc run --rm --no-deps qualify check
dc up -d --wait
dc ps
sitecheck
```

`init-empty` crée les schémas, index et rôles BFF. Un échec partiel impose l’inspection du projet ou un nouvel essai vierge. À la reprise ordinaire, utiliser seulement `dc up -d --wait`, `qualify check` et la vérification HTTPS. Les health checks prouvent la disponibilité de processus. La vérification HTTPS contrôle Vue, les routes fermées, discovery, l’initiation BFF, PKCE, CSRF et le callback exact. La lancer aussi depuis un client autorisé avec les deux variables d’hôte et sans l’option loopback. Pour ouvrir la recette, modifier `HTTPS_BIND` pour l’IP du VPS derrière le filtrage puis recréer uniquement le proxy. Garder le port externe 443.

Le realm `stockpilot` est importé seulement s’il est absent. Client confidentiel `stockpilot-bff`, HTTPS requis, code flow et PKCE S256, origines exactes. L’issuer est `https://AUTH_HOST/realms/stockpilot`, le callback `https://APP_HOST/b2b/auth/callback` et le retour de logout `https://APP_HOST/b2b/auth/logged-out`. L’administration Keycloak reste accessible aux opérateurs depuis la frontière privée. Pour la recette authentifiée, provisionner des comptes synthétiques via l’administration privée et des associations explicites dans le mécanisme d’identité applicative de la recette. Le callback ne crée aucun compte. Vérifier connexion/annulation/logout, expiration, refus inter-organisations, conversations privées et confirmation ORDER sur fixtures. [Recette BFF et Keycloak](security/keycloak-bff.md)

## Inférence

Configurer `INFERENCE_URL` avant préparation. Une valeur vide désactive l’assistant. L’image accepte une origine HTTPS unique pour génération et embeddings. Conserver la même version effective des poids et de la quantification que le corpus qualifié, en plus des noms `qwen2.5:7b` et `nomic-embed-text` (v1, 768 dimensions). Archiver les identifiants des modèles avec la configuration. Le SHA d’une image API ne fige pas les modèles distants.

| Option | Contrat et compromis |
|---|---|
| GPU Ollama externe | Routage privé, TLS de confiance depuis le conteneur et accès limité au VPS. Coût fixe/énergie et capacité administrée. Les passages restent dans l’infrastructure choisie. Mesurer RTT, démarrage à froid, génération p95 et capacité en concurrence. Une panne GPU ou VPN rend l’assistant indisponible. |
| Inférence distante | Le fournisseur ou une passerelle privée doit accepter les contrats Ollama exacts. Une compatibilité OpenAI seule est insuffisante. La passerelle gère l’authentification amont si nécessaire. Coût variable par requête/token, quotas et dépendance au fournisseur. Valider localisation, rétention et confidentialité des extraits avant tout envoi. |

Exercer sur un corpus synthétique `POST /api/generate` avec `model`, `prompt`, `stream:false`, schéma JSON dans `format` et `options` (température/num_predict). Contrôler `response`, JSON conforme, troncature et délais. Pour `POST /api/embed`, envoyer `model` et `input`, vérifier `embeddings[0]`, les 768 valeurs finies et la stabilité du classement/citations sur les vecteurs existants. Rejouer les refus faute de preuve, timeout et indisponibilité. Ne changer ni modèle ni index sans qualification dédiée. Ne placer aucun token dans l’URL. [Adaptateur LLM](../src/stockpilot/infrastructure/llm/ollama_adapter.py) · [Embeddings](../src/stockpilot/infrastructure/embeddings/ollama_embedding_adapter.py)

## Sauvegarde hors VPS et reprise

Procédé physique à froid sur images et architecture identiques. Il conserve les bases système, rôles, ACL, index, journaux Mongo, données pgvector, realm, clés de signature Keycloak et identités. Le snapshot inclut les quatre volumes, le dossier privé (clé rs0, secrets DB/BFF/OIDC, CA et paire TLS), le fichier de paramètres et la configuration d’exécution. Conserver à part le SHA du kit, l’accès GHCR, l’inventaire pare-feu/DNS/VPN/ACME et les moyens de récupération du coffre.

1. Sur stockage **hors VPS** indépendant, initialiser une fois un dépôt restic chiffré via S3 HTTPS ou REST HTTPS. Séparer le droit d’écriture des droits de suppression/rétention. Garder mot de passe restic et accès de récupération dans un coffre hors VPS. Tester leur disponibilité sur la machine de reprise. Le fichier `vault.env` (0600, hors du répertoire sauvegardé) fournit `RESTIC_REPOSITORY`, `RESTIC_PASSWORD` et les identifiants du backend. L’option `--lab-vault` est réservée au stockage local de simulation.
2. Définir RPO, RTO, fréquence, rétention et alerte avec l’exploitant. Une capture froide demande une fenêtre d’interruption. Vérifier la capacité, les schémas, les opérations pending et le dernier backup éprouvé. Relever en privé les totaux par tenant, commandes/réservations, documents, checkpoints et utilisateurs Keycloak. Conserver les clés d’opération pour la réconciliation. Fermer l’admission, drainer les requêtes et arrêter tous les écrivains, y compris jobs et accès administratifs.
3. Dans la fenêtre approuvée, geler toute autre action Docker, puis utiliser les commandes suivantes. Une capture refusée ou incomplète doit être signalée. Vérifier et rétablir la source même si le stockage distant échoue. Consigner interruption, ID précis du snapshot et résultat de reprise dans le journal privé.

```sh
vault_env=/chemin/prive/vault.env
restic_image=restic/restic:0.18.1@sha256:39d9072fb5651c80d75c7a811612eb60b4c06b32ffe87c2e9f3c7222e1797e76
vault() { docker run --rm --env-file "$vault_env" "$restic_image" --no-cache "$@"; }
# Première fois sur un dépôt distant neuf seulement : vault init
dc run --rm --no-deps qualify check
dc stop proxy bff keycloak
dc stop mongo postgres keycloak-db
capture=0
python3 docker/vps/cold_backup.py "$project" "$cfg" "$vault_env" || capture=$?
# Reprendre même après un échec de capture, après contrôle de l’état.
dc up -d --wait
dc run --rm --no-deps qualify check
sitecheck
test "$capture" -eq 0
vault snapshots
vault check --read-data
```

**Exercice de restauration.** Utiliser un hôte de reprise indépendant avec espace chiffré et Docker dédiés. Sélectionner l’ID complet attendu, vérifier provenance/horodatage, version des images et SHA du kit. Restaurer dans un répertoire vide et jamais sur la source. Une restauration de fichiers réussie précède le test applicatif. [Restauration restic](https://restic.readthedocs.io/en/stable/050_restore.html)

```sh
recovery=$(mktemp -d /chemin/chiffre/reprise.XXXXXXXX)
docker run --rm --env-file "$vault_env" --mount "type=bind,src=$recovery,dst=/out" \
  "$restic_image" --no-cache restore "$snapshot_id" --target /out --verify
snap="$recovery/snapshot"
project=sp029-trial-recovery   # choisir un nom neuf à chaque tentative
bash docker/vps/restore-volumes.sh "$project" "$snap"
cfg="$recovery/settings.env"
install -m 600 "$snap/settings.env" "$cfg"
```

Sur la **copie**, faire pointer `PRIVATE_DIR` vers la copie restaurée de `snapshot/private`. Conserver UID/GID. Utiliser un subnet et pool libres, ajuster `PROXY_IP` et la liste JSON exacte `BFF_TRUSTED_PROXY_IPS` dans `bff.json` de la copie. Garder les FQDN, issuer et sujets d’origine, tourner `BFF_CONFIGURATION_KEY` avec un générateur cryptographique afin d’invalider les sessions restaurées. Vérifier la concordance de la paire TLS, les dates et les digests. La copie récupérée contient des secrets et exige la même protection que la source.

```sh
dr() { docker compose --env-file "$cfg" -p "$project" -f docker/vps/compose.yml -f docker/vps/compose.restore.yml "$@"; }
dr config --quiet
dr up -d --wait
dr run --rm --no-deps qualify check
dr ps
```

L’overlay retire la publication HTTPS et rend tous les réseaux internes. Tester les FQDN et OIDC depuis le réseau edge de reprise, avec confiance TLS normale. Comparer en privé les indicateurs avant/après, clés du realm et claims, identités, schémas, comptes et isolation de deux tenants. Vérifier le journal PG face aux opérations et réservations Mongo avec les clés d’origine. Vérifier sur données synthétiques le refus de rejeu et la disponibilité des conversations/citations. Mesurer la perte temporelle et le temps de reprise. Une dérive suspend la bascule. Les quatre volumes de l’essai restent dédiés à cette tentative.

Pour un incident réel, préserver l’état incident, examiner les écritures depuis le snapshot, approuver le RPO réel puis qualifier cette copie avant toute bascule réseau. Pour un incident applicatif, restaurer l’image précédente uniquement si son contrat valide le schéma courant. Une restauration de données ou une migration descendante exige une décision distincte. [Invariants de reprise](b2b-vm-backup-identity.md)

**Paramètres restant à fournir pour ouvrir la recette.** VPS/IP et accès par clé, CIDR d’administration et de recette, deux FQDN et autorité de renouvellement, accès GHCR en lecture, endpoint privé d’inférence qualifié si activé, stockage hors site et détenteur de la clé de reprise. Aucun transfert depuis la VM n’est nécessaire à cette qualification.
