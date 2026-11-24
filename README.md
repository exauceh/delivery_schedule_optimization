# Delivery schedule consolidation

Backend of the delivery consolidation project (ESIGELEC, Data & AI project). From the released schedule of the
plants, the engine chooses for every supplier × plant couple a **delivery profile** (fixed delivery days), groups
the needs into **trucks**, spreads the deliveries over the week for each plant and **quantifies the savings**
against the current situation, without ever delivering late or overloading a truck.

On the November 2026 planning: 1 741 → 760 trucks, 176 725 € → 110 544 € over 6 weeks (saving 37.4 %), every
business rule checked, in about 25 seconds.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 itself).

```bash
uv sync                                   # environment: application + tests + notebooks

# One run from the command line (no server)
uv run main.py run --schedule docs/released_schedule.csv --distances docs/distances.csv --rate-sheet docs/rate_sheet.csv

# The API (interactive documentation on http://127.0.0.1:8000/docs)
uv run main.py serve
```

With Docker (API on port 8000, runs kept in a volume):

```bash
docker compose up --build
```

On first use, `.env` is created from `.env.example`; every setting has a default and no secret is needed.

## Inputs and outputs

A run always takes the same three CSV files; the application stores no reference data.

| Input | Content |
| :--- | :--- |
| `released_schedule.csv` | One row per need: `date`, `supplier_id`, `material`, `plant`, `unloading_point`, `schedule_quantity`, `weight`, `volume` |
| `distances.csv` | `supplier_id`, `distance` (km) |
| `rate_sheet.csv` | `km_up_to` and one column per weight bracket (price of one truck before margin and diesel surcharge) |

Each run writes in `runs/<run_id>/output/`:

| File | Content |
| :--- | :--- |
| `sortie1_profils.csv` | **Output 1**: profile of each couple (days, weekly pattern before / after) and the reason of the choice |
| `sortie2_contenu_camions.csv` | **Output 2**: materials grouped in each truck, by dock, with the need days they cover |
| `sortie2_camions.csv`, `sortie2_affectations.csv` | Output 2 in detail: one row per truck, one row per need (truck, delivery day, advance, rule applied) |
| `sortie3_saving.csv` | **Output 3**: deliveries, trucks and cost before / after, saving per couple |
| `consolidated_dataset.csv`, `anomaly_register.csv` | Prepared data and the anomalies found (DQ01 to DQ07) |
| `situation_actuelle_livraisons.csv` | Current situation: one delivery per couple and need day, with its trucks and cost |
| `controles.csv` | Every business rule (RG01 to RG11) checked on the final plan |

`runs/<run_id>/manifest.json` records the SHA-256 of the inputs, every parameter used (defaults included), the
duration of each stage, the main indicators and the result of the checks: the same inputs and parameters always
give the same results.

## API

| Method | Route | Purpose |
| :--- | :--- | :--- |
| `GET` | `/health` | Service status |
| `GET` | `/config/defaults` | Every parameter of a run with its default value |
| `POST` | `/runs` | Upload the three files (+ optional `parameters` JSON) and launch the run in the background → `202` with the `run_id` |
| `GET` | `/runs`, `/runs/{run_id}` | List of the runs, status and results of one run |
| `GET` | `/runs/{run_id}/log` | Log of the run |
| `GET` | `/runs/{run_id}/tables`, `/runs/{run_id}/tables/{name}` | Output tables as JSON, paginated (for the front end) |
| `GET` | `/runs/{run_id}/files/{name}`, `/runs/{run_id}/download` | One table as CSV, or every output in a zip |

Files with a missing column or an unknown parameter are refused at once (`422`). Runs execute one at a time in a
separate process (the engine already uses every CPU core); a run left unfinished by a restart is marked failed.

## Parameters

Business parameters (`src/delivery_schedule_optimization/domain/config.py`) can be overridden per run, in JSON:

```bash
uv run main.py parameters > parameters.json      # every parameter with its default value
uv run main.py run ... --parameters parameters.json
```

```json
{"planner": {"smoothing_budget": 0.005, "max_advance_days": 7}, "preparation": {"tail_date": null}}
```

The main ones: `max_advance_days` (RG03), `allowed_frequencies` (RG05), `smoothing` and `smoothing_budget` (RG09),
`objective` (`cost` or `trucks`), `strict_coverage` (RG06), `holiday_country` (RG10) and `tail_date` (cumulative last
date of the planning, detected automatically). Technical settings (folder of the runs, log level, number of worker
processes, host and port) come from the environment with the prefix `DSO_` (see `.env.example`).

## Architecture

```
main.py                          entry point: run | serve | parameters
src/delivery_schedule_optimization/
├── domain/        business objects, no I/O: parameters (config.py), calendar (RG10), plan of a couple (models.py)
├── io/            contracts of the input and output files, reading (with checks) and writing
├── preparation/   Milestone 1 cleaning: DQ01–DQ07, full-truck imputation, consolidated dataset, engine lines
├── pricing/       rate sheet and price of one truck (RG11)
├── baseline/      current situation (reference A): one delivery per couple and need day, loaded like the plan
├── engine/        optimisation (Milestone 3)
│   ├── loading.py      whole lines, first fit decreasing (RG04)
│   ├── graph.py        exact plan of a couple on a set of days: shortest path over the departure days
│   ├── reassign.py     local search on the assignment of the needs (RG06 relaxed)
│   ├── candidates.py   one plan per set of weekdays (31) and per couple, in parallel
│   ├── selection.py    choice of the profile (RG07)
│   └── smoothing.py    spreading of the deliveries per plant (RG09, integer programme, HiGHS)
├── reporting/     outputs 1, 2, 3 and indicators
├── validation/    checks of the business rules on the final plan (to be completed by milestone 4)
├── pipeline.py    main flow: preparation → current situation → plans → outputs → checks (no file, no API)
├── service.py     executes a stored run: inputs → pipeline → outputs → manifest
├── storage/       runs on disk (runs/<run_id>/); a database can be added behind the same interface
├── api/           FastAPI application, background execution, routes
├── cli.py         command line
├── settings.py    technical settings (DSO_*)
└── planner.py     former single module, re-exports the engine for the milestone notebooks
tests/
├── unit/          calendar, loading, pricing, preparation, inputs and parameters
├── integration/   pipeline on a small planning, API, command line
└── regression/    the real planning gives exactly the outputs of the milestone 3 notebook (marked slow)
milestone1/ … milestone3/   notebooks of the milestones (analysis, reports)
```

The engine and the business objects know nothing of files, of the API or of the storage: the command line, the
API and the tests all go through `pipeline.run_pipeline`. A front end (Streamlit or React) will only call the API.

## Development

```bash
uv run ruff check src tests main.py     # lint
uv run mypy                             # strict typing
uv run pytest -m "not slow"             # fast tests (a few seconds)
uv run pytest -m slow                   # non-regression on the real planning (about 30 s to 2 min)
```

The CI (`.github/workflows/ci.yml`) runs the same checks, the non-regression test and the Docker build.
