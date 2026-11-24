# Gilbert & Co : API FastAPI + interface web, dans une seule image.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GILBERT_RUNS_DIR=/data/runs GILBERT_IMPORTS_DIR=/data/imports
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data/runs /data/imports
VOLUME ["/data"]
EXPOSE 8000
# Un seul processus uvicorn : la file d'attente des calculs vit dans ce processus.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
