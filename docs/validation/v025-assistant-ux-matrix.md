# Matrice UX v0.2.5 — 24 états

Les IDs UXP restent distincts des 301 demandes du corpus assistant. Scénarios synthétiques uniquement, sans backend, base ni SSO. Les appels Web Audio sont observés ; aucune émission sonore physique n’est revendiquée.

| ID | Exigence originale | Statut automatisé | Preuve |
|---|---|---|---|
| UXP-01 | état idle, aucun spinner ou badge de fin intempestif. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-02 | clic Envoyer → feedback visuel immédiat, sans duplicata de message. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-03 | réponse serveur très rapide → aucun scintillement gênant, aucun retard artificiel. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-04 | réponse retardée >10s → indication honnête de traitement long ; pas de % fictif. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-05 | texte + produits `completed` → résultat visible, indicateur retiré, signal « Réponse prête » unique. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-06 | résultat `NO_RESULTS` → message exact / état vide, sans prétendre trouver un produit. | PASS | Vitest `assistant-feedback.test.ts` |
| UXP-07 | `CLARIFICATION_REQUIRED` et `ORDER_REFUSED` → rendu neutre/refus, pas de son de réussite. | PASS | Vitest `assistant-feedback.test.ts` |
| UXP-08 | `ORDER_PREPARE` → état « préparation à vérifier », pas « commande confirmée/réservée ». | PASS | Vitest `assistant-feedback.test.ts` |
| UXP-09 | attente avec `timeoutMs=90000` / panne réseau → statut incertain géré, texte utilisateur conservé. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-10 | `pending` récupéré puis terminé → une notification au plus, pas de double POST. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-11 | reprise / retry contrôlé et réconciliation idempotente → aucune double notification. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-12 | consultation du seul historique / pages anciennes → aucun son ni toast nouveau. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-13 | panneau réduit pendant une réponse → badge de nouvelle réponse puis effacement à la consultation. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-14 | passage assistant dock ↔ page complète → aucune fuite ou notification de réponse obsolète. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-15 | changement d'organisation ou utilisateur pendant le traitement → annuler/ignorer tout feedback appartenant à l'ancienne identité. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` ; `assistant.test.ts` (remplacement d’identité/logout pendant la réponse) |
| UXP-16 | logout ou session expirée pendant l'attente → aucun son / badge / donnée d'un ancien utilisateur. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` ; `assistant.test.ts` (remplacement d’identité/logout pendant la réponse) |
| UXP-17 | préférence son OFF par défaut ; réponse terminée → 0 audio mais feedback visuel présent. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-18 | préférence son ON via geste → une courte notification sonore originale au plus. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-19 | Web Audio API indisponible/autoplay refusé → aucun crash, notification visuelle toujours présente. | PASS | Vitest `assistant-feedback.test.ts` |
| UXP-20 | préférence conservée sur l'appareil sans casser langue/thème existants ; OFF réversible. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-21 | FR/EN et clair/sombre, responsive mobile, notification non bloquante, boutons cliquables. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-22 | lecteur d'écran/ARIA → annonce concise une fois ; `prefers-reduced-motion` sans animation excessive. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-23 | utilisateur lit un ancien message plus haut → pas d'auto-scroll agressif ; repère nouveau résultat. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
| UXP-24 | deux événements HTTP tardifs ou séquences de réponses → déduplication par tour, absence de toast/son inter-tenant. | PASS | Vitest `assistant-feedback.test.ts` ; Chromium `assistant-progress.spec.ts` |
