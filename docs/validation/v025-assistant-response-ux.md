# Assistant v0.2.5 — attente et fin de réponse

9 novembre 2026. Base main vérifiée :
`f8ab2f5db5e089b182235f64ac8d17586def1c1b`. Branche indépendante
`codex/v025-assistant-response-ux`. Cette PR ne dépend pas des corrections de
grammaire de la branche `codex/v025-assistant-qualification`.

## Parcours livré

- Dès l’envoi : attente indéterminée propre à cette requête, saisie conservée,
  double soumission bloquée. Après dix secondes réelles de cette attente : aide
  neutre. Aucun pourcentage, étape inventée, streaming simulé ni retard ajouté.
- Sur réponse `completed`, autorisée, nouvelle et avec corps vérifié : signal
  discret pendant quatre secondes. Une préparation dit « Préparation à vérifier » ;
  une clarification, un refus ou une recherche vide restent neutres.
- Le dock réduit porte un badge sans contenu métier, effacé à sa consultation.
  Le passage page/dock, changement de conversation, session ou identité nettoie
  les timers, la déduplication et le contexte audio. Les epochs/StaleResponse et
  la génération BFF restent les protections faisant autorité.
- Préférence d’appareil « Son à la fin des réponses de l’assistant », OFF par
  défaut, FR/EN, format v1 compatible avec les anciens objets langue/thème.
  AudioContext déverrouillé par un geste ; une seule tonalité originale, faible,
  190 ms, pour une réponse catalogue non vide. Aucun son ORDER/refus/historique,
  ni fichier tiers, réseau, SDK ou permission de notification OS.
- Après timeout, erreur ou pending : aucun signal de réussite ; reprise/retry
  existants, sans nouveau POST automatique. L’historique déjà terminé est silencieux.
- Région de lecture défilante au clavier, suivi du bas seulement si le lecteur
  s’y trouvait, repère « Nouvelle réponse » autrement. Le message ancien en cours
  de lecture reste présent (borne serveur de 100 tours). Le focus reste en place.
  Une seule région status annonce les changements utiles ; l’historique complet
  n’est plus réannoncé. Les points deviennent statiques avec reduced-motion.

## Recette exécutée

La [matrice des 24 IDs UXP](v025-assistant-ux-matrix.md) conserve les exigences
originales et désigne les preuves Vitest/Chromium correspondantes. Ces 24 états
ne gonflent pas le compteur des 301 questions catalogue.

Commandes depuis le worktree, dépendances installées localement :

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python scripts/run_evals.py --mode deterministic --suite enriched
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test --config=playwright.b2b-ux.config.ts
```

Résultats locaux : **635 tests frontend, 66 scénarios Chromium, 3942 tests Python
et 122 évaluations enrichies réussis**. Les 641 intégrations désélectionnées par
pytest ne sont pas comptées comme réussies ; 370 sous-tests Python et un
avertissement JWT synthétique préexistant sont également signalés. Typecheck,
lint sans avertissement, build et diff-check passent. Les cinq checks CI sur le
SHA exact sont inscrits dans la PR. Tests nouveaux : horloges simulées, route HTTP contrôlée, délai 100 ms/1 s/12 s,
timeout fetch réel à 90 s d’horloge simulée, 401/403, reprise pending/completed,
absence de replay, changement d’identité et réponse tardive, déduplication,
autoplay refusé/API absente, fermeture audio et timers.

Chromium couvre FR/EN, clair/sombre, mobile 390 px et desktop, clavier/focus,
reduced-motion, zoom CSS 200 %, défilement et retour page/dock. Le test de zoom
a exposé un débordement de navigation existant ; le menu peut maintenant défiler
horizontalement lorsqu’il manque de place, y compris sur un grand viewport zoomé.
La préférence sonore est placée après langue/thème pour préserver le premier
arrêt clavier existant. Aucun test historique n’est supprimé ou affaibli.

Captures de recette hors Git : `frontend/test-results/**/waiting-*.png`,
`completed-*.png`, `dock-unread.png`, `assistant-zoom-200.png`. Les captures mobile
attente/fin/badge ont été inspectées visuellement. Les tests mesurent les propriétés
ARIA et le focus ; ils ne constituent pas une audition avec un lecteur d’écran.

## Limites et décision

Le navigateur utilise des réponses synthétiques interceptées. Il ne prouve pas
SSO, Mongo/PG ni une conversation avec Ollama réel. Les tests BFF/persistance
existants restent exécutés par la CI isolée ; Docker local était indisponible.
Le nombre d’appels Web Audio est prouvé ; aucun son physique n’est revendiqué
dans un runner headless. Aucune revue indépendante n’a été effectuée.

Test manuel documenté, **non exécuté** : sur un navigateur réel avec sortie audio,
laisser OFF et envoyer une demande ; activer ON par le menu, envoyer une recherche
non vide et vérifier une seule tonalité discrète ; vérifier silence sur refus,
recherche vide, historique et OFF restauré. Tester avec le lecteur d’écran habituel
que l’attente et la fin sont annoncées une fois, sans relire les cartes.

**Fusion : GO limité à l’UX synthétiquement validée après revue et cinq checks
réussis sur le SHA final.** La preuve audio physique/lecteur d’écran reste une
limite explicite, sans bloquer le retour visuel ou la préférence OFF.

**Release/déploiement : NO-GO.** Aucun tag, release, merge, changement de droits,
migration, déploiement ou écriture sur données DEMO. La qualification complète
de l’assistant et ses écarts sont dans la PR backend distincte.
