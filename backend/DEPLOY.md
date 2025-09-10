# Deploying CardTraders FastAPI

This guide shows two quick ways to deploy the backend and get a stable HTTPS hostname for `api.cardtraders.org`.

## Option A) Render (no Docker required)

1) Push this repo to GitHub (private is fine).
2) Go to https://render.com → New → Web Service → Connect your repo.
3) Settings:
   - Root Directory: `CardTraders-backend/backend`
   - Runtime: Python
   - Build Command:
     `pip install --upgrade pip && pip install -r requirements.txt`
   - Start Command:
     `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Instance Type: Free (for testing) or Starter (recommended)
4) Environment Variables (add as needed):
   - `PYTHON_VERSION=3.11`
   - `DATABASE_URL` (optional; defaults to sqlite file)
   - `MONGODB_URI` (MongoDB Atlas recommended)
   - `MONGODB_DB_NAME=cardtraders`
   - Any provider keys (e.g., `KAKAO_*`, `PAYMENT_*`)
5) Deploy and wait until the service is live, e.g. `https://cardtraders-backend.onrender.com`.
6) Verify health:
   - `GET https://<render-host>/health/` should return `{ "status": "ok" }`.

Once it’s up, create a DNS CNAME record on Squarespace:
- Host: `api`
- Type: `CNAME`
- Data: `<render-hostname>` (no `https://`, no path)
- TTL: 1 hr

After propagation, verify: `https://api.cardtraders.org/health/`.

## Option B) Docker anywhere (Fly.io/Railway/VM)

A minimal Dockerfile is provided under `CardTraders-backend/backend/Dockerfile`.

- Build: `docker build -t cardtraders-api .`
- Run: `docker run -p 8000:8000 --env-file .env cardtraders-api`

For Fly.io:
- `fly launch` (choose existing Dockerfile)
- Set env in Fly secrets: `fly secrets set MONGODB_URI=...` etc.
- Deploy: `fly deploy`
- Map `api.cardtraders.org` via Fly Certificates (CNAME).

## Production notes

- CORS: tighten allowed origins when you publish the mobile app.
- Mongo: If you don’t use Mongo features yet, set `MONGO_ENABLED=false` to avoid 503s on auth/images endpoints.
- Payments: provider env vars are optional; the sandbox path works without external providers.
- Health endpoints: `/health/` and `/health/db`.

## Next steps for the app

- When a stable HTTPS hostname is live, set EAS secret `EXPO_PUBLIC_API=https://api.cardtraders.org` (no trailing slash) before building.
