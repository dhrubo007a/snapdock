# Security Policy

## Supported versions

SnapDock is currently in active development. Security fixes are applied to the
latest commit on the `main` branch only.

| Version | Supported |
|---|---|
| `main` (latest) | Yes |
| Older tags / forks | No |

---

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in SnapDock — including issues
related to encryption, authentication, privilege escalation, or the Docker
socket handling — please report it privately:

**Email:** [dhruboalok0@gmail.com](mailto:dhruboalok0@gmail.com)  
**Subject line:** `SnapDock Security Vulnerability Report`

Include as much detail as you can:

- A clear description of the vulnerability
- The component affected (e.g., snapshot engine, JWT auth, API key handling,
  volume I/O, restore endpoint)
- Steps to reproduce or a proof-of-concept (PoC)
- Potential impact assessment
- Any suggested mitigations, if you have them

---

## What to expect

- **Acknowledgement** within 72 hours of receiving your report.
- **Initial assessment** (confirmed / not confirmed / needs more info)
  within 7 days.
- **Fix or mitigation** prioritised based on severity:
  - Critical / High — targeted within 14 days
  - Medium — targeted within 30 days
  - Low / Informational — addressed in the normal development cycle
- You will be credited in the fix commit or release notes unless you prefer
  to remain anonymous.

---

## Security design notes

These are documented here to help researchers understand the threat model:

### Encryption key and JWT secret
`SNAPDOCK_ENCRYPTION_KEY` and `SNAPDOCK_JWT_SECRET` live in
`backend/snapdock.env`, which is excluded from version control via `.gitignore`.
Losing the encryption key means losing access to all encrypted snapshot data —
there is no key recovery mechanism by design.

### Docker socket
The Docker socket (`/var/run/docker.sock`) is mounted with full access. It is
required for container lifecycle operations (stop, start, create, remove) and
volume management in addition to stack classification. Volume I/O uses
temporary Alpine sidecar containers and does not require host root access.

### Authentication
All API endpoints require authentication via JWT (short-lived, HS256 signed)
or API keys (bcrypt-hashed, stored in the database). Account lockout applies
after 5 consecutive failed login attempts (15-minute cooldown).

### Role-based access control
Three roles exist: `viewer`, `operator`, and `admin`. Restore operations are
restricted to `admin` by default. API keys inherit the role of the user they
are issued to.

### Rate limiting
All endpoints are rate-limited via `slowapi`. Authentication endpoints have
tighter limits to mitigate brute-force attacks.

---

## Out of scope

The following are considered out of scope for this project's security model:

- Vulnerabilities in third-party dependencies (report upstream; we will update
  promptly when fixes are available)
- Attacks requiring physical access to the host machine
- Vulnerabilities in the Docker Engine itself
- Issues arising from misconfiguration by the operator (e.g., exposing the API
  port publicly without authentication)
