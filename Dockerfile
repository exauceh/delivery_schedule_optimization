# Application image: the API and the web interface (no notebook, no test tooling).
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first: this layer is rebuilt only when the lock file changes
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project

COPY src ./src
COPY main.py ./
# files of the project for "Essayer avec un exemple" in the interface (DSO_EXAMPLE_* settings)
COPY docs/released_schedule.csv docs/distances.csv ./docs/
COPY courses/rate_sheet.pdf ./courses/
RUN uv sync --frozen --no-default-groups

ENV PATH="/app/.venv/bin:$PATH" \
    DSO_RUNS_DIR=/data/runs \
    DSO_HOST=0.0.0.0 \
    DSO_PORT=8000

RUN useradd --create-home --uid 1000 app && mkdir -p /data/runs && chown -R app /data
USER app
VOLUME ["/data/runs"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["python", "main.py", "serve"]
