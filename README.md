# ReRoute.AI

ReRoute.AI is a full-stack application for managing trips, reacting to disruptions, and exploring rebooking options with an AI-assisted workflow. The backend orchestrates flight search and booking through **Duffel** (test mode by default), keeps trip state in a database, and only applies changes after explicit user confirmation—so proposals and tool output stay separate from committed itinerary updates.

The stack is a **FastAPI** service, a **Next.js** web app, optional **Celery** workers backed by **Redis**, and **PostgreSQL** or **SQLite** for persistence.

## Repository layout

| Path | Purpose |
|------|---------|
| `reroute-ai/backend/` | REST API, WebSocket routes, integrations, agent graph (optional LangGraph extra), Celery tasks |
| `reroute-ai/frontend/` | Next.js UI (dashboard, trips, monitor, auth, settings) |
| `reroute-ai/docker-compose.yml` | Local **Postgres** (port 5433) and **Redis** (6379) |
| `reroute-ai/docs/` | Deeper design notes (e.g. agent context and actions) |

## Prerequisites

- **Python** 3.11+
- **Node.js** 20+ (matches the frontend toolchain)
- **Docker** (optional): only if you want Compose-managed Postgres and Redis instead of SQLite + ad hoc Redis

## Configuration

Settings are read from environment variables and, when present, from `.env` files. The backend loads files in order of increasing precedence: repository root → `reroute-ai/.env` → `reroute-ai/backend/.env`. Variables in the process environment override file values.

You will typically set at least:

| Variable | Role |
|----------|------|
| `JWT_SECRET_KEY` | Signing key for access/refresh tokens (use a long random value outside local dev) |
| `DATABASE_URL` | Default is SQLite under `reroute-ai/backend/`; for Postgres use something like `postgresql+asyncpg://reroute:reroute_dev@localhost:5433/reroute` to match `docker-compose.yml` |
| `CORS_ORIGINS` | Comma-separated origins allowed for the API (default includes `http://localhost:3000`) |
| `DUFFEL_API_KEY` | Flight offers and orders via Duffel; without it, relevant flows degrade or use mocks depending on code paths |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | Google sign-in; omit if you only use email/password |
| `REDIS_URL` | Celery broker/result (defaults to `redis://localhost:6379/0`) |
| `OPENAI_API_KEY` | Used when the LangChain/LangGraph agent extras are installed and enabled |

Additional toggles and timeouts (email via Resend, Open-Meteo, AviationStack, OpenRouteService, monitor cadence, etc.) are documented in `reroute-ai/backend/config.py`.

## Run the backend

From `reroute-ai/backend/`:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Optional agent stack (LangChain / LangGraph):

```bash
pip install -e ".[dev,agent]"
```

Start the API:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Health check: `GET http://localhost:8000/api/health`

For **Postgres** with Compose (avoids clashing with a local server on 5432, Postgres listens on **5433**):

```bash
docker compose -f reroute-ai/docker-compose.yml up -d postgres
```

Point `DATABASE_URL` at that instance and, if you use Alembic-only mode in production-style setups, align `database_use_alembic_only` with your migration workflow.

## Run the frontend

From `reroute-ai/frontend/`:

```bash
npm install
npm run dev
```

The dev server defaults to port 3000 and expects the API on port 8000 unless you configure otherwise.

## Deploy to production

See **[reroute-ai/docs/DEPLOY.md](reroute-ai/docs/DEPLOY.md)** for Docker Compose and general hosting. For **Vercel + Render + SQLite**, see **[reroute-ai/docs/DEPLOY_VERCEL_RENDER.md](reroute-ai/docs/DEPLOY_VERCEL_RENDER.md)**. Quick start with Docker:

```bash
cd reroute-ai
cp .env.production.example .env.production
# edit secrets and URLs
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## Background workers (optional)

Email queueing, monitor cycles, long-running agent tasks, and related jobs expect **Redis** and a Celery worker.

Start Redis:

```bash
docker compose -f reroute-ai/docker-compose.yml up -d redis
```

From `reroute-ai/backend/` with the virtualenv active:

```bash
celery -A worker.celery_app worker -l info
```

Optional **beat** scheduler (monitor cycle and stale-proposal cleanup are scheduled in `worker/celery_app.py`):

```bash
celery -A worker.celery_app beat -l info
```

## API surface (overview)

Routers are mounted under `/api`, including health, authentication (including Google OAuth callbacks), users and sessions, trips, chat, disruptions, monitoring, a public surface, WebSockets, and agent **propose** / **confirm** flows. For how proposals, tools, and apply steps fit together, see `reroute-ai/docs/AGENT_ARCHITECTURE.md`.

## License

No license file is included in this repository; add one if you intend to distribute or accept contributions.
