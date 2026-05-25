# Deploying ReRoute.AI

ReRoute.AI is a **FastAPI** backend, **Next.js** frontend, **PostgreSQL**, **Redis**, and optional **Celery** worker/beat. Choose one of the paths below.

## Prerequisites

- Docker and Docker Compose (recommended for a single-server deploy)
- Or separate hosts: API + worker on a Python host, frontend on Vercel/similar, managed Postgres + Redis

Generate a strong `JWT_SECRET_KEY` (32+ random bytes). Never commit `.env.production` or real API keys.

---

## Option A — Docker Compose (full stack)

Best for VPS, Railway Docker, Fly.io, or local production smoke tests.

### 1. Configure environment

From `reroute-ai/`:

```bash
cp .env.production.example .env.production
```

Edit `.env.production`:

| Variable | Notes |
|----------|--------|
| `POSTGRES_PASSWORD` | Required; used by Compose for Postgres and `DATABASE_URL` |
| `JWT_SECRET_KEY` | Required; not the default from `config.py` |
| `FRONTEND_URL` | Public URL of the Next.js app (e.g. `https://app.example.com`) |
| `CORS_ORIGINS` | Same origin(s) as the frontend, comma-separated |
| `NEXT_PUBLIC_API_URL` | Public URL of the API **without** `/api` (e.g. `https://api.example.com`) |
| `COOKIE_SECURE` | `true` when both sites use HTTPS |
| `GOOGLE_OAUTH_REDIRECT_URI` | Must match Google Cloud Console: `https://<api-host>/api/auth/google/callback` |
| `DUFFEL_API_KEY`, `OPENAI_API_KEY`, etc. | As needed for booking and agent features |

### 2. Build and run

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

- API health: `GET http://localhost:8000/api/health` (or your `API_PORT`)
- UI: `http://localhost:3000` (or your `FRONTEND_PORT`)

On first start, the API runs **Alembic migrations** when `DATABASE_URL` is Postgres (`DATABASE_USE_ALEMBIC_ONLY` is set automatically in the container entrypoint).

### 3. HTTPS in production

Put **Caddy**, **nginx**, or a cloud load balancer in front of the published ports. Set:

- `COOKIE_SECURE=true`
- `CORS_ORIGINS` and `FRONTEND_URL` to your `https://` URLs
- Rebuild the frontend if `NEXT_PUBLIC_API_URL` changes (it is baked in at **build** time):

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build --no-cache frontend
docker compose -f docker-compose.prod.yml --env-file .env.production up -d frontend
```

### 4. Agent stack (optional)

The default API image does not install LangChain/LangGraph. To enable the agent extra, extend `backend/Dockerfile`:

```dockerfile
RUN pip install -e ".[dev,agent]"
```

Rebuild `api` and `worker` images.

---

## Option B — Vercel + Render (SQLite)

Step-by-step for this repo’s common setup: **[DEPLOY_VERCEL_RENDER.md](./DEPLOY_VERCEL_RENDER.md)**.

---

## Option C — Split hosting (other platforms)

| Component | Suggested platform |
|-----------|-------------------|
| Frontend | [Vercel](https://vercel.com), Netlify, or Cloudflare Pages |
| API | Railway, Render, Fly.io, AWS ECS, or any container host |
| Postgres | Neon, Supabase, RDS, or Compose `postgres` service |
| Redis | Upstash, ElastiCache, or Compose `redis` service |
| Celery worker + beat | Same image as API, separate processes/services |

### Backend

1. Set `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname`
2. Set `DATABASE_USE_ALEMBIC_ONLY=true`
3. Set `REDIS_URL`, `JWT_SECRET_KEY`, `CORS_ORIGINS`, `FRONTEND_URL`, `COOKIE_SECURE=true` (HTTPS)
4. Start: `uvicorn main:app --host 0.0.0.0 --port 8000`
5. Run worker: `celery -A worker.celery_app worker -l info`
6. Run beat (monitor schedules): `celery -A worker.celery_app beat -l info`

Use the `reroute-ai/backend/Dockerfile` on any container platform; inject env vars from the host’s secret store.

### Frontend (Vercel example)

1. Root directory: `reroute-ai/frontend`
2. Build command: `npm run build`
3. Environment variables:
   - `NEXT_PUBLIC_API_URL` = `https://your-api.example.com`
   - Optional: `NEXT_PUBLIC_REROUTE_AGENT_ASYNC=1` if long agent jobs use Celery polling

Redeploy the frontend whenever `NEXT_PUBLIC_*` values change.

### WebSockets

Trip/monitor updates use WebSockets on the API host. Ensure your proxy allows `Upgrade` and sticky sessions if you run multiple API replicas.

---

## Option D — Manual VPS (no Docker for app code)

1. Install Postgres 16 and Redis 7.
2. Clone repo, create venv in `reroute-ai/backend/`, `pip install -e ".[dev]"` (add `,agent` if needed).
3. Export env vars (see root [README.md](../../README.md) and `.env.production.example`).
4. `export DATABASE_USE_ALEMBIC_ONLY=true` and start API with uvicorn.
5. In `reroute-ai/frontend/`: `npm ci && npm run build && npm run start` behind nginx.
6. Run Celery worker and beat on the same machine or a worker VM.

---

## Checklist before go-live

- [ ] `JWT_SECRET_KEY` is unique and secret
- [ ] Postgres (not SQLite) with migrations applied
- [ ] `CORS_ORIGINS` includes only your real frontend origin(s)
- [ ] `FRONTEND_URL` and Google OAuth redirect URI match production URLs
- [ ] `COOKIE_SECURE=true` on HTTPS
- [ ] `DUFFEL_API_KEY` (live vs test mode per your Duffel dashboard)
- [ ] Celery worker running if `EMAIL_VIA_CELERY=true` or monitor/agent async paths are used
- [ ] Health check: `GET /api/health` returns OK

---

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Login works locally but not in prod | `CORS_ORIGINS`, `COOKIE_SECURE`, or API URL mismatch; cookies must be set on the API domain |
| OAuth redirect error | `GOOGLE_OAUTH_REDIRECT_URI` must exactly match Google Console |
| Empty flight data | Missing or invalid `DUFFEL_API_KEY` |
| Agent timeouts | Start worker + Redis; or set `NEXT_PUBLIC_REROUTE_AGENT_ASYNC=1` and ensure Celery processes jobs |
| DB errors on startup | Postgres not ready, wrong `DATABASE_URL`, or migrations failed — check API logs |

For architecture and API behavior, see [AGENT_ARCHITECTURE.md](./AGENT_ARCHITECTURE.md) and the [repository README](../../README.md).
