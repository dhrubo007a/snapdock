# Contributing to SnapDock

Thanks for your interest in contributing. SnapDock is a personal project with a
source-available license — contributions are welcome from the community, but
please read these guidelines before opening a PR.

---

## Before you start

- **Check existing issues** — your bug or idea may already be tracked.
- **Open an issue first** for significant changes (new features, refactors,
  changes to the snapshot/restore orchestration). Getting alignment before
  writing code saves everyone time.
- **Small fixes** (typos, docs, obvious bugs) can go straight to a PR.

---

## License agreement

By submitting a pull request, you agree that your contribution is made under
the same license as SnapDock (see [LICENSE](LICENSE)). This means:

- You retain copyright of your code.
- You grant the project the right to use, modify, and distribute it under the
  SnapDock Source Available License.
- Contributions to this project do **not** grant you a commercial license.

---

## Development setup

### Backend (FastAPI + Python)

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp snapdock.env.example snapdock.env   # fill in SNAPDOCK_ENCRYPTION_KEY and SNAPDOCK_JWT_SECRET
python -m snapdock.main
```

The daemon starts on `http://localhost:8000`. Interactive API docs at
`http://localhost:8000/docs`.

### Frontend (React + Vite + Tailwind)

```bash
cd frontend
npm install
npm run dev    # http://localhost:3000 — proxied to :8000
```

### Full stack via Docker Compose (recommended)

```bash
# Generate an encryption key first
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"

cp backend/snapdock.env.example backend/snapdock.env
# fill in SNAPDOCK_ENCRYPTION_KEY and SNAPDOCK_JWT_SECRET

docker compose up -d
```

---

## Project structure

| Path | What lives here |
|---|---|
| `backend/snapdock/core/` | Snapshot engine, restore engine, volume I/O, encryption |
| `backend/snapdock/api/` | FastAPI route handlers |
| `backend/snapdock/auth/` | JWT, API keys, RBAC |
| `backend/snapdock/models/` | Pydantic manifest model, API schemas |
| `frontend/src/pages/` | React page components |
| `frontend/src/hooks/` | SSE (Server-Sent Events) event stream hook |
| `cli/snapdock_cli/` | Click CLI |

---

## Code style

- **Python**: Follow the existing style. Keep functions focused and names
  descriptive. No type annotation requirement on unchanged code.
- **TypeScript / React**: Functional components, hooks-first. Keep components
  in `pages/` or `components/` as appropriate.
- **No AI-generated boilerplate**: If you used AI assistance, review the output
  carefully and own the result.

---

## Commit messages

Use the imperative mood and be specific:

```
# Good
Fix volume restore failure when bind mount path has spaces
Add dry-run flag to CLI restore command
Clarify quiesce timeout behaviour in README

# Bad
fixes stuff
update
WIP
```

---

## What makes a good PR

- Focused on one thing. Split unrelated changes into separate PRs.
- Includes a clear description of what changed and why.
- Does not include `backend/snapdock.env`, `snapdock-data/`, or
  `snapdock_plan.md`.
- Does not break the existing snapshot + restore cycle.
- Passes a basic manual test (spin up a stack, take a snapshot, restore it).

---

## Questions?

Open an issue with the `question` label or reach out via
[dhruboalok0@gmail.com](mailto:dhruboalok0@gmail.com).
