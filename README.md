# Cartalytix

Self-hosted receipt tracker. First application slice: local password accounts,
owner-scoped receipt APIs, SQL totals per currency and an English/Czech Vue PWA.
The Lidl CZ connector imports receipt history, source items and discounts;
other stores remain unavailable. The original Lidl experiment remains in `experiments/`.

| Path | Purpose |
| --- | --- |
| `backend/` | Python 3.15, FastAPI, SQLAlchemy, Alembic, Celery; managed by uv |
| `frontend/` | Vue, TypeScript, Vite, Vue Query, Vue I18n, PWA |
| `experiments/` | Lidl prototype and `provider-research.md` |
| `ARCHITECTURE.md` | Approved direction and remaining decisions |

## Local setup

Requires Docker Compose; Node 24 and npm for the local frontend dev server.
Copy `.env.example` to `.env` and set a random URL-safe `POSTGRES_PASSWORD`.
There are no default application credentials or public registration.

```powershell
uv --directory backend sync --locked --extra browser
uv --directory backend run --no-sync python -c "from pathlib import Path; from cryptography.fernet import Fernet; p=Path('../.secrets/lidl.key'); p.parent.mkdir(exist_ok=True); p.open('xb').write(Fernet.generate_key())"
docker compose build
docker compose up -d postgres redis
docker compose run --rm api uv run --no-sync alembic upgrade head
docker compose run --rm api uv run --no-sync cartalytix create-user
docker compose up -d
npm --prefix frontend ci
npm --prefix frontend run dev
```

Enter the account password at the terminal prompt; never put it in a command or
chat. Open `http://localhost:5173` for local development; Vite forwards API calls
to `127.0.0.1:8000`.

The Docker frontend serves static files at `127.0.0.1:8080` (change
`FRONTEND_PORT` in `.env` if occupied), the API at
`127.0.0.1:8000`. The existing VPS Nginx must route `/api/` to the API and all
other paths to the frontend under one HTTPS origin; no proxy container is
included. Configure that origin, production mode and secure cookies through
`.env`. Set `CARTALYTIX_TRUSTED_PROXY_HOSTS` to the actual Nginx peer IP/CIDR
seen inside the API container, and have Nginx overwrite `X-Forwarded-For`.
This keeps login limits per client. PostgreSQL and Redis have no published ports.

Create the encryption key once; keep it outside Git and back it up separately
from the database. `TOKEN_KEY_PATH` overrides its host path. Losing or replacing
the key requires reconnecting all store accounts.

## Lidl account linking

Add a named Lidl account under Connections, then choose **Link with Chrome**.
Run its displayed command on a computer with uv, this repository and Chrome.
Enter Cartalytix credentials at the local terminal prompt; Lidl credentials and
verification codes go directly into Lidl's browser page. No tokens are printed
or saved locally; the server stores only an encrypted refresh token.
Refresh Connections after linking and choose **Sync now**; schedules are optional.

This uses an unofficial native OAuth/API flow. Initial linking requires desktop
Chrome because Lidl's native callback cannot return directly to this PWA.
API changes, login challenges, rate limits and provider history retention can
interrupt imports or require relinking. Every sync verifies all available list
pages, downloads missing receipt details and keeps source JSON for rejected
documents; arithmetic/precision failures do not enter spending aggregates.

## Checks

```powershell
uv --directory backend sync --locked --all-groups --extra browser
uv --directory backend run --no-sync ruff check .
uv --directory backend run --no-sync ruff format --check .
uv --directory backend run --no-sync ty check
uv --directory backend run --no-sync pytest
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
```

GitHub Actions selects affected backend/frontend jobs; docs/experiments-only
changes skip both. Require the always-running `Required checks` gate in branch
protection. Install local hooks with `uvx pre-commit install`.

The service worker caches static assets only. Private API responses use
`no-store`; logout clears the in-memory query cache. Beat dispatches due/pending
jobs every minute; workers serialize each account's token rotation and imports.
Broker interruptions keep jobs in the database for retry. Categorization and AI
remain deferred.
