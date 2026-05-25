# ReRoute.AI API — production image (Postgres + Alembic on startup when configured)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY main.py config.py api_router.py deps.py database.py ./
COPY routers ./routers
COPY service ./service
COPY dao ./dao
COPY schema ./schema
COPY model ./model
COPY integrations ./integrations
COPY agent ./agent
COPY utils ./utils
COPY worker ./worker
COPY alembic.ini ./
COPY alembic ./alembic

# dev extra: alembic + psycopg for migrations; agent extra omitted in default prod image
RUN pip install --upgrade pip \
    && pip install -e ".[dev]"

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
