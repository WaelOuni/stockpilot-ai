# PR préparée : Industrialiser CI, packaging Docker et releases privées

Base : dev. Branche : feat/ci-cd-release. PR de livraison V10.

La CI existante ne validait que Python. Cette modification ajoute les gates Vue,
les tests transactionnels Mongo/checkpoints PostgreSQL sur services éphémères,
et les builds/smoke Docker. Les images API/web fonctionnent sous utilisateurs
non-root et le proxy /api se configure au runtime.

Les releases exigent un tag SemVer annoté appartenant à main et relancent les
gates sur son commit. La publication est désactivée par défaut, soumise à un
environnement protégé ; les images privées GHCR ont des tags version/SHA,
leurs digests sont joints à une GitHub Release draft. Un dry-run ne publie rien.

Voir docs/ci-cd.md et docs/archive/v10-validation.md pour preuves, prérequis et limites.
Playwright reste explicitement désactivé : son harness dépend de vrai Ollama.

La revue finale retire tout transfert d’images via artefact Actions du dépôt public,
vérifie l’existence d’un environnement release avec reviewers sans auto-approbation,
et refuse les erreurs de registre avant publication. La confidentialité GHCR doit
être configurée et maintenue manuellement ; le YAML en vérifie la visibilité sans
la modifier. Aucun environnement ni règle GitHub n’est créé par cette PR.
