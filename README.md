<div align="center">

```
███████╗███╗   ██╗ █████╗ ██████╗ ██████╗  ██████╗  ██████╗██╗  ██╗
██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔══██╗██╔═══██╗██╔════╝██║ ██╔╝
███████╗██╔██╗ ██║███████║██████╔╝██║  ██║██║   ██║██║     █████╔╝
╚════██║██║╚██╗██║██╔══██║██╔═══╝ ██║  ██║██║   ██║██║     ██╔═██╗
███████║██║ ╚████║██║  ██║██║     ██████╔╝╚██████╔╝╚██████╗██║  ██╗
╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝     ╚═════╝  ╚═════╝  ╚═════╝╚═╝  ╚═╝
```

**Containers are disposable. State is not.**

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Node](https://img.shields.io/badge/Node-20-339933?logo=node.js&logoColor=white)](https://nodejs.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![Docker](https://img.shields.io/badge/Docker-required-2496ED?logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/badge/License-SnapDock%20SAL%20v1.0-orange)](LICENSE)

</div>

---

> [!WARNING]
> **Hobby project: under active development.** SnapDock is built and maintained in spare time as a personal homelab tool. It may have rough edges, incomplete features, and breaking changes between versions (no semver guarantees yet). Please test all features to see if they work properly for you before deploying to production. You have been warned. Don't kill this poor dev later for any data loss.

---

## What is SnapDock?

Say hello to SnapDock, a vibe-coded mess of a self-hosted Docker state management
daemon that takes **full, consistent, point-in-time snapshots** of your running
stacks, containers, volumes, config, and all and lets you restore them back to
exactly that state with a single click or a single CLI command. Think of it as
Time Machine for your Docker environment, except it also quiesces your databases
before it touches anything, encrypts everything at rest, and gives you a dry-run
mode to verify the restore won't make things worse before you commit to it.

It's not a backup tool that archives your files. It's not a Kubernetes operator.
It's a daemon that sits next to your Docker socket, watches your stacks, and
makes the kind of confidence-inspiring "I can always roll back" experience
available to everyone running containers on bare metal, a homelab server, or a
small VPS, not just teams with enterprise budgets.

**Who is it for?**
- Homelab enthusiasts who self-host a dozen compose stacks and live in fear of
  botching an upgrade.
- Developers who want a pre-deploy safety net before pushing to a staging server.
- Small teams who need snapshot automation, RBAC, and audit trails without a
  six-figure observability contract.

---

## Features

### Intelligent Stack Detection

SnapDock doesn't need you to tell it where your stacks are. When it starts, it
inspects every running container, reads `com.docker.compose.project` labels,
runs `docker compose config` to resolve multi-file compose setups with full
variable substitution, and builds a live map of your environment.

It then goes further: it analyses shared networks and named volumes to detect
**interconnected service groups** (containers that aren't part of the same
compose project but are effectively coupled at runtime). These get surfaced as
logical groups in the UI so you can snapshot them together or manage them
separately.

The result: you open the dashboard and your entire Docker environment is already
organised into stacks, with health indicators, container counts, last-snapshot
timestamps, and schedule status; no config files to write, no agents to install
per-stack.

---

### 25-Step Snapshot Orchestration

Taking a snapshot isn't just `tar -czf`. SnapDock runs a deterministic
orchestration sequence that treats correctness as a hard constraint:

| # | Step | What happens |
|---|---|---|
| 1 | Manifest generation | A full plan is assembled: every container, volume, network, and config file |
| 2 | Stack detection | All containers and their relationships are confirmed live |
| 3 | Compose resolution | `docker compose config` merges all compose files with env substitution |
| 4 | Manifest creation | The plan is written to the database with `complete: false` |
| 5 | Health check | Stack state is assessed: `CLEAN`, `DEGRADED`, or `BROKEN` |
| 6 | Confirmation gate | Non-CLEAN stacks require explicit acknowledgement before proceeding |
| 7 | Diagnostics capture | Last 1000 log lines, `docker inspect` JSON, and 10-minute Docker events window are saved |
| 8 | Pre-snapshot hooks | Per-service bash scripts run (e.g. flush caches, trigger app-level checkpoints) |
| 9 | Restart policy override | Original restart policies are recorded; all services set to `--restart=no` |
| 10 | Application quiescing | Database-aware flush operations run (see Quiescing section) |
| 11 | Pending restart flag | Written to daemon state to survive crashes during the critical window |
| 12 | Stack stop | Graceful shutdown in reverse dependency order |
| 13 | Volume capture | All persistent data archived via Alpine sidecar |
| 14 | Volume I/O | Sidecar containers handle tar extraction; no host root required |
| 15 | Config layer save | Compose files, env files, image digests, networks, and startup order preserved |
| 16 | Encryption | AES-256-GCM chunked encryption applied to all snapshot data |
| 17 | Storage write | Encrypted snapshot written to `/var/lib/snapdock/snapshots/` |
| 18 | Manifest finalisation | Sizes, checksums, and `complete: true` written to database |
| 19 | Stack restart | Services brought back up in dependency order |
| 20 | Post-snapshot hooks | Per-service cleanup scripts run |
| 21 | Restart policy restoration | Original policies restored |
| 22 | Health verify | Daemon confirms the stack is back up and healthy |
| 23 | Retention policy | Configured retention rules applied; old snapshots pruned |
| 24 | Notifications | Webhook dispatched (Slack, Teams, or any HTTP endpoint) |
| 25 | Audit log | Action recorded with actor, timestamp, and outcome |

Every step that can fail safely will. The manifest's `complete` flag ensures
partial snapshots are never mistaken for usable ones. The pending restart flag
ensures restart policies are always restored even if the daemon crashes mid-way.

---

### Database-Aware Quiescing

The moment most backup tools skip (and the one that matters most) is making
sure your database has actually flushed to disk before you freeze its volume.
SnapDock handles this per database type, using `docker exec` into the running
container:

| Database | Quiesce method |
|---|---|
| **PostgreSQL** | `CHECKPOINT`: forces all dirty pages to disk |
| **MySQL / MariaDB** | `FLUSH TABLES WITH READ LOCK`: consistent snapshot point |
| **Redis** | `BGSAVE` + wait for completion: persists the in-memory dataset |
| **MongoDB** | `db.fsyncLock()`: flushes and locks the journal |
| **Generic** | `SIGTERM` + configurable wait: graceful for anything else |

Database type is auto-detected from the container image name. No configuration
required for standard images. For custom images, the quiesce method can be
**overridden per-service** in Settings → General → Quiesce Overrides. Each
service entry accepts one of: `auto` (default detection), `postgresql_checkpoint`,
`mysql_flush_tables`, `redis_bgsave`, `mongodb_fsynclock`, or `skip` (bypass
quiescing entirely for that service).

The quiesce timeout is configurable (`SNAPDOCK_QUIESCE_TIMEOUT`, default 30s).
If quiescing times out, the snapshot is aborted rather than captured in an
inconsistent state.

---

### 17-Step Restore Engine & Dry-Run Verification

Restoring a snapshot is the operation where you most need to trust the tool.
SnapDock's restore engine is built around two principles: **verify before
committing**, and **never leave the stack in a worse state than it started**.

**Dry-run mode** spins up an isolated copy of the stack under a suffixed project
name, restores the snapshot data into it, runs health checks, and reports
whether the restore is viable, all without touching your live stack or data.
Containers bind to **ephemeral host ports** chosen automatically by Docker,
so there are no conflicts with the running live stack. Once the dry-run
completes, the UI surfaces clickable **preview URLs** for every exposed service
port so you can actually browse the restored stack before committing.
It's a full end-to-end verification that the snapshot is intact, the encryption
keys are valid, and the containers come up healthy. The isolated environment is
torn down automatically after inspection.

**Full restore** requires explicit confirmation. The UI calculates and displays
the exact data loss window: *"Data changed since snapshot was finalized (approx
2h 14m)"*. You have to check a box acknowledging this before the restore runs.

The restore sequence mirrors the snapshot sequence: manifest lookup → volume
teardown → Alpine sidecar restore → config reconstruction → stack start →
health verify → audit log. For solo containers where no stopped instance
exists, the engine reconstructs them from the `docker inspect` diagnostics
captured at snapshot time. Restart policy restoration is wrapped in a critical
section that runs even on failure, so the stack is never left with `--restart=no`
set permanently.

---

### Volume I/O via Alpine Sidecar

All volume operations (both capture and restore) are handled by temporary
Alpine 3.19 sidecar containers spun up by the daemon at runtime:

- **No host root required.** The daemon user doesn't need to mount the host
  filesystem or run privileged operations.
- **Storage-driver agnostic.** Works with overlay2, devicemapper, and any other
  driver Docker supports.
- **Backup**: The sidecar mounts the target volume, tars its contents to tmpfs,
  and the daemon retrieves it via `docker cp` / `get_archive()`.
- **Restore**: The daemon injects the tar payload via `put_archive()` wrapped in
  a tar envelope; the sidecar extracts it into the fresh volume.
- **Volume types supported**: Named volumes, anonymous volumes (tracked by
  manifest), bind mounts (host paths). `tmpfs` volumes are recorded in the
  manifest but skipped (in-memory by definition).

Volume diff (change detection between snapshots) is available for stacks where
no single volume exceeds 5 GB.

---

### AES-256-GCM Encryption at Rest

Every snapshot data file is encrypted independently using AES-256-GCM in
chunked mode before it touches disk:

- **Key**: 32-byte base64url-encoded key set via `SNAPDOCK_ENCRYPTION_KEY`.
  Generate one with:
  ```bash
  python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
  ```
- **Key ID**: The manifest records only a key identifier, never the key itself.
- **Per-file encryption**: Each snapshot file is encrypted independently;
  a corrupted file affects only that file, not the entire snapshot.
- **No key, no data**: Losing `.env` (specifically `SNAPDOCK_ENCRYPTION_KEY`) means losing access to all
  encrypted snapshots. Back it up. Separately. Somewhere safe.

---

### Scheduled Snapshots & Retention Policies

Set it and forget it. SnapDock's scheduler runs on APScheduler with
SQLiteJobStore, so schedules survive daemon restarts.

Schedules are configured per stack with a standard 5-field cron expression:

```
┌───────────── minute (0–59)
│ ┌───────────── hour (0–23)
│ │ ┌───────────── day of month (1–31)
│ │ │ ┌───────────── month (1–12)
│ │ │ │ ┌───────────── day of week (0–6, Sun=0)
│ │ │ │ │
0 2 * * *    # every day at 2:00 AM
0 */6 * * *  # every 6 hours
0 3 * * 1    # every Monday at 3:00 AM
```

Each schedule carries its own **retention policy**, configured independently:

| Policy | What it keeps |
|---|---|
| `retention_manual_count` | The N most recent manually triggered snapshots |
| `retention_daily_days` | One snapshot per day for the past N days |
| `retention_weekly_weeks` | One snapshot per week for the past N weeks |

Retention runs automatically after each scheduled snapshot. Locked snapshots
are never pruned regardless of policy.

---

### Role-Based Access Control

Three roles, cleanly separated:

| Role | Can do |
|---|---|
| **viewer** | Read stacks, snapshots, schedules, audit log. Cannot trigger any write operations. |
| **operator** | Everything a viewer can do, plus: trigger snapshots, run dry-run verifies, lock/unlock snapshots, export snapshots. Cannot restore or manage users. |
| **admin** | Full access: restore, user management, API key management, settings, audit log export. |

Authentication supports two methods:
- **JWT tokens**: short-lived, HS256 signed, used by the web UI
- **API keys**: long-lived, bcrypt-hashed, `X-Api-Key` header, used by the CLI
  and CI/CD pipelines. Each key inherits the role of the user it was issued to.

Account lockout activates after 5 consecutive failed login attempts (15-minute
cooldown).

---

### Real-Time Web UI

The UI is a React + Vite + Tailwind application served by Nginx. It connects to
the daemon via both REST API and a WebSocket event stream, so every snapshot
progress update, health state change, and scheduler event appears in real time
without polling.

**Dashboard**: At a glance: total stacks, healthy count, issue count, scheduled
count. Stacks are grouped by health state (CLEAN / DEGRADED / BROKEN) with
per-stack last-snapshot timestamps and quick-action buttons. Stacks that share
user-defined networks or named volumes with any other stack are automatically
annotated with **cross-project coupling badges** so you know which stacks need
to be snapshotted together for a consistent restore.

**Snapshot History**: Per-stack timeline of all snapshots. State badges,
container statuses, timestamps, lock indicators, and one-click actions for
snapshot, restore, dry-run, lock, export, and delete. Each row has an inline
**diff panel** that compares image versions, config changes, and volume size
delta against the previous snapshot.

**Snapshot Inspector**: Deep-dive into a single snapshot: **Services table**
(per-service image, exposed ports, quiesce method + outcome, pre/post hook
outcomes), **Volume Inventory** (name, type, mount path, captured size),
**Diagnostics Capture** (log file list, inspect files), and a collapsible raw
manifest JSON view.

**Audit Log**: Admin-only chronological log of every state-changing action:
snapshots taken, restores run, schedules changed, users created, API keys issued,
snapshots deleted. Filterable by stack name and paginated. Full CSV export for
compliance records.

**Coverage Dashboard**: Compliance view: which stacks have a recent snapshot
(Protected), which are overdue, and which have never been snapshotted
(Unprotected). Gives you the "snapshot hygiene" picture at a glance.

**Settings**: API key generation (shown once, copy it), API key revocation,
password change, webhook configuration, and admin-level global configuration
including per-service quiesce method overrides.

---

### CLI

A lightweight Python CLI for headless environments, CI/CD pipelines, and
scripting. Install with:

```bash
cd cli && pip install -e .
```

Configure via environment variables:

```bash
export SNAPDOCK_URL=http://your-server:9000
export SNAPDOCK_API_KEY=sdck_...
```

**Commands:**

```bash
# See everything at once
snapdock status

# Snapshot before a risky operation
snapdock snapshot myapp --label "pre-deploy-v2.3"

# Get the latest snapshot ID (useful in scripts)
snapdock latest myapp

# Verify a snapshot restores cleanly without touching live data
snapdock restore myapp --snapshot snap_20260515_020000_a1b2 --dry-run

# Actually restore (requires --confirm)
snapdock restore myapp --snapshot snap_20260515_020000_a1b2 --confirm

# Lock a snapshot so retention policy can't delete it
snapdock lock myapp --snapshot snap_20260515_020000_a1b2

# Set an automated schedule
snapdock schedule set myapp --cron "0 2 * * *"
```

**The deploy-and-rollback pattern:**

```bash
SNAP=$(snapdock latest myapp)

snapdock snapshot myapp --label "pre-deploy" \
  && ./deploy.sh \
  || snapdock restore myapp --snapshot "$SNAP" --confirm
```

Snapshot before, deploy, and auto-rollback to the pre-deploy state if the
deployment script exits non-zero. One line.

---

### Coverage Dashboard

The Coverage page answers the question every responsible homelab admin should
be able to answer instantly: *"Which of my stacks don't have a recent snapshot?"*

Stacks are classified into three states:

- **Protected**: Has at least one snapshot within the configured freshness
  window. Green.
- **Overdue**: Has snapshots, but the most recent one is older than the
  freshness threshold. Yellow.
- **Unprotected**: No snapshots at all. Red.

Each row shows the stack name, last snapshot timestamp, days since last snapshot,
and compliance status. The page is designed to be the first thing you check after
making infrastructure changes.

---

### Audit Log & CSV Export

Every action that modifies state (snapshots taken, restores run, schedules
changed, users created, API keys issued, snapshots deleted) is recorded to an
append-only audit log with actor, timestamp, action type, target, outcome, and
IP address. The full log is queryable and filterable in the UI (admin only) and
can be exported as CSV for compliance records or incident post-mortems.

---

### Webhook Notifications

SnapDock can POST a JSON payload to any HTTP endpoint after snapshot and restore
events: Slack, Teams, or any custom webhook. Webhooks are configured in
Settings and include the snapshot ID, stack name, outcome, and trigger type
(scheduled / manual / API).

---

### Pre/Post Snapshot Hooks

Run arbitrary bash commands inside any service container at the right moment in
the orchestration sequence:

- **Pre-snapshot hooks**: run after quiescing and before the stack stops. Ideal
  for application-level checkpointing, flushing caches, or closing file handles.
- **Post-snapshot hooks**: run after the stack restarts. Ideal for re-enabling
  write operations, warming caches, or sending internal notifications.

Hooks are configured per-service and stored in the snapshot manifest alongside
their exit codes, making them fully auditable.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Web UI  (React/Vite/Tailwind)  :9001                        │
│  CLI     (Click/Rich)                                        │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP / WebSocket
┌───────────────────────▼──────────────────────────────────────┐
│  Daemon  (FastAPI + uvicorn)  :8000                          │
│  ├── Container Classifier   (docker-py)                      │
│  ├── Snapshot Engine        (25-step orchestration)          │
│  ├── Restore Engine         (17-step orchestration)          │
│  ├── Volume I/O             (Alpine:3.19 sidecar + tar)      │
│  ├── Encryption             (AES-256-GCM, chunked)           │
│  ├── Scheduler              (APScheduler + SQLiteJobStore)   │
│  └── Event Bus              (asyncio.Queue, WebSocket fan)   │
│  PostgreSQL  (SQLAlchemy ORM)                                │
│  Snapshots  /var/lib/snapdock/snapshots/                     │
└───────────────────────┬──────────────────────────────────────┘
                        │ unix:///var/run/docker.sock
┌───────────────────────▼──────────────────────────────────────┐
│  Docker Engine                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Generate secrets

```bash
# AES-256 encryption key
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

# JWT signing secret
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Create `.env`

```bash
cp .env.example .env
```

Edit it and fill in the generated values:

```env
SNAPDOCK_ENCRYPTION_KEY=<output from step 1>
SNAPDOCK_JWT_SECRET=<output from step 2>
POSTGRES_PASSWORD=<a strong password>
SNAPDOCK_DATABASE_URL=postgresql+psycopg2://snapdock:<a strong password>@db:5432/snapdock
```

### 3. Start the stack

```bash
docker compose up -d
```

| Service | URL |
|---|---|
| Web UI | http://localhost:9001 |
| Daemon API | http://localhost:9000 |
| API docs (Swagger) | http://localhost:9000/docs |

A default admin account is created on first startup:

- **Email:** `admin@snapdock.local`
- **Password:** `changeme`

**Change this immediately** via Settings → Change Password.

---

## Project Layout

```
SnapDock/
├── .env.example                    ← copy to .env for docker-compose setup
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── snapdock.env.example        ← copy to snapdock.env for local dev (non-Docker)
│   └── snapdock/
│       ├── main.py                 ← FastAPI app, lifespan, all routers
│       ├── config.py               ← Pydantic-settings
│       ├── database.py             ← SQLAlchemy models + engine
│       ├── docker_client.py        ← Cached docker-py singleton
│       ├── events.py               ← In-process WebSocket event bus
│       ├── scheduler.py            ← APScheduler singleton
│       ├── api/                    ← Route handlers (one file per domain)
│       ├── auth/                   ← JWT, API keys, RBAC
│       ├── core/                   ← Snapshot, restore, volume, crypto engines
│       └── models/                 ← Pydantic manifest model + API schemas
├── cli/
│   └── snapdock_cli/main.py        ← Click CLI
├── frontend/
│   ├── src/pages/                  ← React page components
│   ├── src/hooks/useEventStream.ts ← WebSocket event hook
│   ├── src/lib/api.ts              ← axios API client
│   └── nginx.conf / nginx.prod.conf
├── docker-compose.yml              ← Development stack
├── docker-compose.prod.yml         ← Production stack (GHCR images)
└── .gitignore
```

---

## Development

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate       # Windows
source .venv/bin/activate    # macOS / Linux

pip install -r requirements.txt
cp snapdock.env.example snapdock.env   # fill in keys
python -m snapdock.main
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # http://localhost:3000 (proxied to backend on :8000)
```

### Full stack

```bash
docker compose up -d
```

---

## Security Notes

- `.env` (root) contains your AES-256 key and JWT secret. It is
  `.gitignore`d. **Back it up separately and securely.** Losing it means
  losing access to all encrypted snapshots permanently; there is no recovery
  mechanism.
- The Docker socket is mounted **read-only** for classification. Volume I/O
  uses temporary Alpine sidecar containers; no host root access required.
- All API endpoints require authentication (JWT or API key).
- Restore operations are restricted to the `admin` role by default.
- Account lockout activates after 5 consecutive failed login attempts.
- To report a security vulnerability privately, see [SECURITY.md](SECURITY.md).

---

## CI/CD Integration

SnapDock is designed to slot into any CI/CD pipeline. The recommended approach
is an **API key** with `operator` or `admin` role, so the pipeline never needs
a user password.

### 1. Create an API key

In the UI go to **Settings → API Keys → Generate**. Copy the key; it is shown
once. Store it as a secret in your CI/CD system:

```bash
# GitHub Actions secret:  SNAPDOCK_API_KEY
# GitLab CI variable:     SNAPDOCK_API_KEY
# Jenkins credential:     snapdock-api-key (Secret text)
```

### 2. Direct REST API (curl)

All endpoints are under `http://<host>:9000`. Authenticate with
`X-Api-Key: <key>` or `Authorization: Bearer <jwt>`.

```bash
SNAPDOCK=http://your-server:9000
KEY=sdck_...

# Trigger a snapshot
curl -s -X POST "$SNAPDOCK/stacks/myapp/snapshots" \
  -H "X-Api-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"label": "pre-deploy"}'

# Get the latest snapshot ID
SNAP_ID=$(curl -s "$SNAPDOCK/stacks/myapp/snapshots?limit=1" \
  -H "X-Api-Key: $KEY" | jq -r '.[0].id')

# Dry-run restore to verify snapshot integrity
curl -s -X POST "$SNAPDOCK/stacks/myapp/snapshots/$SNAP_ID/restore" \
  -H "X-Api-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true, "confirmed": false}'

# Full restore (requires admin key)
curl -s -X POST "$SNAPDOCK/stacks/myapp/snapshots/$SNAP_ID/restore" \
  -H "X-Api-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"confirmed": true, "dry_run": false}'

# Lock a snapshot so retention can't prune it
curl -s -X PATCH "$SNAPDOCK/stacks/myapp/snapshots/$SNAP_ID/lock" \
  -H "X-Api-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"locked": true}'
```

Full interactive API reference: `http://your-server:9000/docs`

### 3. CLI (recommended for scripts)

The CLI wraps all of the above and is easier to read in pipeline YAML:

```bash
pip install -e ./cli
export SNAPDOCK_URL=http://your-server:9000
export SNAPDOCK_API_KEY=sdck_...
```

### 4. GitHub Actions

```yaml
# .github/workflows/deploy.yml
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install SnapDock CLI
        run: pip install -e ./cli

      - name: Snapshot before deploy
        env:
          SNAPDOCK_URL: ${{ secrets.SNAPDOCK_URL }}
          SNAPDOCK_API_KEY: ${{ secrets.SNAPDOCK_API_KEY }}
        run: |
          SNAP=$(snapdock snapshot myapp --label "pre-deploy-${{ github.sha }}")
          echo "SNAP_ID=$SNAP" >> $GITHUB_ENV

      - name: Deploy
        run: ./deploy.sh

      - name: Rollback on failure
        if: failure()
        env:
          SNAPDOCK_URL: ${{ secrets.SNAPDOCK_URL }}
          SNAPDOCK_API_KEY: ${{ secrets.SNAPDOCK_API_KEY }}
        run: snapdock restore myapp --snapshot "$SNAP_ID" --confirm
```

### 5. GitLab CI

```yaml
# .gitlab-ci.yml
deploy:
  stage: deploy
  before_script:
    - pip install -e ./cli
  script:
    - export SNAP=$(snapdock snapshot myapp --label "pre-deploy-$CI_COMMIT_SHORT_SHA")
    - ./deploy.sh
  after_script:
    - |
      if [ "$CI_JOB_STATUS" != "success" ]; then
        snapdock restore myapp --snapshot "$SNAP" --confirm
      fi
  variables:
    SNAPDOCK_URL: $SNAPDOCK_URL        # set in GitLab CI/CD variables
    SNAPDOCK_API_KEY: $SNAPDOCK_API_KEY
```

### 6. Jenkins

```groovy
// Jenkinsfile
pipeline {
    agent any
    environment {
        SNAPDOCK_URL     = credentials('snapdock-url')
        SNAPDOCK_API_KEY = credentials('snapdock-api-key')
    }
    stages {
        stage('Snapshot') {
            steps {
                sh 'pip install -e ./cli'
                sh 'snapdock snapshot myapp --label "pre-deploy-${GIT_COMMIT[0..7]}"'
                script { env.SNAP_ID = sh(script: 'snapdock latest myapp', returnStdout: true).trim() }
            }
        }
        stage('Deploy') {
            steps { sh './deploy.sh' }
        }
    }
    post {
        failure {
            sh 'snapdock restore myapp --snapshot "${SNAP_ID}" --confirm'
        }
    }
}
```

---

## License

SnapDock is source-available software. It is free for personal use, homelab
self-hosting, and educational purposes. Commercial use requires written
permission.

See [LICENSE](LICENSE) for the full terms. For commercial licensing enquiries:
[dhruboalok0@gmail.com](mailto:dhruboalok0@gmail.com)

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, code style
guidelines, and how to submit a pull request.
