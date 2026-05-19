"""FastAPI application factory and daemon startup."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from snapdock.api import auth, audit, coverage, restore, schedule, settings as settings_api, setup as setup_api, snapshots, stacks, users
from snapdock.api import diff as diff_api
from snapdock.api import transfer as transfer_api
from snapdock.api.ws import router as ws_router
from snapdock.config import settings
from snapdock.database import AuditLog, RevokedToken, Schedule, SessionLocal, Snapshot, SystemConfig, User, init_db
from snapdock.docker_client import get_docker_client
from snapdock.events import event_bus
from snapdock.limiter import limiter
from snapdock.scheduler import start_scheduler, stop_scheduler, get_scheduler

# --------------------------------------------------------------------------- #
# Logging                                                                       #
# --------------------------------------------------------------------------- #

if settings.debug:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
else:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer() if not settings.debug else structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)


# --------------------------------------------------------------------------- #
# Startup / shutdown                                                            #
# --------------------------------------------------------------------------- #

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SnapDock daemon starting…")

    # Ensure storage and state directories exist
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    Path("/var/lib/snapdock/state/pending_restart").mkdir(parents=True, exist_ok=True)
    Path("/var/lib/snapdock/state/pending_restore").mkdir(parents=True, exist_ok=True)

    # Initialise database
    init_db()
    _handle_first_boot()

    # Capture the running event loop so thread-based code can publish events
    event_bus._loop = asyncio.get_running_loop()

    # Verify Docker connection
    try:
        get_docker_client()
    except Exception as exc:
        logger.error("Cannot connect to Docker: %s", exc)

    # Crash recovery: check for pending_restart or pending_restore flags
    await _crash_recovery()

    # Start APScheduler and reload persisted schedules
    start_scheduler()
    _reload_schedules()

    # Start Docker event watcher background task
    watcher_task = asyncio.create_task(_docker_event_watcher())
    cleanup_task = asyncio.create_task(_cleanup_revoked_tokens())

    logger.info("SnapDock daemon ready on %s:%d", settings.host, settings.port)
    yield

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass
    stop_scheduler()
    logger.info("SnapDock daemon stopped")


# --------------------------------------------------------------------------- #
# Application                                                                   #
# --------------------------------------------------------------------------- #

def create_app() -> FastAPI:
    app = FastAPI(
        title="SnapDock",
        description="Stateful Docker Snapshot Management",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    app.include_router(setup_api.router)
    app.include_router(auth.router)
    app.include_router(settings_api.router)
    app.include_router(schedule.router)   # must precede stacks (avoids /stacks/{name} swallowing /stacks/schedules)
    app.include_router(stacks.router)
    app.include_router(snapshots.router)
    app.include_router(restore.router)
    app.include_router(audit.router)
    app.include_router(coverage.router)
    app.include_router(users.router)
    app.include_router(diff_api.router)
    app.include_router(transfer_api.router)
    app.include_router(ws_router)

    @app.get("/health")
    def health_check():
        return {"status": "ok"}

    @app.get("/healthz")
    def healthz():
        """Liveness/readiness probe: checks DB connection."""
        import sqlalchemy as sa
        from snapdock.database import engine
        try:
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
        except Exception as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail=f"DB unavailable: {exc}")
        return {"status": "ok"}

    return app


app = create_app()


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #

def _handle_first_boot() -> None:
    """Handle first-boot state:

    * If users already exist (e.g. existing install), mark setup as complete
      so the onboarding flow is skipped.
    * If no users exist, generate a one-time setup token, hash it, store the
      hash in the DB, and print the plaintext token to the log.
    """
    import hashlib
    import secrets as _secrets

    db = SessionLocal()
    try:
        setup_done = db.query(SystemConfig).filter_by(key="setup_complete").first()

        # ── Existing install — ensure setup is marked complete ─────────────
        if db.query(User).count() > 0:
            if setup_done is None:
                db.add(SystemConfig(key="setup_complete", value="true"))
                db.commit()
            elif setup_done.value != "true":
                setup_done.value = "true"
                db.commit()
            # Also remove any stale token
            stale = db.query(SystemConfig).filter_by(key="setup_token_hash").first()
            if stale:
                db.delete(stale)
                db.commit()
            return

        # ── Fresh install — setup already finished in a previous boot ──────
        if setup_done and setup_done.value == "true":
            return

        # ── Fresh install — generate or refresh setup token ────────────────
        plaintext_token = _secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(plaintext_token.encode()).hexdigest()

        existing_token = db.query(SystemConfig).filter_by(key="setup_token_hash").first()
        if existing_token:
            existing_token.value = token_hash
        else:
            db.add(SystemConfig(key="setup_token_hash", value=token_hash))
        db.commit()

        # Print prominently so the operator can read it from docker logs
        sep = "=" * 64
        logger.warning("\n%s\n  SNAPDOCK FIRST-BOOT SETUP TOKEN\n\n  %s\n\n  Open http://localhost:%d/setup and enter this token.\n  It is valid for ONE use only.\n%s",
                       sep, plaintext_token, settings.port, sep)
    finally:
        db.close()


async def _cleanup_revoked_tokens() -> None:
    """Hourly background task: purge expired revoked-token records."""
    while True:
        try:
            await asyncio.sleep(3600)
            with SessionLocal() as db:
                db.query(RevokedToken).filter(RevokedToken.expires_at < datetime.utcnow()).delete()
                db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Revoked-token cleanup error: %s", exc)


async def _crash_recovery() -> None:
    """Restart any stacks that have a pending_restart flag from a previous crash."""
    from snapdock.core.classifier import ContainerClassifier

    pending_dir = Path("/var/lib/snapdock/state/pending_restart")
    if not pending_dir.exists():
        return

    flags = list(pending_dir.iterdir())
    if not flags:
        return

    try:
        docker = get_docker_client()
    except Exception:
        logger.warning("Crash recovery skipped: Docker unavailable")
        return

    classifier = ContainerClassifier(docker)

    for flag_path in flags:
        stack_name = flag_path.name
        logger.warning(
            "Crash recovery: found pending_restart for '%s' — attempting restart",
            stack_name,
        )
        stack = await asyncio.to_thread(classifier.get_stack, stack_name)
        if stack:
            for container in stack.containers:
                try:
                    container.start()
                    logger.info("Crash recovery: started %s", container.name)
                except Exception as exc:
                    logger.warning("Crash recovery: could not start %s: %s", container.name, exc)
        flag_path.unlink(missing_ok=True)

    # Flag incomplete snapshots
    db = SessionLocal()
    try:
        incomplete = db.query(Snapshot).filter_by(complete=False).all()
        for snap in incomplete:
            logger.warning(
                "Incomplete snapshot detected: %s for stack '%s' — flagged in DB",
                snap.id,
                snap.stack_name,
            )
    finally:
        db.close()


def _reload_schedules() -> None:
    """Re-register APScheduler jobs for all active DB schedules on startup."""
    from snapdock.api.schedule import _register_schedule_job

    db = SessionLocal()
    try:
        schedules = db.query(Schedule).filter_by(is_active=True).all()
        for sched in schedules:
            try:
                _register_schedule_job(
                    sched.stack_name, sched.cron_expression, sched.is_active
                )
                logger.info(
                    "Reloaded schedule for '%s': %s",
                    sched.stack_name,
                    sched.cron_expression,
                )
            except Exception as exc:
                logger.warning(
                    "Could not reload schedule for '%s': %s", sched.stack_name, exc
                )
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Docker event watcher                                                          #
# --------------------------------------------------------------------------- #

# Events that invalidate the in-memory classifier cache
_INVALIDATING_EVENTS = frozenset({
    "start", "die", "stop", "kill", "destroy",
    "create", "rename", "pause", "unpause",
})


async def _docker_event_watcher() -> None:
    """Stream Docker events and publish SnapDock notifications for relevant ones."""
    logger.info("Docker event watcher started")
    while True:
        try:
            docker = get_docker_client()
            # run the blocking generator in a thread
            await asyncio.to_thread(_stream_docker_events, docker)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Docker event watcher error: %s — retrying in 5s", exc)
            await asyncio.sleep(5)


def _stream_docker_events(docker) -> None:
    """Blocking loop — runs inside asyncio.to_thread()."""
    for raw in docker.events(decode=True):
        event_type = raw.get("Type", "")
        action = raw.get("Action", "")
        actor = raw.get("Actor", {})
        container_name = actor.get("Attributes", {}).get("name", "")
        stack_label = actor.get("Attributes", {}).get(
            "com.docker.compose.project", ""
        )

        if event_type != "container" or action not in _INVALIDATING_EVENTS:
            continue

        logger.debug(
            "Docker event: %s %s (stack=%s)", action, container_name, stack_label or "—"
        )

        # Publish to event bus so WebSocket clients see container lifecycle changes
        from snapdock.events import SnapDockEvent, event_bus
        event_bus.publish_sync(
            SnapDockEvent(
                event_type=f"container.{action}",
                stack_name=stack_label or None,
                message=f"{container_name} {action}",
            )
        )


# --------------------------------------------------------------------------- #
# Entry point                                                                   #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    uvicorn.run(
        "snapdock.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )
