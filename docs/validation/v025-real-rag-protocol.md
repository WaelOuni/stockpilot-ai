# Qualification Ollama réelle v0.2.5

Le module opt-in `evals.assistant_v025.real_rag` exécute les messages originaux
G15-01 à G15-10 trois fois, avec les seeds 25051, 25052 et 25053. Il utilise le
graphe assistant et `TenantRetrieval` de production, la vraie classification et
génération Ollama et les vrais embeddings. Aucune configuration applicative,
base métier, donnée VM ou fichier privé d'environnement n'est chargé.

```sh
PYTHONDONTWRITEBYTECODE=1 LOG_FILE= .venv/bin/python -m evals.assistant_v025.real_rag \
  --url http://127.0.0.1:11587 --isolated-synthetic-only \
  --windows-curl --output /tmp/stockpilot-final-real-rag.json \
  --permutations-output /tmp/stockpilot-final-citation-permutations.json
```

`--windows-curl` est seulement un transport WSL vers une instance Windows propre
sur loopback. Il appelle le curl Windows sans interpréter les prompts dans un
shell. Omettre cette option pour un serveur loopback dans le même environnement.
L'instance doit appartenir au test, utiliser un port non standard et uniquement
une copie isolée de modèles déjà disponibles. Le harnais ne démarre, ne modifie
et ne supprime aucun serveur. `--case` sert uniquement aux essais exploratoires;
une qualification complète omet cette option.

La mesure utilise qwen2.5:7b pour classification/génération et
nomic-embed-text:latest pour les embeddings. Le rapport relève les digests réels,
version Ollama, limites, seeds, latences, requêtes originales, sorties, citations,
sources consultées et assertions. Température génération 0,2, classification 0,
sortie bornée à 256 tokens, contexte 4096, délai par appel 120 secondes. Le SHA
et la propreté sont contrôlés avant et après la campagne; un résultat obtenu
dans un arbre modifié ou dont le SHA change n'est pas une preuve finale.

Le corpus documentaire est entièrement synthétique : procédure de stock,
préparation sans réservation, confirmation explicite, livraison sept jours,
garantie vingt-quatre mois, retours quatorze jours pour produit inutilisé,
ancien prix documentaire et leurres non autorisés. Son hash figure au rapport.
G15-05 utilise un corpus autorisé vide. G15-06 a comme préalable explicite les
questions G15-03, G15-01 et G15-04, respectivement pour les trois seeds, puis
demande la source de cette réponse. Le préalable est lui-même vérifié et la
source attendue (livraison, stocks, préparation) figure par essai au rapport :
retrouver par hasard une source fixe ne suffit pas. G15-08 a comme préalable
une recherche du SKU DEMO-01-0225, puis exige le prix courant du catalogue.
Les demandes et attentes originales restent inchangées.

Le suivi G15-06 ne relance désormais ni classifieur, ni génération, ni embedding,
ni recherche sémantique. Le harnais conserve les tours effectivement produits
par le graphe dans un journal synthétique privé, avec identités de tours
distinctes. Il exige que les références structurées rendues correspondent
exactement aux documents et passages réellement cités par le modèle dans le
tour précédent, ainsi qu'à son `source_turn_id`. Les labels locaux S1/S2 ne
servent jamais d'identifiants persistants. Chaque référence contient une
génération synthétique stable ; sa lecture exacte est revalidée avant restitution.
Le journal synthétique vérifie conversation, propriétaire, organisation et
session courante ; cela ne remplace pas les preuves PostgreSQL de persistance.

L'option `--permutations-output` produit un fichier **séparé** : six ordres des
trois sujets (livraison, stock, réservation), soit dix-huit suivis, puis neuf
scénarios supplémentaires : plusieurs sources citées, retour au catalogue,
suppression, retrait, autre conversation, autre utilisateur, autre organisation,
changement de session et replay après suppression. Chaque préalable documentaire
est une véritable génération Ollama validée, jamais une réponse injectée.
Les seeds historiques sont distribuées de façon déterministe entre ces quinze
scénarios. Les documents consultés mais non cités sont exclus par l'égalité
exacte avec les références du préalable. Les essais supplémentaires n'augmentent
pas le nombre de demandes du corpus original et ne remplacent aucun de ses
trente essais. Tout échec est conservé avec son contexte et son résultat.

Les vérifications des réponses initiales séparent les citations générées du pied de page de provenance.
Ce dernier prouve les documents consultés, pas que chaque phrase est étayée.
Une citation doit identifier un document qui contient le fait demandé; ses
formes `[S2]`, `[source: S2]` ou « document S2 » sont sémantiquement équivalentes.
Un label seul ne constitue pas une réponse utile. Les faits critiques, le prix
catalogue, l'abstention sans extrait, l'absence de mutation et les leurres sont
vérifiés. Une relecture des sorties reste nécessaire pour détecter d'autres
affirmations non étayées; cette mesure finie ne prouve pas l'absence de toute
hallucination.

Le repository mémoire n'est pas une preuve d'autorisation PostgreSQL. Son leurre
privé utilise une ACL du harnais uniquement; la production utilise organisation
et permission RAG_READ, sans ACL fine par document/utilisateur. Les tests
`test_assistant_v025_final_rag_integration.py` complètent chaque ID par une preuve
directe PG/Mongo jetable de portée, documents retirés et révocation des droits.
G15-08 possède aussi un test de priorité du catalogue. Ces preuves ne remplacent
pas la qualité du modèle réel, et réciproquement. Les fixtures exigent les URI
de services jetables et refusent les cibles applicatives ou DEMO.

Les essais exploratoires ont permis de découvrir des réponses réduites aux
citations, des sources historiques mal attribuées et une confusion entre
préparation et prédiction. Leurs résultats ne sont pas des preuves sur la HEAD
finale. Les assertions ne doivent pas être assouplies pour masquer ces défauts;
un échec réellement observé reste FAIL et une indisponibilité réelle du modèle
reste une preuve absente, jamais un PASS simulé.
