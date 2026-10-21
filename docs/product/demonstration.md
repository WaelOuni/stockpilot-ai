# Démonstration commerciale — 9 minutes

Scénario **historique** (interface JWT), préparé le 19 octobre 2026.
Pour la future cible Keycloak/BFF/Vue, suivre le [guide canonique B2B](../b2b-demo.md),
qui n'est pas encore un parcours opérationnel.

Script à répéter sur une **installation de répétition isolée et autorisée**, avec
données fictives. Ce document n'atteste pas une séance réalisée. Ne pas utiliser
ni modifier les démos stable v017, candidate V13.3 ou historique 5175/8003.


## Avant le rendez-vous : feu vert du présentateur et de l'opérateur

- Noter date, URL et commit **réellement lancé**. La base historique du script est
  `85df5c4` ; la réconciliation documentaire du 21 octobre part de `394bd7c`.
  Aucun de ces SHA ne prouve une installation ou une répétition commerciale.
- L'opérateur vérifie bases isolées, sauvegarde/restauration si nécessaire,
  catalogue, accès client et accès administrateur distincts, puis disponibilité
  du modèle local. Une page de santé seule ne suffit pas. Aucun seed/reset sur
  une installation existante ; aucune donnée prospect pour une découverte.
- Répéter dans un nouveau fil les messages ci-dessous. Relever les références,
  noms, prix et stocks réellement affichés ; adapter le script si les données
  diffèrent. Ne jamais mélanger les valeurs d'une fixture et celles d'une DEMO.
- Exemple de contrôle reproductible : le [catalogue synthétique de tests](../../evals/fixtures/catalogue_advice.py)
  contient D-A à 60/stock 4, D-B à 80/stock 2, D-C à 99/stock 5, D-E à
  100/stock 3 et D-O à 40/stock 0. Sous 100 et disponible : D-A, D-B, D-C.
  **Ce catalogue n'est pas automatiquement celui d'une installation.**
- Choisir un nom complet unique disponible pour la commande fictive. Remplacer
  `NOM_COMPLET_VÉRIFIÉ` ci-dessous avant la séance ; ne pas saisir ce marqueur.
  Relever le stock initial S ; prévoir une seule unité et conserver son identifiant.
- Préparer accès et onglets avant partage d'écran ; masquer jetons, mots de passe,
  logs, données privées et panneau d'accès. La connexion actuelle est technique,
  pas un compte client commercial prêt à l'emploi.
- Préparer hors Git une capture ou courte vidéo **issue d'une répétition autorisée**,
  datée, avec commit et données fictives, sans secret. Ce support reste à produire :
  aucune capture nouvelle n'est livrée par ce chantier documentaire.

Si le catalogue, les accès ou les faits ne sont pas vérifiés, présenter seulement
la [fiche produit](fiche-produit.md) et mener [l'entretien](validation-commerciale.md).
Aucun diagnostic, redémarrage ou renouvellement d'accès sur les DEMO.

## Déroulé à garder sous les yeux

Ce script décrit la sémantique du parcours historique : stock affiché S−1 après
réservation. Le modèle B2B interne conserve le physique et augmente `reserved` ;
son futur scénario est défini dans le guide canonique, sans assimilation des deux.

Les attentes portent sur la **nouvelle réponse** ; les anciennes cartes restent
des instantanés, pas une disponibilité actualisée. Les durées incluent l'attente.

| Temps | Action / message exact | Résultat attendu et phrase à dire |
|---|---|---|
| 0:00–0:45 | Ouvrir Catalogue, montrer la référence choisie | « Nous cherchons une référence dans un catalogue fictif. Les prix et stocks viennent du catalogue ; nous ne démontrons ni paiement ni connexion ERP. » |
| 0:45–2:00 | Nouveau fil : **« Rechercher trois perceuses disponibles sous 100 »** | Jusqu'à trois perceuses, chacune sous 100 strictement et stock positif ; moins si peu de correspondances, aucune si aucune. Pour la fixture ci-dessus : D-A, D-B, D-C, pas D-E ni D-O. « Un conseil ne réserve aucun stock. » Ne pas ajouter de devise absente du modèle. |
| 2:00–3:00 | **« Et si je monte le budget à 150 euros ? »**, puis **« Montre-moi seulement deux produits. »** | Plafond remplacé par 150 inclusif, disponibilité conservée, puis deux résultats maximum. Les moins chers peuvent rester identiques. La mention « euros » est une formulation comprise, pas une devise garantie par le catalogue. |
| 3:00–4:15 | Nouveau fil : **« Conseille-moi sur le SKU D-C »**, puis **« Compare cette perceuse avec deux autres disponibles. »** | Si D-C est le SKU prévalidé : une fiche ciblée puis référence + jusqu'à deux alternatives, écarts de prix/stock vérifiés. Adapter le SKU avant la séance. « Aucune puissance ou performance technique n'est déduite. » |
| 4:15–5:15 | Nouveau fil : **« Quel est le stock de NOM_COMPLET_VÉRIFIÉ ? »**, puis **« Commande-moi 1 NOM_COMPLET_VÉRIFIÉ »** | Stock lu puis proposition avec produit et quantité 1 ; aucune réservation à ce stade. Relire les valeurs avec le prospect. Si nom ambigu ou quantité refusée, arrêter et clarifier, sans prétendre à un succès. |
| 5:15–6:15 | Cliquer une seule fois **Confirmer**, ouvrir **Mes commandes** | Attendre le résultat définitif, noter identifiant et statut. Sur cette copie sans autre activité, stock S−1 après confirmation. « La confirmation déclenche la réservation. » Un échec ou une réponse incertaine n'est pas une commande à recommencer. |
| 6:15–7:30 | Espace administrateur : catalogue, tableau de bord, page Import CSV ; montrer l'exemple sans importer | Données serveur ; accès distinct. L'import crée de nouveaux produits à stock zéro, après aperçu/confirmation. Pas de synchronisation Excel/ERP ni de mise à jour massive des références existantes. |
| 7:30–9:00 | Revenir à la fiche produit et demander : « Quelle tâche voudriez-vous comparer à votre méthode actuelle ? » | Annoncer les limites, proposer un entretien puis un pilote isolé de 2–4 semaines, soumis à accord. Aucun gain ou prix annoncé comme acquis. |

Option de fin si la séance reste sous 10 minutes : dans un nouveau fil,
**« Conseille-moi sur le SKU INTROUVABLE-DEMO »**, après vérification de son absence :
zéro fiche, aucune substitution inventée. Ne pas allonger la séance pour finir
toutes les étapes ; garder au moins une minute pour la question métier.

Après la séance, sur la seule copie de répétition autorisée : annuler uniquement
la commande identifiée si son statut le permet ; vérifier CANCELLED et stock S.
Conserver les traces, sans reset. Ne rien annuler sur une installation protégée.

## Plan de secours : basculer en moins d'une minute

| Incident | Action du présentateur | Ce qu'on peut encore montrer |
|---|---|---|
| Réponse trop lente pour le créneau | Attendre l'issue sans répéter une commande ; au-delà de 30 secondes de présentation, passer au support préparé | Capture/vidéo datée, annoncée comme une répétition antérieure ; latence non garantie |
| Modèle indisponible | Dire « Le modèle local est indisponible » ; si une réponse déterministe apparaît, l'identifier comme mode dégradé | Catalogue vérifié ou support préparé ; jamais présenter ce repli comme Ollama testé en direct |
| Base/catalogue indisponible ou faits incohérents | Suspendre conseil et commandes ; conserver l'erreur sans log privé à l'écran | Fiche produit et entretien ; aucun prix/stock supposé |
| Accès expiré ou session perdue | Masquer l'écran privé ; laisser l'opérateur gérer l'accès sur la copie autorisée, sinon basculer | Support préparé ; un refresh peut effacer accès et historique visuel |
| Résultat incertain après confirmation | Noter l'identifiant connu et laisser l'opérateur rapprocher l'état serveur ; aucune nouvelle préparation/confirmation | Expliquer la limite et poursuivre l'entretien, sans annoncer succès ni échec métier définitif |

Fiche de répétition/séance à remplir : date, opérateur, commit lancé, catalogue,
modèle, étapes réellement exécutées, durée, identifiant fictif de commande, stock
avant/après, incident, support utilisé et décision de poursuivre. Garder secrets et
identifiants personnels hors des documents partagés.

Preuves techniques : [tests navigateur synthétiques](../../frontend/advice-synthetic-e2e/catalogue-advice.spec.ts),
[recherches](../../frontend/advice-synthetic-e2e/search-language.spec.ts),
[diagnostic réel du 19 octobre](../validation/2026-10-19-environment-language-diagnostic.md).
Ces preuves ne remplacent pas la répétition sur le commit choisi.
