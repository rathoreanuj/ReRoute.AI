# Deploy: Vercel (frontend) + Render (backend) + SQLite

Use this guide when the **Next.js** app is on Vercel and the **FastAPI** API is on Render, with **SQLite** as the database.

## Deploy from the terminal (PowerShell)

Scripts live in `reroute-ai/scripts/`. Run from the **repository root**.

### One-time authentication

```powershell
# Render (pick one)
& .\tools\render\cli_v2.8.0.exe login
# or: $env:RENDER_API_KEY = "rnd_..."   # from Render Dashboard → Account Settings → API Keys

# Vercel (you may already be logged in)
vercel login
vercel whoami
```

### Full deploy

```powershell
# First time: create Render web service from GitHub + deploy
.\reroute-ai\scripts\deploy.ps1 -CreateRenderService -Production

# Later deploys (git push + trigger, or CLI only):
.\reroute-ai\scripts\deploy-render.ps1
.\reroute-ai\scripts\deploy-vercel.ps1 -ApiUrl https://reroute-api.onrender.com -Production
```

`deploy-render.ps1` reads secrets from repo `.env` / `reroute-ai/.env` (not committed). After Render deploy it writes `reroute-ai/scripts/.deploy-state.json` with the API URL for Vercel.

Update production URLs in `.env` before deploy (`CORS_ORIGINS`, `FRONTEND_URL`, `GOOGLE_OAUTH_REDIRECT_URI`) or set them in the Render dashboard after Vercel gives you a URL.

### Manual Render CLI (optional)

```powershell
$render = ".\tools\render\cli_v2.8.0.exe"
& $render -o json services list -e
& $render -o json deploys create srv-XXXXXXXX --confirm --wait
```

---

Replace placeholders:

- `https://YOUR-APP.vercel.app` — Vercel production URL (Project → Settings → Domains)
- `https://reroute-api.onrender.com` — Render service URL (after first deploy)

---

## Important: SQLite on Render

Render’s filesystem is **ephemeral**: redeploys and restarts can **wipe** `data/reroute.db`. SQLite is fine for demos; for real users use [Render Postgres](https://render.com/docs/databases) and set:

```env
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST/DATABASE
DATABASE_USE_ALEMBIC_ONLY=true
```

---

## Security

- Do **not** commit `.env` files or paste API keys into GitHub.
- Generate a new `JWT_SECRET_KEY` for production (not `change-me-...`).
- If keys were shared in chat or tickets, **rotate** them in each provider’s dashboard.

---

## 1. Render (backend)

### Create the service

1. [Render Dashboard](https://dashboard.render.com) → **New** → **Web Service** → connect this repo.
2. **Root Directory:** `reroute-ai/backend`
3. **Runtime:** Python 3
4. **Build Command:** `pip install -r requirements.txt`
5. **Start Command:** `mkdir -p data && uvicorn main:app --host 0.0.0.0 --port $PORT`
6. **Health Check Path:** `/api/health`

Or use the repo **`render.yaml`** blueprint at the repository root (then fill secret env vars in the dashboard).

### Environment variables (Render)

Copy from your local `.env`, but update URLs and production flags:

| Key | Value |
|-----|--------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/reroute.db` |
| `DATABASE_USE_ALEMBIC_ONLY` | `false` |
| `JWT_SECRET_KEY` | Long random string (new for prod) |
| `API_PREFIX` | `/api` |
| `CORS_ORIGINS` | `https://YOUR-APP.vercel.app` (exact; add preview URL if needed: `https://xxx.vercel.app,https://YOUR-APP.vercel.app`) |
| `FRONTEND_URL` | `https://YOUR-APP.vercel.app` |
| `COOKIE_SECURE` | `true` |
| `COOKIE_SAMESITE` | `none` (required: frontend and API are different sites) |
| `GOOGLE_OAUTH_CLIENT_ID` | From Google Cloud |
| `GOOGLE_OAUTH_CLIENT_SECRET` | From Google Cloud |
| `GOOGLE_OAUTH_REDIRECT_URI` | `https://reroute-api.onrender.com/api/auth/google/callback` |
| `DUFFEL_API_KEY` | Your Duffel key |
| `OPENAI_API_KEY` | Your OpenAI key |
| `RESEND_API_KEY` | Your Resend key |
| `AVIATION_STACK_API_KEY` | Your key |
| `OPENROUTESERVICE_API_KEY` | Your key (or legacy name `OPEN_ROUTE_SERVICE_API_KEY`) |
| `EMAIL_VIA_CELERY` | `false` (no Redis worker on Render free tier) |

After deploy, open `https://YOUR-SERVICE.onrender.com/api/health` — should return OK.

**Note:** Free Render services spin down after inactivity; first request may be slow.

### Google Cloud Console

In your OAuth Web client:

- **Authorized JavaScript origins:** `https://YOUR-APP.vercel.app`
- **Authorized redirect URIs:** `https://YOUR-SERVICE.onrender.com/api/auth/google/callback`

Keep localhost entries if you still develop locally.

---

## 2. Vercel (frontend)

### Create the project

1. [Vercel](https://vercel.com) → **Add New Project** → import this repo.
2. **Root Directory:** `reroute-ai/frontend`
3. Framework: Next.js (auto-detected)

### Environment variables (Vercel)

| Key | Value |
|-----|--------|
| `NEXT_PUBLIC_API_URL` | `https://YOUR-SERVICE.onrender.com` (no trailing slash; no `/api`) |

Redeploy after changing `NEXT_PUBLIC_*` variables (they are embedded at build time).

### Deploy

Push to the connected branch or click **Deploy**. Open the Vercel URL and test login / trips.

---

## 3. Order of operations

1. Deploy **Render** first; note the `onrender.com` URL.
2. Set Render env vars including `CORS_ORIGINS` and `GOOGLE_OAUTH_REDIRECT_URI` (use placeholders, then update after Vercel URL is known).
3. Deploy **Vercel** with `NEXT_PUBLIC_API_URL` pointing at Render.
4. Update Render `CORS_ORIGINS` and `FRONTEND_URL` to the final Vercel URL.
5. Update Google OAuth origins/redirects.
6. Redeploy both if env vars changed.

---

## 4. Verify

| Check | Expected |
|--------|----------|
| `GET https://YOUR-SERVICE.onrender.com/api/health` | Healthy JSON |
| Login on Vercel | Session works (cookies cross-origin) |
| Google sign-in | Redirect back to Vercel dashboard |
| Create trip | Data stored (until Render redeploy wipes SQLite) |

---

## 5. Troubleshooting

| Issue | Fix |
|--------|-----|
| CORS error in browser | `CORS_ORIGINS` must exactly match the Vercel origin (`https://`, no trailing slash) |
| Login succeeds then 401 | `COOKIE_SECURE=true` and `COOKIE_SAMESITE=none`; API URL in `NEXT_PUBLIC_API_URL` must match Render |
| Google `redirect_uri_mismatch` | Redirect URI in Google Console must match `GOOGLE_OAUTH_REDIRECT_URI` on Render exactly |
| Maps/routing not working | Set `OPENROUTESERVICE_API_KEY` (not only `OPEN_ROUTE_SERVICE_API_KEY` — both work now) |
| Data disappeared | Expected with SQLite on Render after redeploy; move to Postgres |

---

## Optional later

- **Render Postgres** + `DATABASE_USE_ALEMBIC_ONLY=true`
- **Redis + Celery** on Render for `EMAIL_VIA_CELERY` and monitor beat (separate worker service)
- Custom domain on Vercel and Render; update all URLs and OAuth entries
