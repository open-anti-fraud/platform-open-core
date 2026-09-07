# Platform Open Core

An open-source face recognition platform for building biometric applications and services.

Key capabilities include:

* **Face Detection** — detecting and locating faces in images.
* **Identification and Verification** — 1:N face search and 1:1 face comparison.
* **Face Quality Assessment** — evaluating whether a face image is suitable for recognition.
* **Liveness Detection** — evaluating whether a face image is live and not a photo or video.
* **API** — integration with external applications and services through a programmatic interface.

The platform can be used as a foundation for access control, identity verification, user enrollment, search, and other face recognition scenarios.

## Quick Start

### Prerequisites

- Docker Engine with Compose v2

### Configuration

Credentials live in [`.env`](.env):

| Variable | Purpose |
|----------|---------|
| `POSTGRES_*` | Database user, password, DB name |
| `RABBIT_*` | RabbitMQ user / password |
| `SERVICE_KEY` | Service token |
| `PLATFORM_ADMIN_*` | Admin user email / password |
| `PLATFORM_DEFAULT_*` | Default user email / password |

Backend listens on host port **8080** (`8080:80` in Compose).

Postgres data is stored in the named Docker volume `platform_postgres_data` (mounted at `/var/lib/postgresql/data`).

## Run

```bash
docker compose up -d
```

## Test

Check GraphQL UI:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/api/v2/
```

Login and get an access token:

```bash
curl -s -X POST http://localhost:8080/internal-api/v2/ \
  -H 'Content-Type: application/json' \
  -d '{"query":"mutation { login(login:\"admin@example.com\", password:\"change-this-password\") { ok me { workspaces { accesses { token } } } } }"}'
```

Use the returned `token` (UUID) in subsequent requests:

```bash
-H "Token: <uuid>"
```

Default users (from `.env`):

- Admin: `admin@example.com` / `change-this-password`
- Default: `default@example.com` / `change-this-password`

## Explore API in sandbox

Open the GraphiQL sandbox in your browser:

[http://localhost:8080/api/v2/](http://localhost:8080/api/v2/)

Configure token header in the sandbox to use API.
