# Gilbert & Co

Planification et consolidation des livraisons fournisseurs. Le moteur (dossier `fusion/`) regroupe les besoins de chaque couple fournisseur × usine en moins de camions, sur des jours fixes, sans retard et avec au plus 7 jours d'avance. Les livraisons peuvent commencer 7 jours avant le premier besoin (le 02/11 pour le planning du 09/11). Gilbert & Co l'habille d'une **API FastAPI** et d'une **interface web**, pour que chacun puisse lancer un calcul sans ligne de commande.

Le parcours dans l'interface :

1. **Nouveau calcul.** On dépose le planning de livraison et le fichier des distances (la grille tarifaire est facultative), puis on clique : l'import et l'optimisation s'enchaînent. Deux menus repliés, « Réglages détaillés » et « Données manquantes », permettent de changer les réglages par défaut. L'historique des imports permet de relancer un calcul sur un planning déjà importé. « Essayer avec un exemple » utilise les fichiers du projet.
2. **Résultats**, en moins d'une minute. La synthèse montre les économies, les camions et les objectifs, puis les indicateurs de la situation actuelle face au plan Gilbert. On y voit aussi le planning actuel et la grille tarifaire. Viennent ensuite, repliés, les données manquantes (avec relance en un clic), les règles appliquées, le registre des anomalies et les contrôles. Les onglets Planning, Profils, Camions, Fournisseurs et Scénarios détaillent le reste.

Aucune manipulation technique n'est nécessaire : double-clic sur le lanceur, dépôt de deux fichiers, un clic.

> **Jalon 5.** Code en modules et point d'entrée `main.py`, suite de tests `tests/`, ce README, lanceurs à double-cliquer et `Dockerfile` avec `docker-compose.yml`. Pour le tableau de bord, l'équipe a choisi une interface web servie par FastAPI plutôt que Streamlit : elle se lance de la même façon, d'un double-clic.

## Ouvrir Gilbert & Co sur son ordinateur

Prérequis : Python 3.11 ou plus récent.

| Système | Action |
| :--- | :--- |
| Windows | Double-clic sur `Lancer_Gilbert.bat` |
| macOS | Double-clic sur `Lancer_Gilbert.command` (la première fois : clic droit, puis Ouvrir) |
| Ligne de commande | `pip install -r requirements.txt`, puis `python main.py` (le navigateur s'ouvre sur http://localhost:8000) |
| Docker | `docker compose up --build`, puis ouvrir http://localhost:8000 |

## Le flux principal sans interface

`main.py` est le point d'entrée unique. `python main.py run` enchaîne les quatre étapes sur l'exemple du projet ; on peut aussi lui donner ses propres fichiers :

```
python main.py run --planning mon_planning.csv --distances distances.csv --sortie sorties --poids median
```

1. Préparation des données brutes (règles 1 à 9, valeurs manquantes) : `fusion/data.py`
2. Registre des anomalies et situation actuelle : `api/situation.py`
3. Optimisation (plans par couple, lissage, chargement exact) : `fusion/engine.py`
4. Sorties CSV et Excel, contrôle de chaque règle sur le plan final : `fusion/report.py`

## Tests

```
pip install -r requirements-dev.txt
python -m pytest              # 16 tests, environ 40 s
python -m pytest -m "not slow"   # sans le calcul complet, quelques secondes
```

| Fichier | Ce qui est vérifié |
| :--- | :--- |
| `tests/test_data.py` | prix d'exemple de la grille, mêmes 4 389 lignes que le Milestone 1, aucun manquant après traitement, retrait exact, départ le 02/11 |
| `tests/test_situation.py` | situation actuelle du Milestone 2 (1 665 livraisons, 1 741 camions, 176 724,61 €), registre des anomalies, règles affichées, besoin un dimanche bloquant |
| `tests/test_api.py` | fichiers refusés, paramètres contrôlés, et le scénario de validation : un nouvel import mène à un résultat qui atteint les trois cibles, toutes règles respectées, fichiers téléchargeables |

## Organisation

```
main.py             point d'entrée : ouvre l'application, ou exécute le flux complet en ligne de commande (run)
api/main.py         API FastAPI : imports, situation actuelle, calculs, résultats, téléchargements
api/situation.py    contrôle d'un import : registre des anomalies, règles appliquées, coût actuel, grille, calendrier
api/results.py      lecture des sorties du moteur et préparation des données affichées
engine_runner.py    lance le moteur dans un processus séparé (indispensable pour le calcul parallèle)
fusion/             le moteur de consolidation (voir ENGINE.md) ; ajouts : traitement au choix des poids, volumes
                    et distances manquants (camion plein, maximum, médiane, retrait), départ 7 jours avant le 1er besoin
web/                l'interface : index.html, styles.css, app.js (sans compilation, graphiques SVG faits main)
tests/              suite pytest : données, situation actuelle, parcours complet par l'API
Dockerfile, docker-compose.yml   image unique (API + interface), données dans le volume /data
web/brand/          le logo Gilbert & Co : SVG (lettres vectorisées) et PNG à fond transparent
web/fonts/          polices Plus Jakarta Sans et Manrope, hébergées avec l'application (licence SIL OFL)
docs/               fichiers de l'exemple du projet (planning, distances) et grille tarifaire par défaut
data/               besoins nettoyés du Milestone 1 (référence, non utilisés par l'interface)
imports/            un dossier par import : les trois fichiers et import.json (créé au premier import)
runs/               un dossier par calcul : réglages, sorties, journal (créé au premier calcul)
```

L'interface n'effectue aucun calcul. Elle envoie les réglages à l'API, qui écrit `reglages.json` et place le calcul dans une file d'attente. `engine_runner.py` exécute alors le moteur, qui écrit ses sorties et `resume.json`. L'interface interroge l'API chaque seconde, puis affiche les résultats.

## L'API

La documentation interactive est disponible sur `/docs` (Swagger).

| Méthode | Route | Rôle |
| :--- | :--- | :--- |
| POST | `/api/imports` | Importer. Formulaire : `schedule` et `distances` (obligatoires), `rates` (facultatif), `label`, ou `example=true` pour l'exemple du projet |
| GET | `/api/imports` | Tous les imports : période, lignes, fournisseurs, usines, nombre de calculs |
| GET | `/api/imports/{id}/situation` | Situation actuelle. Paramètres `weight`, `volume` (`full_truck`, `max`, `median`, `drop`) et `distance` (`max`, `median`, `drop`) |
| DELETE | `/api/imports/{id}` | Supprimer un import |
| POST | `/api/runs` | Lancer une optimisation sur un import. Formulaire : `settings` (JSON) |
| GET | `/api/runs` | Tous les calculs, avec leurs indicateurs |
| GET | `/api/runs/{id}` | État d'un calcul : `en_attente`, `en_cours`, `termine`, `erreur` ou `interrompu` |
| GET | `/api/runs/{id}/summary` | Indicateurs, objectifs, contrôles, calendrier, profils, lissage |
| GET | `/api/runs/{id}/situation` | Situation actuelle enregistrée avec le calcul : indicateurs, anomalies, règles, grille, calendrier |
| GET | `/api/runs/{id}/trucks` | Liste des camions, filtres `plant`, `supplier`, `day`, pagination `limit` et `offset` |
| GET | `/api/runs/{id}/trucks/{camion}` | Contenu d'un camion |
| GET | `/api/runs/{id}/profiles` | Jours de livraison par fournisseur, filtres `plant` et `q` |
| GET | `/api/runs/{id}/suppliers` | Économies par fournisseur |
| GET | `/api/runs/{id}/files/{nom}` | `resultats_consolidation.xlsx`, `sortie1_profils.csv`, `sortie2_camions.csv`, `sortie3_saving.csv` ou `gilbert_resultats.zip` |
| DELETE | `/api/runs/{id}` | Supprimer un calcul terminé |

Les réglages de `settings` :

| Réglage | Valeur | Défaut |
| :--- | :--- | :--- |
| `label` | nom du scénario | `"Scénario"` |
| `import_id` | l'import à optimiser | obligatoire |
| `anticipation_days` | 0 à 21 | 7 |
| `deliver_before_first_need` | livrer dès 7 jours avant le premier besoin | `true` |
| `smoothing_max_extra_cost` | surcoût accepté par usine pour le lissage, de 0 à 0,05 | 0 |
| `max_frequency` | livraisons par semaine au plus, de 1 à 5 | 5 |
| `missing_weight`, `missing_volume` | `full_truck`, `max`, `median` ou `drop` | `full_truck` |
| `missing_distance` | `max`, `median` ou `drop` | `max` |

Exemple : `curl -F example=true http://localhost:8000/api/imports`, puis `curl -F 'settings={"label":"Lissage 1 %","import_id":"<id>","smoothing_max_extra_cost":0.01}' http://localhost:8000/api/runs`

## Déployer

L'application est un **seul service** : l'API sert aussi l'interface.

```
docker build -t gilbert-co .
docker run -p 8000:8000 -v gilbert-data:/data gilbert-co
```

Ce fonctionnement convient à toute plateforme qui exécute un conteneur (Render, Railway, Fly.io, Azure App Service, Google Cloud Run, etc.), à quatre conditions :
- **Une seule instance et un seul processus uvicorn.** La file d'attente des calculs vit dans le processus. Le moteur utilise déjà tous les cœurs, donc on fait grandir la machine plutôt que le nombre d'instances.
- **Un disque persistant monté sur `/data`.** Sinon l'historique des calculs disparaît à chaque redémarrage.
- **Au moins 2 processeurs et 2 Go de mémoire.** Un calcul prend alors environ 30 secondes.
- **La plateforme fournit `PORT`.** L'image l'utilise automatiquement.

Variables d'environnement :

| Variable | Rôle | Défaut |
| :--- | :--- | :--- |
| `GILBERT_RUNS_DIR` | dossier des calculs | `/data/runs` dans l'image, `runs/` en local |
| `GILBERT_IMPORTS_DIR` | dossier des imports | à côté du dossier des calculs : `/data/imports`, `imports/` en local |
| `GILBERT_RUN_TIMEOUT_S` | durée maximale d'un calcul | 900 |
| `GILBERT_MAX_UPLOAD_MB` | taille maximale d'un fichier déposé | 50 |
| `PORT` | port d'écoute | 8000 |

## Avant une mise en ligne publique

- **Authentification.** L'API n'en a pas : n'importe qui ayant l'adresse peut importer, lancer et supprimer des calculs. Le plus simple est de la placer derrière l'authentification de la plateforme ou d'un proxy. Sinon, il faut ajouter une dépendance FastAPI qui vérifie un jeton.


## Identité visuelle

| Élément | Valeur |
| :--- | :--- |
| Logo | `web/brand/gilbert-logo.svg` (horizontal), `gilbert-logo-stacked.svg` (empilé), `gilbert-logo-inverse.svg` (sur fond sombre), `gilbert-mark.svg` (icône). Chaque fichier existe aussi en PNG. |
| Symbole | un « G » vert tracé comme une route : il rassemble les petits points (les besoins) en un seul point orange, la livraison consolidée |
| Principe | fond crème, cartes blanches arrondies, deux couleurs franches qui ont chacune un rôle, mouvements courts (barres qui poussent, chiffres qui défilent, onglets qui glissent, itinéraire animé dans le menu) |
| Fond | crème `#FAF6F0`, cartes `#FFFFFF`, en-têtes `#F4EEE5`, filets `#ECE4D8` ; texte `#14302A` |
| Vert émeraude | `#0F5B46` → `#1FA77A` : structure (menu, bandeau des économies, écran de calcul), situation actuelle dans les graphiques, rampe d'intensité |
| Orange | `#E8772E` : action (boutons principaux, menu actif), résultat « avec Gilbert », jours de pointe |
| Rouge | `#C0392B` : réservé aux règles non respectées et aux erreurs |
| Polices | Plus Jakarta Sans (titres et chiffres), Manrope (texte). Le logo est dessiné à partir de Fraunces, mais ses lettres sont vectorisées : il ne dépend d'aucune police. |

Les couleurs sont des variables CSS en tête de `web/styles.css`. Les couleurs des graphiques sont dans la constante `C` en tête de `web/app.js`.
