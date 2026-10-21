# Espace entreprise StockPilot AI — V13.8.2

Interface de test sur **/b2b-app/**, distincte de l’interface JWT historique.
Elle exige le BFF en mode explicite `b2b_business_test`, un provisioning autorisé,
des migrations explicites sur une base jetable et HTTPS au même origin.
Elle ne constitue pas une installation publique ou une démonstration officielle
promue. [Cible et conditions de promotion](b2b-demo.md).

## Se connecter et retrouver sa session

Saisir le code d’entreprise communiqué par l’administrateur, puis « Continuer
avec Keycloak ». Ce code est une candidature : seul le serveur vérifie l’identité
et le membership. L’utilisateur saisit ses identifiants sur Keycloak, jamais dans
Vue. Le retour du BFF est fixe vers /b2b-app/. Le rechargement relit la session
serveur ; aucun token OIDC n’est enregistré dans le navigateur.

L’en-tête indique le nom d’entreprise vérifié et l’identifiant utilisateur interne.
Le modèle User actuel ne fournit pas de nom humain : l’interface n’en invente pas.
Aucun sélecteur d’organisation n’est proposé sans endpoint serveur de changement.
La session est courte, sans refresh. Un avertissement utilise les dates absolue
et d’inactivité du BFF. La date d’inactivité est un snapshot conservateur ; « Revérifier
la session » relit son état et efface les brouillons affichés, sans mutation.

« Se déconnecter » efface immédiatement les données à l’écran et demande la
révocation serveur. La fermeture de la session SSO Keycloak reste distincte ; le
lien proposé permet de la demander. Si le réseau échoue, l’interface signale que
la révocation n’est pas confirmée. Elle ne recharge pas automatiquement l’ancienne
session. Une 401/403 invalide également l’espace local, même sans corps JSON.
Les lectures métier portent aussi la preuve de session courante : un changement
de cookie dans un autre onglet ne peut pas injecter la réponse d’une autre session
dans une ancienne vue. BroadcastChannel propage la demande de logout aux autres
onglets lorsque le navigateur le permet ; leur contrôle serveur reste obligatoire.

## Catalogue et commandes

Catalogue paginé, SKU exact ou catégorie/disponibilité. Le filtre SKU remplace
les filtres de liste, sans recherche textuelle inventée côté client. Les fiches
montrent les prix reçus et les stocks physique, réservé et disponible. La
disponibilité est instantanée et ne constitue pas une réservation.

Les prix restent des chaînes en unités mineures : calcul entier BigInt, nombre
de décimales issu de la devise via Intl. EUR, JPY et KWD n’ont pas la même échelle.
Une devise inconnue est affichée explicitement en unités mineures sans lui
attribuer deux décimales par défaut.

1. Choisir un acheteur de la liste autorisée, les SKU exacts et quantités entières.
   La liste est bornée à 100 associations ; un provisioning plus large nécessite
   une pagination/recherche dédiée avant un pilote de cette taille.
2. « Préparer la commande » crée une préparation, sans réserver.
3. Lire le récapitulatif aux prix du serveur, cocher la confirmation, puis
   « Confirmer et réserver ». Aucun chargement/rechargement ne confirme.
4. L’historique affiche les commandes autorisées par le BFF :
   own signifie **created_by**, pas customer_id partagé. Le rôle disposant de
   order:read_organization voit explicitement l’historique de son organisation.
5. Une annulation exige une seconde action explicite et attend la réponse serveur.

La préparation est liée à la session d’origine et expire. Après reconnexion,
ne pas appliquer une ancienne préparation comme une nouvelle autorisation.

## Réponse incertaine : vérifier avant de recommencer

Chaque mutation reçoit une clé aléatoire avant son premier envoi. Aucun retry
automatique et aucune nouvelle clé de compensation après timeout, 202 pending
ou 503 OUTCOME_UNKNOWN. La zone « Vérifier avant de recommencer » utilise seulement
le GET de réconciliation avec la clé initiale.

Un résultat durable terminal retire la référence et recharge les vues métier :
les anciens brouillons et confirmations sont invalidés. Un résultat pending reste
en attente. Un 404 ne prouve pas un abort : la référence reste conservée et les
nouvelles mutations sont bloquées. Faire vérifier la référence par un opérateur
autorisé si l’incertitude persiste ; ne pas vider le stockage pour forcer un nouvel
envoi. Une préparation retrouvée par réconciliation n’est pas confirmée automatiquement
depuis l’historique : elle reste sans réservation, et une nouvelle préparation
éventuelle requiert une décision explicite après résolution de la première opération.

Un journal de **références opaques seulement**, borné à 20, utilise sessionStorage
dans cet onglet : user/org/namespace/famille/operation_id. Aucun token, cookie,
CSRF, payload, document, prix, CSV ou commande n’y est persisté. Ce journal non
fiable n’accorde aucun droit ; chaque GET est autorisé côté serveur. Il sert à
retrouver une opération après rechargement/expiration. Il n’est affiché qu’après
restauration de la même identité et organisation ; un autre compte l’efface.
Le logout explicite efface aussi ces références : vérifier ou noter la référence
avant de quitter en cas d’incertitude. Sans accès à sessionStorage, elles restent
en mémoire avec un avertissement, et un rechargement les perd.

## Stock, mouvements et imports

Tous les rôles de consultation peuvent lire le stock et les mouvements autorisés.
Seuls les rôles disposant de stock:adjust voient l’ajustement ; le serveur reste
l’autorité même si une requête est forgée.

- Rechercher le SKU, contrôler physique/réservé/disponible/révision.
- Saisir un nouveau physique absolu et un motif. Physique ne peut être inférieur
  au réservé ; le serveur revalide les réservations et la révision au commit.
- Le journal physique couvre ajustements et imports, **pas toutes les réservations
  ORDER**. Il conserve acteur, motif et valeurs avant/après.

L’import exige import:execute. Choisir un mode : création uniquement,
métadonnées/prix uniquement ou stock physique uniquement. Le formulaire affiche
les en-têtes exacts. CSV UTF-8, virgule/point-virgule, **256 Kio / 100 lignes**.
La taille/encodage est vérifiée côté client ; le parser serveur demeure autoritaire,
notamment pour le nombre de lignes CSV avec champs multilignes.

« Prévisualiser » ne modifie aucun stock. Vérifier fichier, mode, empreinte,
référence, date de validité et diagnostics. Changer le fichier/mode annule le
consentement et invalide toute ancienne réponse de preview. Cocher la confirmation
puis appliquer le lot atomique. Aucune application après reconnexion automatique.
Le statut d’aperçu dans l’historique ne remplace pas le résultat durable d’application.

## Limites actuelles

Pas de gestion de membres ni de changement d’organisation. Pas de token browser,
de proxy vers l’API globale ou de dashboard historique. Les préparations/CSV sont
en mémoire et ne sont pas transférés entre identités. Les droits affichés servent
l’UX ; l’autorisation et l’isolation persistent côté serveur.

**L’assistant conversationnel B2B relève de V13.8.3.** RAG, checkpoints et Ollama ne
sont pas connectés à cette UI. Proxy de déploiement, restauration Mongo/PG,
révocation fournisseur et quotas distribués restent des gates avant Internet.

Contrats : [API B2B](security/b2b-http-api.md).
