# Cartalytix

Self-hosted receipt tracker. First application slice: local password accounts,
owner-scoped receipt APIs, SQL totals per currency and an English/Czech Vue PWA.
The Lidl CZ connector imports receipt history, source items and discounts.
The Mixit.cz connector imports shipped orders (products and mixes); Mixit does not
itemize shipping, vouchers or credits, so the difference to the paid total is stored
as a "Shipping and fees" item or a "Discounts and credits" order discount.
The Fermato.cz connector (Shoptet customer area, HTML) imports all orders; the
password is stored Fernet-encrypted because Shoptet has no refresh token. Paid fees
become items, coupons order discounts, Shoptet whole-CZK rounding `rounding_amount`;
orders shown as 0 Kč use the sum of their lines.
Other stores remain unavailable. The original Lidl experiment remains in `experiments/`.

| Path | Purpose |
| --- | --- |
| `backend/` | Python 3.15, FastAPI, SQLAlchemy, Alembic, Celery; managed by uv |
| `frontend/` | Vue, TypeScript, Vite, Vue Query, Vue I18n, PWA |
| `experiments/` | Lidl prototype and `provider-research.md` |
| `ARCHITECTURE.md` | Approved direction and remaining decisions |

## Architecture

```mermaid
%%{init: {"theme": "base", "themeVariables": {"fontSize": "14px", "lineColor": "#94a3b8", "edgeLabelBackground": "#f8fafc"}, "flowchart": {"curve": "basis", "nodeSpacing": 30, "rankSpacing": 50, "padding": 12}}}%%
flowchart TB
    user(["<b>Browser</b> · PWA"]):::client

    subgraph edge["Edge"]
        direction LR
        proxy["<b>Reverse proxy</b><br/>Vite dev · Nginx prod"]:::web
        frontend["<b>Frontend</b><br/>Vue 3 · TypeScript"]:::web
    end

    subgraph backend["Backend · Python 3.15 · uv"]
        direction LR
        migrate["<b>Migrate</b><br/>Alembic, one-shot"]:::job
        api["<b>API</b><br/>FastAPI · uvicorn"]:::svc
        worker["<b>Worker</b><br/>Celery"]:::svc
        beat["<b>Beat</b><br/>Celery scheduler"]:::svc
    end

    subgraph data["Data"]
        direction LR
        pg[("<b>PostgreSQL 17</b><br/>users · receipts · jobs")]:::db
        redis[("<b>Redis 7</b><br/>broker · rate limit")]:::db
        key[/"<b>Fernet key</b><br/>Docker secret"/]:::secret
    end

    subgraph connectors["Store connectors"]
        direction TB
        lidl["<b>Lidl CZ</b><br/>OAuth · headless Chrome"]:::conn
        lidlapi["<b>Lidl API</b><br/>external · unofficial"]:::ext
        mixit["<b>Mixit CZ</b><br/>email/password · token"]:::conn
        mixitapi["<b>Mixit API</b><br/>external · unofficial"]:::ext
        fermato["<b>Fermato CZ</b><br/>email/password · session"]:::conn
        fermatoweb["<b>Fermato web</b><br/>external · Shoptet HTML"]:::ext
    end

    user --> proxy
    proxy -- static --> frontend
    proxy -- /api --> api
    migrate --> pg
    api --> pg & redis
    worker -- consume --> redis
    worker --> pg
    beat -- enqueue --> redis
    backend -. token encryption .-> key
    api -. link account .-> lidl
    worker -. sync receipts .-> lidl
    lidl -- HTTPS --> lidlapi
    api -. link account .-> mixit
    worker -. sync orders .-> mixit
    mixit -- HTTPS --> mixitapi
    api -. link account .-> fermato
    worker -. sync orders .-> fermato
    fermato -- HTTPS --> fermatoweb

    classDef client fill:#f1f5f9,stroke:#64748b,color:#0f172a
    classDef web fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e
    classDef svc fill:#ede9fe,stroke:#7c3aed,color:#2e1065
    classDef job fill:#f5f3ff,stroke:#7c3aed,color:#2e1065,stroke-dasharray:4 3
    classDef conn fill:#ffedd5,stroke:#ea580c,color:#431407
    classDef ext fill:#fff7ed,stroke:#ea580c,color:#431407,stroke-dasharray:4 3
    classDef db fill:#dcfce7,stroke:#16a34a,color:#052e16
    classDef secret fill:#fef9c3,stroke:#ca8a04,color:#422006
    style edge fill:none,stroke:#0284c7,stroke-dasharray:6 4,color:#0284c7
    style backend fill:none,stroke:#7c3aed,stroke-dasharray:6 4,color:#7c3aed
    style data fill:none,stroke:#16a34a,stroke-dasharray:6 4,color:#16a34a
    style connectors fill:none,stroke:#ea580c,stroke-dasharray:6 4,color:#ea580c
```

Connectors run in-process in the API (login) and worker (sync); there is no
separate connector service. Jobs are persisted in PostgreSQL, so a Redis outage
delays but does not lose them.

## Local setup

Requires Docker Compose. Node and Vite run in the frontend container.
Copy `.env.example` to `.env` and set a random URL-safe `POSTGRES_PASSWORD`.
There are no default application credentials or public registration.

```powershell
uv --directory backend sync --locked --extra browser
uv --directory backend run --no-sync python -c "from pathlib import Path; from cryptography.fernet import Fernet; p=Path('../.secrets/lidl.key'); p.parent.mkdir(exist_ok=True); p.open('xb').write(Fernet.generate_key())"
docker compose build
docker compose up -d
docker compose exec api uv run --no-sync cartalytix create-user
```

Enter the account password at the terminal prompt; never put it in a command or
chat. After initial setup, start development with `docker compose build` and
`docker compose up -d`; no override files are needed. Migrations run before
the API and workers start.

The frontend runs Vite at `http://localhost:8080` (or `FRONTEND_PORT` from `.env`),
listens on all interfaces, and proxies `/api/` to FastAPI inside Docker.
Frontend source changes reload automatically. For mobile testing on the same
Wi-Fi, use the computer's IP and add that exact origin to
`CARTALYTIX_ALLOWED_ORIGINS` in `.env`. The API is bound to `127.0.0.1:8000`;
PostgreSQL and Redis have no published ports. No local Nginx or `work/` files
are required. This Compose setup is for development; production serves the
frontend build and `/api/` through the existing VPS Nginx under one HTTPS origin.

Create the encryption key once; keep it outside Git and back it up separately
from the database. `TOKEN_KEY_PATH` overrides its host path. Losing or replacing
the key requires reconnecting all store accounts.

## Lidl account linking

Add a named Lidl account under Connections, then choose **Connect account**.
Enter Lidl credentials in the application and a verification code if requested.
The backend uses ephemeral headless Chrome for device/CAPTCHA initialization and
HTTP for OAuth/password/MFA. Passwords and codes are never persisted; only the
validated refresh token is stored encrypted. Choose **Sync now** after linking;
schedules are optional. All connector code lives in the backend, independently
of `experiments/`; no experimental service or port is required.

The Docker image includes Chrome. Run one API process: active login continuations
remain in bounded process-local memory for at most ten minutes and are discarded
on restart. Existing stored connections and syncs survive restart. Allow up to
180 seconds for login requests at the external reverse proxy.

This uses an unofficial native OAuth/API flow.
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
