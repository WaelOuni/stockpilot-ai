# Démonstration commerciale de 8 minutes

Scénario historique. Pour la future cible Keycloak/BFF/Vue, suivre le
[guide canonique B2B](../b2b-demo.md), qui n'est pas encore un parcours opérationnel.

Script préparé le 17 octobre 2026. C'est une procédure à exécuter avant chaque
présentation, pas une nouvelle preuve de validation. Utiliser uniquement la
candidate synthétique autorisée, jamais la DEMO stable v017.

## Préparation du présentateur

Vérifier URL, commit d'application réellement lancé, identité des deux bases,
services et modèles. Pour la candidate V13.3 prévue : `https://localhost:5193` ;
ce certificat local peut nécessiter une acceptation explicite pour localhost.
Cette adresse n'est pas un accès public à transmettre à un prospect distant.
Consulter le runbook de l'installation ; ne pas recréer ou réinitialiser ses
données pour préparer une séance. Ne pas utiliser les scripts historiques de seed
qui effacent une base.

Préparer un navigateur de présentation sans données privées, un JWT de démonstration
valide émis par l'opérateur et une session administrateur distincte. Ne montrer ni
jeton, mot de passe, fichier d'environnement, log brut ni terminal de génération.
Le client utilise actuellement un panneau de jeton ; l'expliquer comme une limite
du prototype, pas comme une connexion utilisateur commerciale aboutie.

Dans un thread neuf, contrôler les produits synthétiques D-A, D-B, D-C et le stock
courant de D-C. Les prix de la fixture V13.3 sont 29,90 ; 39,90 ; 49,90, mais le
présentateur doit relire les valeurs actuelles. Le stock change après une commande.
Prévoir un seul achat fictif de quantité 1 et noter son identifiant afin de pouvoir
l'annuler selon le contrat ; aucune remise à zéro du catalogue.

## Déroulé

| Temps | Action | Message à faire comprendre / contrôle |
|---|---|---|
| 0:00–0:45 | Présenter le problème et ouvrir Catalogue | Données synthétiques ; SKU, prix, stock ; pas de caractéristiques techniques déduites |
| 0:45–1:45 | Assistant : « Je cherche une perceuse disponible à moins de 100 euros. » | Jusqu'à trois cartes ; chaque prix `< 100`, stock positif ; tri déterministe ; aucune réservation |
| 1:45–2:30 | « Et si je monte le budget à 150 euros ? », puis « Montre-moi seulement deux produits. » | Budget remplacé, catégorie/disponibilité conservées ; deux cartes. Un top des moins chères peut rester identique malgré le budget accru |
| 2:30–3:00 | « Et parmi ceux-là, lequel est le moins cher ? » | Comparaison des produits présentés, pas d'une sélection inventée |
| 3:00–4:00 | Nouveau thread : « Peux-tu me conseiller sur la Perceuse Concurrence Synthétique V017 ? », puis « Compare cette perceuse avec deux autres. » | Une fiche ciblée puis référence + deux alternatives maximum ; écart de prix, stock, disponibilité ; pas de puissance inventée |
| 4:00–4:30 | « Compare cette perceuse avec deux autres. » après une liste de plusieurs références | Une référence non univoque doit provoquer une clarification ; le SKU ou « le premier » permet de préciser |
| 4:30–5:45 | Nouveau thread : demander le stock du nom exact, puis « Commande-moi 1 Perceuse Concurrence Synthétique V017 » | Lire le récapitulatif : produit, quantité. Expliquer que le stock n'est pas encore réservé ; vérifier dans un autre onglet |
| 5:45–6:30 | Cliquer une seule fois « Confirmer », ouvrir Mes commandes et le détail | Réservation uniquement après résultat confirmé ; identifiant et statut persistés ; aucune facture ou paiement |
| 6:30–7:30 | Espace administrateur séparé : tableau de bord, commandes et catalogue | Session opérateur distincte ; l'état vient du serveur. Ajustements de stock exigent motif et confirmation |
| 7:30–8:00 | Montrer la page Import CSV et son exemple téléchargeable, sans exécuter d'import | Aperçu ≠ création ; stock initial zéro. Conclure par les limites et le problème à mesurer avec le prospect |

Après la séance, annuler uniquement la commande synthétique identifiée si elle est
encore dans un statut annulable. Vérifier son statut CANCELLED et la restitution
exacte du stock ; ne pas envoyer plusieurs commandes pour « réessayer » un résultat
incertain. Conserver les reçus et l'historique, sans effacer les données.

## Contrôles et plan de secours

- Un produit inexistant doit donner zéro résultat, une référence ambiguë une
  clarification. Tester ces deux cas avant la séance si le temps le permet.
- Une recommandation n'est jamais une commande ; une préparation n'est jamais une
  réservation. L'interface et le discours doivent maintenir cette distinction.
- En cas de lenteur, attendre l'état final ; ne pas soumettre la même mutation
  sous un nouvel identifiant. Montrer le catalogue et le statut serveur si besoin.
- En cas de panne LLM, annoncer le mode dégradé déterministe s'il apparaît. Une
  réponse de secours ne prouve pas une exécution Ollama réelle.
- Une panne de catalogue interdit toute démonstration de faits supposés. Suspendre
  le scénario concerné, conserver l'erreur contrôlée et expliquer l'incident.
- Rafraîchir le navigateur peut perdre le JWT client et l'historique affiché en
  mémoire. Les checkpoints serveur ne constituent pas une liste d'historique UI.
- Pour un besoin technique (« la plus puissante »), annoncer l'absence de données
  techniques plutôt que conclure à une supériorité.

Versionner une fiche de séance sans secrets : date, commit, étapes réellement
exécutées, temps total, incident et résultat. Les temps et résultats des validations
techniques antérieures ne doivent pas être présentés comme des gains client.
