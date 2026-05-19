"""SnapDock CLI — API-key authenticated, exit-code driven for CI/CD use.

Configuration (env vars or ~/.snapdock/config):
  SNAPDOCK_URL      Daemon base URL (default: http://localhost:8000)
  SNAPDOCK_API_KEY  API key for authentication

Exit codes:
  0  Success
  1  Command failed / server error
  2  Not found / bad arguments
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.table import Table
from rich import print as rprint

console = Console()

# --------------------------------------------------------------------------- #
# Shared client helpers                                                         #
# --------------------------------------------------------------------------- #

def _base_url() -> str:
    return os.getenv("SNAPDOCK_URL", "http://localhost:8000").rstrip("/")


def _headers() -> dict[str, str]:
    key = os.getenv("SNAPDOCK_API_KEY", "")
    if key:
        return {"X-Api-Key": key}
    return {}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_base_url(),
        headers=_headers(),
        timeout=30.0,
    )


def _require_ok(resp: httpx.Response, action: str) -> dict:
    if resp.status_code >= 400:
        console.print(f"[red]ERROR:[/red] {action} failed (HTTP {resp.status_code})")
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        console.print(f"  {detail}")
        sys.exit(1)
    return resp.json()


# --------------------------------------------------------------------------- #
# CLI root                                                                      #
# --------------------------------------------------------------------------- #

@click.group()
@click.version_option("1.0.0")
def cli():
    """SnapDock — Stateful Docker Snapshot Management."""
    pass


# --------------------------------------------------------------------------- #
# status                                                                        #
# --------------------------------------------------------------------------- #

@cli.command()
def status():
    """Show all detected stacks and their health."""
    with _client() as c:
        stacks = _require_ok(c.get("/stacks"), "list stacks")

    table = Table(title="SnapDock — Stack Status")
    table.add_column("Stack", style="cyan")
    table.add_column("Type")
    table.add_column("Health")
    table.add_column("Containers")
    table.add_column("Last Snapshot")
    table.add_column("Scheduled")

    for s in stacks:
        health_color = {"CLEAN": "green", "DEGRADED": "yellow", "BROKEN": "red"}.get(
            s["health_state"], "white"
        )
        table.add_row(
            s["name"],
            s["type"],
            f"[{health_color}]{s['health_state']}[/{health_color}]",
            str(len(s["containers"])),
            s["last_snapshot_at"] or "never",
            "✓" if s["has_schedule"] else "—",
        )

    console.print(table)


# --------------------------------------------------------------------------- #
# snapshot                                                                      #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
@click.option("--label", default=None, help="Human-readable label for this snapshot")
@click.option("--tag", "tags", multiple=True, help="Tags (repeatable)")
@click.option("--confirm", is_flag=True, default=False, help="Confirm DEGRADED/BROKEN snapshot")
@click.option("--watch", is_flag=True, default=False, help="Stream progress via WebSocket")
@click.option("--id-only", is_flag=True, default=False, help="Print only the snapshot ID (CI-friendly)")
def snapshot(stack_name: str, label: str | None, tags: tuple, confirm: bool, watch: bool, id_only: bool):
    """Trigger a snapshot for STACK_NAME."""
    with _client() as c:
        resp = c.post(
            f"/stacks/{stack_name}/snapshots",
            json={"label": label, "tags": list(tags), "confirmed": confirm},
        )
        data = _require_ok(resp, "trigger snapshot")

    if data.get("requires_confirmation"):
        if not id_only:
            console.print(
                f"[yellow]WARNING:[/yellow] Stack is [bold]{data['health_state']}[/bold]. "
                f"Stopped: {data.get('stopped', [])}"
            )
            console.print("Re-run with [bold]--confirm[/bold] to proceed.")
        sys.exit(0)

    snap_id = data.get("snapshot_id") or data.get("id", "")
    if id_only:
        click.echo(snap_id)
    else:
        console.print(f"[green]Snapshot triggered[/green] for {stack_name}")
        if snap_id:
            console.print(f"  ID: {snap_id}")

    if watch:
        _watch_events(stack_name)


# --------------------------------------------------------------------------- #
# wait                                                                          #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
@click.option("--snapshot", "snap_id", default=None, help="Snapshot ID to wait for (default: latest)")
@click.option("--timeout", default=300, show_default=True, help="Seconds before giving up")
@click.option("--interval", default=5, show_default=True, help="Polling interval in seconds")
def wait(stack_name: str, snap_id: str | None, timeout: int, interval: int):
    """Block until the snapshot for STACK_NAME completes. Exits 1 on failure or timeout.

    Useful in CI pipelines where WebSocket streaming is not practical.
    """
    import time

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        with _client() as c:
            snaps = _require_ok(c.get(f"/stacks/{stack_name}/snapshots"), "list snapshots")

        target = None
        if snap_id:
            target = next((s for s in snaps if s["id"] == snap_id), None)
            if target is None:
                console.print(f"[red]ERROR:[/red] Snapshot {snap_id} not found.")
                sys.exit(2)
        else:
            # Wait for the most recent snapshot (complete or incomplete)
            target = snaps[0] if snaps else None

        if target is None:
            console.print(f"[yellow]No snapshots found for {stack_name}, waiting...[/yellow]")
        elif target.get("complete"):
            console.print(f"[green]Snapshot {target['id']} complete.[/green]")
            click.echo(target["id"])
            sys.exit(0)
        elif target.get("error"):
            console.print(f"[red]Snapshot {target['id']} failed:[/red] {target.get('error')}")
            sys.exit(1)
        else:
            elapsed = int(time.monotonic() - (deadline - timeout))
            console.print(
                f"  [dim]waiting for {target['id']} … {elapsed}s / {timeout}s[/dim]"
            )

        time.sleep(interval)

    console.print(f"[red]ERROR:[/red] Timed out after {timeout}s waiting for snapshot.")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# restore                                                                       #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
@click.option("--snapshot", "snap_id", required=True, help="Snapshot ID to restore")
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--confirm", is_flag=True, default=False, help="Confirm data loss")
@click.option("--watch", is_flag=True, default=False)
def restore(stack_name: str, snap_id: str, dry_run: bool, confirm: bool, watch: bool):
    """Restore STACK_NAME from a snapshot."""
    with _client() as c:
        resp = c.post(
            f"/stacks/{stack_name}/snapshots/{snap_id}/restore",
            json={"confirmed": confirm, "dry_run": dry_run},
        )
        data = _require_ok(resp, "trigger restore")

    if data.get("requires_confirmation"):
        console.print(
            f"[yellow]WARNING:[/yellow] This will overwrite ~{data.get('data_loss_window')} "
            f"of data."
        )
        console.print(data.get("message", ""))
        console.print("Re-run with [bold]--confirm[/bold] to proceed.")
        sys.exit(0)

    console.print(
        f"[green]Restore {'(dry-run) ' if dry_run else ''}triggered[/green] for {stack_name} "
        f"from {snap_id}"
    )

    if watch:
        _watch_events(stack_name)


# --------------------------------------------------------------------------- #
# latest                                                                        #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
def latest(stack_name: str):
    """Print the most recent snapshot ID for STACK_NAME."""
    with _client() as c:
        snaps = _require_ok(c.get(f"/stacks/{stack_name}/snapshots"), "list snapshots")

    completed = [s for s in snaps if s.get("complete")]
    if not completed:
        console.print(f"[yellow]No completed snapshots for {stack_name}[/yellow]")
        sys.exit(2)

    # Already ordered by generated_at desc from the API
    click.echo(completed[0]["id"])


# --------------------------------------------------------------------------- #
# history                                                                       #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
def history(stack_name: str):
    """Show snapshot history for STACK_NAME."""
    with _client() as c:
        snaps = _require_ok(c.get(f"/stacks/{stack_name}/snapshots"), "list snapshots")

    table = Table(title=f"Snapshots — {stack_name}")
    table.add_column("ID", style="cyan")
    table.add_column("Time")
    table.add_column("Trigger")
    table.add_column("State")
    table.add_column("Complete")
    table.add_column("Size")
    table.add_column("Label")
    table.add_column("Locked")

    for s in snaps:
        state_color = {"CLEAN": "green", "DEGRADED": "yellow", "BROKEN": "red"}.get(
            s["stack_state"], "white"
        )
        size = f"{s['size_bytes'] // (1024*1024)}MB" if s.get("size_bytes") else "—"
        table.add_row(
            s["id"],
            (s["generated_at"] or "")[:19],
            s["trigger_type"],
            f"[{state_color}]{s['stack_state']}[/{state_color}]",
            "✓" if s["complete"] else "✗",
            size,
            s.get("label") or "—",
            "🔒" if s.get("locked") else "",
        )

    console.print(table)


# --------------------------------------------------------------------------- #
# lock / unlock                                                                 #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
@click.option("--snapshot", "snap_id", required=True)
def lock(stack_name: str, snap_id: str):
    """Lock a snapshot to exempt it from retention cleanup."""
    with _client() as c:
        _require_ok(
            c.patch(f"/stacks/{stack_name}/snapshots/{snap_id}/lock", json={"locked": True}),
            "lock snapshot",
        )
    console.print(f"[green]Locked[/green] {snap_id}")


@cli.command()
@click.argument("stack_name")
@click.option("--snapshot", "snap_id", required=True)
def unlock(stack_name: str, snap_id: str):
    """Unlock a snapshot."""
    with _client() as c:
        _require_ok(
            c.patch(f"/stacks/{stack_name}/snapshots/{snap_id}/lock", json={"locked": False}),
            "unlock snapshot",
        )
    console.print(f"[green]Unlocked[/green] {snap_id}")


# --------------------------------------------------------------------------- #
# schedule                                                                      #
# --------------------------------------------------------------------------- #

@cli.group()
def schedule():
    """Manage snapshot schedules."""
    pass


@schedule.command("set")
@click.argument("stack_name")
@click.option("--cron", required=True, help='5-field cron expression, e.g. "0 2 * * *"')
def schedule_set(stack_name: str, cron: str):
    """Set the snapshot schedule for STACK_NAME."""
    with _client() as c:
        _require_ok(
            c.put(
                f"/stacks/{stack_name}/schedule",
                json={"cron_expression": cron, "is_active": True},
            ),
            "set schedule",
        )
    console.print(f"[green]Schedule set[/green] for {stack_name}: {cron}")


@schedule.command("get")
@click.argument("stack_name")
def schedule_get(stack_name: str):
    """Show the current schedule for STACK_NAME."""
    with _client() as c:
        resp = c.get(f"/stacks/{stack_name}/schedule")
    if resp.status_code == 404 or resp.json() is None:
        console.print(f"No schedule configured for {stack_name}")
        return
    data = _require_ok(resp, "get schedule")
    rprint(data)


# --------------------------------------------------------------------------- #
# diff                                                                          #
# --------------------------------------------------------------------------- #

@cli.command()
@click.argument("stack_name")
@click.option("--from", "from_id", required=True, help="Base snapshot ID")
@click.option("--to", "to_id", required=True, help="New snapshot ID")
def diff(stack_name: str, from_id: str, to_id: str):
    """Compare two snapshots for STACK_NAME."""
    with _client() as c:
        data = _require_ok(
            c.get(f"/stacks/{stack_name}/snapshots/{to_id}/diff", params={"compare_to": from_id}),
            "diff snapshots",
        )

    # Image diff
    image_changes = data.get("image_diff", [])
    if image_changes:
        console.print("\n[bold]Image changes[/bold]")
        for ch in image_changes:
            console.print(f"  {ch['service']}: {ch.get('old_image', '—')} → {ch.get('new_image', '—')}")
    else:
        console.print("[dim]Image diff: no changes[/dim]")

    # Config diff
    config_changes = data.get("config_diff", [])
    if config_changes:
        console.print("\n[bold]Config changes[/bold]")
        for ch in config_changes:
            console.print(f"\n  [cyan]{ch['file']}[/cyan] ({ch['change']})")
            for line in ch["unified_diff"].splitlines()[:40]:
                color = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
                console.print(f"[{color}]{line}[/{color}]")
    else:
        console.print("[dim]Config diff: no changes[/dim]")

    # Volume diff
    vol_changes = data.get("volume_diff", [])
    if vol_changes:
        console.print("\n[bold]Volume changes[/bold]")
        for v in vol_changes:
            change = v.get("change", "?")
            if change in ("unchanged", "skipped"):
                console.print(f"  [dim]{v['volume']}: {change}[/dim]")
            else:
                added = len(v.get("added", []))
                removed = len(v.get("removed", []))
                modified = len(v.get("modified", []))
                console.print(
                    f"  [cyan]{v['volume']}[/cyan]: "
                    f"[green]+{added}[/green] [red]-{removed}[/red] [yellow]~{modified}[/yellow]"
                )


# --------------------------------------------------------------------------- #
# export / import                                                               #
# --------------------------------------------------------------------------- #

@cli.command("export")
@click.argument("stack_name")
@click.option("--snapshot", "snap_id", required=True, help="Snapshot ID to export")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output .tar.gz path")
def export_snapshot(stack_name: str, snap_id: str, output: str):
    """Export a snapshot archive to disk."""
    out_path = Path(output)
    with _client() as c:
        with c.stream("GET", f"/stacks/{stack_name}/snapshots/{snap_id}/export") as resp:
            if resp.status_code >= 400:
                console.print(f"[red]ERROR:[/red] export failed (HTTP {resp.status_code})")
                sys.exit(1)
            with out_path.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    fh.write(chunk)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    console.print(f"[green]Exported[/green] {snap_id} → {out_path} ({size_mb:.1f} MB)")


@cli.command("import")
@click.argument("archive", type=click.Path(exists=True))
def import_snapshot(archive: str):
    """Import a snapshot archive into SnapDock."""
    archive_path = Path(archive)
    with _client() as c:
        with archive_path.open("rb") as fh:
            data = _require_ok(
                c.post(
                    "/snapshots/import",
                    files={"file": (archive_path.name, fh, "application/x-tar")},
                    timeout=120.0,
                ),
                "import snapshot",
            )

    console.print(
        f"[green]Imported[/green] {data['snapshot_id']} "
        f"(stack: {data['stack_name']}, "
        f"size: {data['size_bytes'] // (1024*1024)} MB)"
    )


# --------------------------------------------------------------------------- #
# WebSocket progress watcher                                                    #
# --------------------------------------------------------------------------- #

def _watch_events(stack_name: str) -> None:
    """Block and stream events from the daemon for *stack_name*."""
    import websockets
    import asyncio

    url = _base_url().replace("http://", "ws://").replace("https://", "wss://")
    ws_url = f"{url}/events?stack_name={stack_name}"

    async def _stream():
        key = os.getenv("SNAPDOCK_API_KEY", "")
        extra_headers = {"X-Api-Key": key} if key else {}
        async with websockets.connect(ws_url, additional_headers=extra_headers) as ws:
            async for msg in ws:
                try:
                    event = json.loads(msg)
                except Exception:
                    continue
                if event.get("event_type") == "ping":
                    continue
                status = event.get("status", "")
                msg_text = event.get("message", "")
                color = {
                    "ok": "green",
                    "error": "red",
                    "warning": "yellow",
                    "running": "blue",
                }.get(status, "white")
                ts = event.get("timestamp", "")[:19]
                console.print(f"[dim]{ts}[/dim] [{color}]{msg_text}[/{color}]")
                if event.get("event_type") in (
                    "snapshot.complete",
                    "snapshot.error",
                    "restore.complete",
                    "restore.error",
                ):
                    break

    asyncio.run(_stream())


if __name__ == "__main__":
    cli()
