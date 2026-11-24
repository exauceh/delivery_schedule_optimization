"""Entry point of the application.

    uv run main.py run --schedule docs/released_schedule.csv --distances docs/distances.csv --rate-sheet docs/rate_sheet.csv
    uv run main.py serve
"""

from delivery_schedule_optimization.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
