FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    IMPORT_DIR=/imports \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY .env.example README.md ./

RUN useradd --create-home --shell /bin/bash appuser \
    && mkdir -p /data /imports \
    && chown -R appuser:appuser /app /data /imports

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
