# Product evolution — organisation et récupération OIDC

Audit du 17 novembre 2026, base GitHub `main` :
`fd7e212` (fusion #91). La branche par défaut reste `dev`, qui n'est pas
intégrée. Les PR #87–91 sont présentes. Le checkout historique est resté intact.
La v0.2.8 déclarée active par l'utilisateur n'a pas été interrogée ni modifiée.

## Diagnostic et décision

Le parcours B2B compose `bff.py` → routes auth → `BffSessionService` →
`OidcHttpProvider` et stores Mongo. Le callback consomme une transaction liée
au navigateur, échange le code avec PKCE, vérifie l'identité puis résout les
appartenances applicatives. Le code d'entreprise n'accorde aucun droit.

Sur la base, une identité valide sans appartenance remonte `BffForbidden` :
HTTP 403 JSON `Application access refused.`, sans retour vers Vue. Une session
antérieure n'est pas révoquée dans cette branche. Le cas est reproduit par les
deux nouveaux tests initialement rouges et par Chromium/Keycloak 26.8.1 réel.
Les annulations Keycloak avaient déjà un retour sûr ; ce travail le conserve.

Choix : distinguer uniquement le refus d'accès après échange OIDC valide par
`BffLoginDenied`. Révoquer la session antérieure attachée à cette transaction,
puis, en mode métier, supprimer les deux cookies applicatifs et répondre 303
vers `/b2b-app/?login=denied`. Aucun identifiant ni détail fournisseur n'est
transporté. Les erreurs de protocole restent 401, les refus API restent 403,
les indisponibilités restent 503. Le mode auth-only garde son contrat 403.

Vue relit la session serveur, retire les paramètres d'URL et présente un message
FR/EN, un champ vide avec focus et deux parcours : choisir une autre organisation,
ou saisir son code puis se connecter avec un autre compte. Le nouveau login
utilise le `prompt=login` **déjà présent** et le sélecteur de compte natif Keycloak.
La préférence d'affichage est conservée. Les autres onglets sont invalidés via
le canal existant ; un document restauré par BFCache relit sa session avant de
réafficher des données métier. Aucune mutation n'est rejouée.

La query `login=denied` est un indice d'interface, jamais une preuve de sécurité.
Elle n'annule pas une session valide vérifiée et n'accorde aucun accès.

Pour un compte déjà connecté, le parcours existant « Changer de compte » révoque
le BFF puis ouvre le logout fournisseur. L'URI post-logout reste fixe et enregistrée,
avec confirmation native du fournisseur lorsqu'il n'y a pas d'ID token hint.
Sources : [Keycloak OIDC](https://www.keycloak.org/securing-apps/oidc-layers),
[administration 26.8.1](https://www.keycloak.org/docs/26.8.1/server_admin/),
[RP-Initiated Logout](https://openid.net/specs/openid-connect-rpinitiated-1_0.html).
Aucun changement de flux global, de version, de realm déployé, de secrets ou
de droits réels. Aucun nouveau schéma, modèle ou prompt ORDER.

## Audit initial des autres chantiers

| Surface / source | Existant conservé | Travail séparé |
| --- | --- | --- |
| Accueil B2B / `B2BApp`, route racine | Catalogue comme accueil, header responsive, rôles et préférences | Intelligence sera une route autorisée dédiée ; pas de faux KPI à l'accueil |
| Organisation / authentification | Code explicite, OIDC/PKCE, session BFF et retour d'annulation | Présent correctif de refus après login |
| Catalogue / produits / comparaison | Filtres, combobox, pagination, prix exacts, fiches, thèmes | Harmonisation tokens, densité, chargement et états vides |
| Stocks / mouvements / imports | Physique, réservé, disponible séparés ; confirmation d'ajustement ; pagination | Lisibilité, signal faible stock explicitement qualifié |
| Commandes / historique | Ownership serveur, pagination, archivage personnel des seules commandes annulées | Actions icônes accessibles sur desktop, libellés mobile, détails structurés |
| Détail de commande | `DetailsPanel` natif dialog, ESC et retour du focus ; SKU/prix/date | Loading/erreur dans le dialogue, dates connues, totaux exacts par devise, événements attestés |
| Assistant / historique | Pending/completed/failed, citations validées, pagination privée et erreurs récupérables | Conserver #87–91 ; harmonisation visuelle sans logique ORDER |
| Administration | Actions produit/import/stock dans les vues selon permissions, backend autoritaire | Pas de nouvelle administration globale ni rôle inventé |
| Aide/contact | Contacts locaux d'organisation et formulaire scoppé | Réutiliser les composants et styles communs |
| Notifications / formulaires / modales | Live regions, confirmations explicites, alertes et dialogue natif | Composants cohérents, focus visible, contrastes et reduced motion |

La bibliothèque d'icônes existante est `components/AppIcon.vue` (SVG local),
les tokens sont dans `b2b/experience.css`, les préférences dans `preferences.ts`
et la traduction dans `translations.ts`. Aucune seconde architecture UI ni
bibliothèque volumineuse n'est nécessaire.

### Disponibilité des données analytiques

`TenantProduct` expose SKU, catégorie, prix de vente en unités mineures, devise,
statut et révision. Le stock embarqué expose `quantity` et `reserved` ; disponible
= physique − réservé pour un produit actif. Les commandes ont exactement les
états `prepared`, `reserved`, `cancelled`, avec dates de création/réservation/
annulation. **Aucune vente réalisée n'est attestée par ces états.**

`TenantStockMovement` enregistre avant/après, réservation, type, namespace, motif,
source et date d'ajustement/import. Le signe du delta ne prouve ni vente ni
réception fournisseur. Les coûts d'achat, fournisseurs, délais, minimums,
réceptions confirmées, ventes/retours, promotions, âge des lots et historique
de ruptures ne sont pas des faits structurés des modèles actuels.

Le lot analytique devra donc séparer stock observé et données commerciales
qualifiées, laisser les indicateurs non calculables explicitement indisponibles
et n'utiliser des séries synthétiques que sur services jetables. Le rôle ADMIN
demandé correspond au rôle existant `ORGANIZATION_ADMIN`, avec `MANAGER` ; aucun
rôle Keycloak ne doit directement conférer une permission analytique.

### Découpage

1. A : récupération Keycloak, présente branche `codex/evolution-auth-ux`.
2. B : design system et interfaces, branche indépendante depuis `main`.
3. C : dictionnaire, modèles additifs et analytics déterministes en lecture seule.
4. D : interface Intelligence sur les contrats C, dépendance explicitée dans sa PR.
5. E : formulations UAT catalogue/stock, correctif séparé de C et sans toucher ORDER.

## Preuves et limites

Tests RED : deux refus métier attendus récupérables renvoient encore 403 avant
correctif. GREEN : 329 tests ciblés BFF/OIDC/renouvellement/tenant passent.
La suite complète avec dépendances verrouillées donne 5 929 pass, 824 deselected,
370 subtests pass ; les désélections incluent les intégrations, comptées séparément.
Un warning HMAC court provient de la fixture préexistante `test_demo_launcher`.
Évaluations déterministes : 122/122. Frontend final : 644 pass,
dont 23 tests de session ; typecheck, lint et build réussis.
Playwright UX : 88/88, dont refus FR/EN dans les deux thèmes sur 390 px.

Une campagne de base sur `fd7e212` a confirmé un échec préexistant du harnais
`b2b-browser.mjs` : sa liste exacte des préférences omettait `assistantSound:false`,
déjà persisté par `preferences.ts`. Le harnais est aligné sur cette préférence
publique précise, en conservant l'égalité exacte et l'interdiction de tokens/
données métier. Ce n'est pas une modification de l'oracle métier ou ORDER.

Captures privées hors Git : baseline dans le worktree d'audit,
`tmp/v1372-nscys819/membership-denied-before.png` ; après dans le worktree auth,
`tmp/v1372-52yh_p5z/membership-denied-after.png` et
`frontend/test-results/auth-denied-{fr,en}-{light,dark}.png`.
L'échec intermédiaire préserve les trois preuves de nettoyage et d'intégrité
des conteneurs/volumes protégés. Les résultats finaux OIDC/intégration et CI
sont consignés après exécution, sans confondre mocks et validation fournisseur.

Le port CI PostgreSQL 55432 est refusé par le moteur hôte. Un override privé
utilise 59928, sur le seul projet tmpfs `stockpilot-evolution-ci-20261117` créé
par cette tâche. Labels, provenance Compose, montages, interfaces loopback et
gardes `integration_targets` sont vérifiés avant les tests. Le script CI à port
fixe n'est pas modifié. Ni base application/DEMO ni configuration Windows modifiée.

Pas de revue indépendante encore réalisée. Aucune qualification de production,
de disponibilité SaaS, ni de révocation instantanée distribuée n'est revendiquée.
Les PR, CI et tests n'autorisent ni fusion ni déploiement.

### Campagne OIDC finale

`B2B_TEST_PORT_SET=isolated B2B_TEST_CERTUTIL=<binaire NSS local de même version>
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/tests/validate_bff_keycloak.py
--login-return` : **38 contrôles réussis**. Rapport privé
`tmp/v1372-fi1yx278/report.json` : `passed`, `cleanup`,
`protected_containers_unchanged` et `volumes_unchanged` vrais.
Chromium réel prouve A→A/B→B, refus A→B/B→A, récupération et sélecteur natif,
PKCE, replay, invalidation, expiration, suspension, CSRF, API mauvais tenant,
permissions, erreurs fournisseur et régressions ORDER/stock/import. Pas de modèle
Ollama sollicité. Capture finale `tmp/v1372-fi1yx278/membership-denied-after.png`.

`--continuity` : **22 contrôles réussis**, rapport
`tmp/v1372-owas85wt/report.json`, mêmes trois attestations d'intégrité/nettoyage.
Vrai logout RP-Initiated, changement d'identité, deux onglets, session expirée,
conversation privée et aucune mutation ORDER rejouée. Deux tentatives précédentes
ont échoué au bind 59914 : une connexion éphémère de la campagne d'intégration
occupait ce port. Aucune ressource étrangère n'a été arrêtée ; la campagne a été
rejouée après les intégrations, sans changement de garde.

Intégrations finales : `-m "integration and not integration_optional" tests`,
**823 pass, 5 860 deselected**, 575,68 s, sur le projet tmpfs vérifié ci-dessus.
Ces tests couvrent notamment Mongo transactionnel, PostgreSQL/RAG/checkpoints,
permissions, pannes, isolation et migrations existantes. Aucun nouveau schéma
n'est nécessaire. `git diff --check` réussi.

GO limité à la soumission de la PR Draft et à sa revue. La CI du SHA poussé est
à vérifier ; revue indépendante et autorisation de livraison restent distinctes.
