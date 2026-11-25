# Consolidation du planning de livraisons

Application **Gilbert & Co** du projet de consolidation des livraisons : une interface web et l'API qui la sert. À partir du planning publié par les
usines, le moteur choisit pour chaque couple fournisseur × usine un **profil de livraison** (des jours de livraison
fixes), regroupe les besoins dans des **camions**, répartit les livraisons sur la semaine pour chaque usine et
**chiffre les économies** par rapport à la situation actuelle, sans jamais livrer en retard ni surcharger un camion.


## Démarrage rapide

Prérequis : [uv](https://docs.astral.sh/uv/) (il installe lui-même Python 3.14).

```bash
uv sync                                   # environnement : application + tests + notebooks

# Une exécution en ligne de commande (sans serveur)
uv run main.py run --schedule docs/released_schedule.csv --distances docs/distances.csv --rate-sheet courses/rate_sheet.pdf

# L'application : interface web sur http://127.0.0.1:8000, API sous /api (documentation interactive sur /docs)
uv run main.py serve
```

Avec Docker (interface et API sur le port 8000, exécutions conservées dans un volume) :

```bash
docker compose up --build
```

Au premier lancement, `.env` est créé à partir de `.env.example` ; chaque réglage a une valeur par défaut et aucun
secret n'est nécessaire.

## Entrées et sorties

Une exécution prend toujours les trois mêmes fichiers ; l'application ne stocke aucune donnée de référence.

| Entrée | Contenu |
| :--- | :--- |
| `released_schedule.csv` | Une ligne par besoin : `date`, `supplier_id`, `material`, `plant`, `unloading_point`, `schedule_quantity`, `weight`, `volume` |
| `distances.csv` | `supplier_id`, `distance` (km) |
| `rate_sheet.pdf` | La grille du transporteur, telle qu'il l'envoie (PDF) : prix par tranche de distance et de poids, marge, surcharge diesel, date de validité |

La marge et la surcharge diesel ne sont pas des paramètres : elles viennent toujours du PDF, avec la grille, et le
manifeste de l'exécution les enregistre. Une grille dans un autre format (CSV) est refusée.

### Comment la grille est extraite du PDF

Le code est dans `src/delivery_schedule_optimization/io/rate_sheet_pdf.py`.

1. **Lecture des mots avec leur position.** La première page est lue avec `pdfplumber`, qui donne chaque mot avec
   ses coordonnées sur la page. Le texte brut n'est pas utilisé pour la grille : dans ce PDF, il donne une ligne de
   prix sans distance (7,33 … 73,04) et une étiquette `>650` sans prix, et une lecture ligne par ligne décalerait
   tous les prix d'une tranche de distance à partir de 100 km.
2. **Regroupement en lignes.** Les mots dont le haut est à moins de 2 points d'écart forment une même ligne.
3. **En-tête.** La ligne qui contient `km` et `kg` est l'en-tête. Les tranches de poids sont les mots à droite de
   `kg` (`50` … `4000`, `>4000`) ; la position horizontale de chacune définit une colonne.
4. **Lignes de prix.** Chaque ligne suivante qui commence, à gauche de la première colonne, par une étiquette de
   distance (`30` … `650`, `>650`) est une ligne de la grille. Chacun de ses prix est rangé dans la colonne dont le
   centre est le plus proche. La lecture est refusée si un prix est loin de toute colonne, si une colonne reçoit deux
   prix ou si une ligne a un prix manquant. La grille s'arrête à la première ligne qui n'a plus d'étiquette.
5. **Termes du contrat.** Dans le texte de la page : « Freight value margin: 239.23% » donne la marge (2,3923),
   « Diesel surcharge: 1.1943 » la surcharge, « Valid from: 01.08.2026 » la date de validité. Les nombres sont lus à
   l'anglaise, comme tout le PDF : « 1,000 » vaut mille.
6. **Contrôle par l'exemple du PDF.** Le PDF imprime un exemple de calcul (300 km, 1 000 kg : coût 82,77 €, prix final
   335,34 €). L'application relit ce coût dans la grille extraite, avec la même règle « jusqu'à » que le moteur, puis
   recalcule 82,77 × (1 + 2,3923) × 1,1943. Si l'un des deux résultats ne correspond pas, le fichier est refusé avec un
   message clair. Une étiquette décalée ou une marge mal lue est ainsi détectée avant tout calcul.

Résultat sur `courses/rate_sheet.pdf` : 14 tranches de distance × 15 tranches de poids. Les 210 prix sont identiques
à la grille saisie à la main au Milestone 1 (`docs/rate_sheet.csv`) et à ceux qu'extrait un autre outil
(`pdftotext`). Les tests le vérifient (`tests/unit/test_rate_sheet_pdf.py`).

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
| `tests_sorties.csv` | Les suites de tests du Milestone 4 rejouées sur les sorties : contraintes recalculées depuis les lignes, données manquantes (H05, H06), exhaustivité, absence de rupture, cohérence du saving |
| `situation_actuelle_camions.csv` | Situation actuelle camion par camion (poids, remplissage, prix) : la grille tarifaire de l'interface |
| `lissage_usines.csv`, `lissage_couples.csv` | Effet du lissage : pic et coût de chaque usine, profil de chaque couple, avant et après lissage |
| `resultats_consolidation.xlsx` | Rapport Excel : une feuille de synthèse, puis les sorties 1, 2 et 3 au format du sujet (colonnes et en-têtes des diapos 10 à 12 de `courses/consolidation_algorithme_pdf.pdf`, puis le détail), le lissage, les anomalies, les contrôles et les tests |

`runs/<run_id>/manifest.json` enregistre l'empreinte SHA-256 des entrées, tous les paramètres utilisés (valeurs par
défaut comprises), la durée de chaque étape, les principaux indicateurs et le résultat des contrôles et des tests de
sortie (`checks_passed`, faux si un seul échoue) : les mêmes entrées et les mêmes paramètres donnent toujours les mêmes
résultats.

## Interface web

L'interface de Wissam (dossier `src/delivery_schedule_optimization/web/` : HTML, CSS et JavaScript sans compilation,
graphiques SVG faits main, logo et polices hébergés avec l'application) est servie par FastAPI sur `/`. Elle ne calcule
rien : tous ses chiffres viennent des tables de sortie d'une exécution, par les routes `/api/runs/{run_id}/views/...`
(`reporting/dashboard.py`).

- **Nouveau calcul** : on dépose les trois fichiers d'un coup (reconnus automatiquement), on nomme le scénario et on
  ajuste les réglages détaillés (avance maximale, surcoût accepté pour lisser), puis on
  lance. « Essayer avec un exemple » utilise les fichiers du projet. Les calculs précédents se rouvrent, se relancent sur
  les mêmes fichiers avec d'autres réglages, ou se suppriment.
- **Synthèse** : économie, coût, camions, livraisons et remplissage avant / après, charge quotidienne des usines,
  conformité, puis en détail le coût par usine, le lissage, la situation actuelle sur la grille tarifaire, les règles
  appliquées aux données, le registre des anomalies, les contrôles, les tests de sortie et le journal du calcul.
  « Exporter le rapport » donne le rapport Excel, les sorties en CSV ou le zip de toutes les tables.
- **Planning, Profils, Camions, Fournisseurs** : le détail des sorties 1 à 3, avec filtres et le bon de livraison de
  chaque camion.
- **Scénarios** : tous les calculs côte à côte (économie, camions, pics par usine, réglages, durée).

Un lissage avec surcoût est plus long que le lissage gratuit : environ 50 secondes pour 0,5 % sur le planning du projet,
contre une dizaine de secondes (les usines sont lissées en parallèle, l'usine la plus lente fixe la durée). L'interface
le signale quand on choisit ce réglage.

## API

Toutes les routes sont sous `/api`.

| Méthode | Route | Rôle |
| :--- | :--- | :--- |
| `GET` | `/api/health` | État du service |
| `GET` | `/api/config/defaults` | Tous les paramètres d'une exécution avec leur valeur par défaut |
| `POST` | `/api/runs` | Importer les trois fichiers (+ `label` et un JSON `parameters` facultatifs) et lancer l'exécution en arrière-plan → `202` avec le `run_id` |
| `POST` | `/api/runs/example` | Même chose avec les fichiers du projet (`DSO_EXAMPLE_*`) |
| `POST` | `/api/runs/{run_id}/rerun` | Nouvelle exécution sur les fichiers de `run_id`, avec d'autres paramètres (`source_run_id` dans le manifeste) |
| `GET` | `/api/runs`, `/api/runs/{run_id}` | Liste des exécutions, statut et résultats d'une exécution |
| `DELETE` | `/api/runs/{run_id}` | Supprimer une exécution terminée et ses fichiers |
| `GET` | `/api/runs/{run_id}/log` | Journal de l'exécution |
| `GET` | `/api/runs/{run_id}/tables`, `/api/runs/{run_id}/tables/{name}` | Tables de sortie en JSON, paginées |
| `GET` | `/api/runs/{run_id}/files/{name}`, `/api/runs/{run_id}/download` | Une table en CSV, ou toutes les sorties dans un zip |
| `GET` | `/api/runs/{run_id}/excel` | Le rapport Excel |
| `GET` | `/api/runs/{run_id}/views/summary`, `situation`, `trucks`, `trucks/{truck_id}`, `profiles`, `suppliers` | Les données des écrans de l'interface |

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
`overflow_first` (ajouter des jours plutôt que des camions), `strict_coverage` (RG06), `holiday_country` (RG10) et `tail_date` (dernière date
du planning qui cumule les besoins, détectée automatiquement). La marge et la surcharge diesel (RG11) n'en font pas
partie : elles sont lues dans la grille du transporteur. Le plan commence toujours `max_advance_days` avant
le premier besoin (réponse client) : les premiers besoins peuvent partir en avance comme les autres. Les réglages techniques (dossier des exécutions,
niveau de log, nombre de processus, hôte et port) viennent de l'environnement avec le préfixe `DSO_` (voir
`.env.example`).

## Architecture

```
main.py                          point d'entrée : run | serve | parameters
src/delivery_schedule_optimization/
├── domain/        objets métier, sans I/O : paramètres (config.py), calendrier (RG10), plan d'un couple (models.py)
├── io/            contrats des fichiers d'entrée et de sortie, lecture (avec contrôles, PDF de la grille) et écriture
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
├── reporting/     sorties 1, 2, 3 (au format du sujet pour l'Excel : brief.py), lissage, indicateurs, synthèse Excel et données des écrans (dashboard.py)
├── validation/    contrôles des règles métier (checks.py), tests du Milestone 4 sur les sorties d'une exécution
│                  (output_tests.py) et cas unitaires du moteur (engine_cases.py)
├── pipeline.py    flux principal : préparation → situation actuelle → plans → sorties → contrôles (ni fichier, ni API)
├── service.py     exécute une exécution enregistrée : entrées → pipeline → sorties → manifeste
├── storage/       exécutions sur disque (runs/<run_id>/) ; une base de données peut s'ajouter derrière la même interface
├── api/           application FastAPI, exécution en arrière-plan, routes (dont views.py pour l'interface)
├── web/           interface Gilbert & Co : index.html, styles.css, app.js, logo (brand/) et polices (fonts/)
├── cli.py         ligne de commande
├── settings.py    réglages techniques (DSO_*)
└── planner.py     ancien module unique, réexporte le moteur pour les notebooks des milestones
tests/
├── unit/          calendrier, chargement, tarification, préparation, entrées et paramètres, cas du moteur (Milestone 4)
├── integration/   pipeline sur un petit planning, tests de sortie (et sorties cassées exprès), API, routes de
│                  l'interface (écrans, exemple, relance, suppression, Excel), ligne de commande
└── regression/    le vrai planning donne exactement les sorties du notebook milestone 3 (marqué slow)
milestone1/ … milestone4/   notebooks des milestones (analyses, rapports ; milestone4 : tests et analyse avant / après)
```

Le moteur et les objets métier ne connaissent ni les fichiers, ni l'API, ni le stockage : la ligne de commande,
l'API et les tests passent tous par `pipeline.run_pipeline`. L'interface web ne parle qu'à l'API.

Le fonctionnement de l'optimiseur (graphe des jours de départ, recherche locale, lissage par usine) est expliqué
pas à pas, avec des liens vers le code, dans [`milestone3/optimiseur.md`](milestone3/optimiseur.md).

## Développement

```bash
uv run ruff check src tests main.py     # lint
uv run mypy                             # typage strict
uv run pytest -m "not slow"             # tests rapides (quelques secondes)
uv run pytest -m slow                   # non-régression sur le vrai planning et recherche locale (de 30 s à 2 min)
```

La CI (`.github/workflows/ci.yml`) lance les mêmes contrôles, le test de non-régression et la construction de
l'image Docker.
