# Consolidation du planning de livraisons

Backend du projet de consolidation des livraisons (ESIGELEC, projet Data & IA). À partir du planning publié par les
usines, le moteur choisit pour chaque couple fournisseur × usine un **profil de livraison** (des jours de livraison
fixes), regroupe les besoins dans des **camions**, répartit les livraisons sur la semaine pour chaque usine et
**chiffre les économies** par rapport à la situation actuelle, sans jamais livrer en retard ni surcharger un camion.

Sur le planning de novembre 2026 : 1 741 → 760 camions, 176 725 € → 110 544 € sur 6 semaines (saving de 37,4 %),
toutes les règles métier vérifiées, en 25 secondes environ.

## Démarrage rapide

Prérequis : [uv](https://docs.astral.sh/uv/) (il installe lui-même Python 3.14).

```bash
uv sync                                   # environnement : application + tests + notebooks

# Une exécution en ligne de commande (sans serveur)
uv run main.py run --schedule docs/released_schedule.csv --distances docs/distances.csv --rate-sheet docs/rate_sheet.csv

# L'API (documentation interactive sur http://127.0.0.1:8000/docs)
uv run main.py serve
```

Avec Docker (API sur le port 8000, exécutions conservées dans un volume) :

```bash
docker compose up --build
```

Au premier lancement, `.env` est créé à partir de `.env.example` ; chaque réglage a une valeur par défaut et aucun
secret n'est nécessaire.

## Entrées et sorties

Une exécution prend toujours les trois mêmes fichiers CSV ; l'application ne stocke aucune donnée de référence.

| Entrée | Contenu |
| :--- | :--- |
| `released_schedule.csv` | Une ligne par besoin : `date`, `supplier_id`, `material`, `plant`, `unloading_point`, `schedule_quantity`, `weight`, `volume` |
| `distances.csv` | `supplier_id`, `distance` (km) |
| `rate_sheet.csv` | `km_up_to` et une colonne par tranche de poids (prix d'un camion avant marge et surcharge diesel) |

Chaque exécution écrit dans `runs/<run_id>/output/` :

| Fichier | Contenu |
| :--- | :--- |
| `sortie1_profils.csv` | **Sortie 1** : profil de chaque couple (jours, rythme hebdomadaire avant / après) et la raison du choix |
| `sortie2_contenu_camions.csv` | **Sortie 2** : matériels regroupés dans chaque camion, par quai, avec les jours de besoin couverts |
| `sortie2_camions.csv`, `sortie2_affectations.csv` | Sortie 2 en détail : une ligne par camion, une ligne par besoin (camion, jour de livraison, avance, règle appliquée) |
| `sortie3_saving.csv` | **Sortie 3** : livraisons, camions et coût avant / après, saving par couple |
| `consolidated_dataset.csv`, `anomaly_register.csv` | Données préparées et anomalies détectées (DQ01 à DQ07) |
| `situation_actuelle_livraisons.csv` | Situation actuelle : une livraison par couple et par jour de besoin, avec ses camions et son coût |
| `controles.csv` | Chaque règle métier (RG01 à RG11) vérifiée sur le plan final |

`runs/<run_id>/manifest.json` enregistre l'empreinte SHA-256 des entrées, tous les paramètres utilisés (valeurs par
défaut comprises), la durée de chaque étape, les principaux indicateurs et le résultat des contrôles : les mêmes
entrées et les mêmes paramètres donnent toujours les mêmes résultats.

## API

| Méthode | Route | Rôle |
| :--- | :--- | :--- |
| `GET` | `/health` | État du service |
| `GET` | `/config/defaults` | Tous les paramètres d'une exécution avec leur valeur par défaut |
| `POST` | `/runs` | Importer les trois fichiers (+ un JSON `parameters` facultatif) et lancer l'exécution en arrière-plan → `202` avec le `run_id` |
| `GET` | `/runs`, `/runs/{run_id}` | Liste des exécutions, statut et résultats d'une exécution |
| `GET` | `/runs/{run_id}/log` | Journal de l'exécution |
| `GET` | `/runs/{run_id}/tables`, `/runs/{run_id}/tables/{name}` | Tables de sortie en JSON, paginées (pour le front) |
| `GET` | `/runs/{run_id}/files/{name}`, `/runs/{run_id}/download` | Une table en CSV, ou toutes les sorties dans un zip |

Un fichier auquel il manque une colonne, ou un paramètre inconnu, est refusé immédiatement (`422`). Les exécutions
passent une par une, chacune dans un processus séparé (le moteur utilise déjà tous les cœurs) ; une exécution
interrompue par un redémarrage est marquée en échec.

## Paramètres

Les paramètres métier (`src/delivery_schedule_optimization/domain/config.py`) peuvent être modifiés pour chaque
exécution, en JSON :

```bash
uv run main.py parameters > parameters.json      # tous les paramètres avec leur valeur par défaut
uv run main.py run ... --parameters parameters.json
```

```json
{"planner": {"smoothing_budget": 0.005, "max_advance_days": 7}, "preparation": {"tail_date": null}}
```

Les principaux : `max_advance_days` (RG03), `allowed_frequencies` (RG05), `smoothing` et `smoothing_budget` (RG09),
`objective` (`cost` ou `trucks`), `strict_coverage` (RG06), `holiday_country` (RG10) et `tail_date` (dernière date
du planning qui cumule les besoins, détectée automatiquement). Les réglages techniques (dossier des exécutions,
niveau de log, nombre de processus, hôte et port) viennent de l'environnement avec le préfixe `DSO_` (voir
`.env.example`).

## Architecture

```
main.py                          point d'entrée : run | serve | parameters
src/delivery_schedule_optimization/
├── domain/        objets métier, sans I/O : paramètres (config.py), calendrier (RG10), plan d'un couple (models.py)
├── io/            contrats des fichiers d'entrée et de sortie, lecture (avec contrôles) et écriture
├── preparation/   nettoyage du Milestone 1 : DQ01–DQ07, imputation camion plein, dataset consolidé, lignes du moteur
├── pricing/       grille tarifaire et prix d'un camion (RG11)
├── baseline/      situation actuelle (référence A) : une livraison par couple et par jour de besoin, chargée comme le plan
├── engine/        optimisation (Milestone 3)
│   ├── loading.py      lignes entières, first fit decreasing (RG04)
│   ├── graph.py        plan exact d'un couple sur un ensemble de jours : plus court chemin sur les jours de départ
│   ├── reassign.py     recherche locale sur l'affectation des besoins (RG06 assouplie)
│   ├── candidates.py   un plan par ensemble de jours de la semaine (31) et par couple, en parallèle
│   ├── selection.py    choix du profil (RG07)
│   └── smoothing.py    répartition des livraisons par usine (RG09, programme linéaire en nombres entiers, HiGHS)
├── reporting/     sorties 1, 2, 3 et indicateurs
├── validation/    contrôles des règles métier sur le plan final (à compléter par le jalon 4)
├── pipeline.py    flux principal : préparation → situation actuelle → plans → sorties → contrôles (ni fichier, ni API)
├── service.py     exécute une exécution enregistrée : entrées → pipeline → sorties → manifeste
├── storage/       exécutions sur disque (runs/<run_id>/) ; une base de données peut s'ajouter derrière la même interface
├── api/           application FastAPI, exécution en arrière-plan, routes
├── cli.py         ligne de commande
├── settings.py    réglages techniques (DSO_*)
└── planner.py     ancien module unique, réexporte le moteur pour les notebooks des milestones
tests/
├── unit/          calendrier, chargement, tarification, préparation, entrées et paramètres
├── integration/   pipeline sur un petit planning, API, ligne de commande
└── regression/    le vrai planning donne exactement les sorties du notebook milestone 3 (marqué slow)
milestone1/ … milestone3/   notebooks des milestones (analyses, rapports)
```

Le moteur et les objets métier ne connaissent ni les fichiers, ni l'API, ni le stockage : la ligne de commande,
l'API et les tests passent tous par `pipeline.run_pipeline`. Un front (Streamlit ou React) n'aura qu'à appeler l'API.

## Développement

```bash
uv run ruff check src tests main.py     # lint
uv run mypy                             # typage strict
uv run pytest -m "not slow"             # tests rapides (quelques secondes)
uv run pytest -m slow                   # non-régression sur le vrai planning (de 30 s à 2 min)
```

La CI (`.github/workflows/ci.yml`) lance les mêmes contrôles, le test de non-régression et la construction de
l'image Docker.
