"""Service lifecycle and explicit native-session attachment."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from aw.mcp.client import (
    DEFAULT_URL, bound_arguments, call, connect, credentials, default_state_dir, service_token,
)
from aw.mcp.store import private_replace, private_write, state_token

app = typer.Typer(no_args_is_help=True, help="Shared local MCP mailbox and native session receivers.")


def state_path(value: Path | None) -> Path:
    return (value or default_state_dir()).expanduser().resolve()


def report_error(action):
    try:
        return action()
    except Exception as error:
        while isinstance(error, ExceptionGroup) and len(error.exceptions) == 1:
            error = error.exceptions[0]
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error


@app.command()
def serve(
    state_dir: Path | None = typer.Option(None, help="Private shared state outside Git worktrees."),
    port: int = typer.Option(8765, min=1, max=65535),
) -> None:
    """Run one authenticated HTTP service on 127.0.0.1."""
    def run():
        import uvicorn
        from aw.mcp.server import create_app

        path = state_path(state_dir)
        application = create_app(path)
        typer.echo(f"AW MCP: http://127.0.0.1:{port}/mcp", err=True)
        typer.echo(f"Private service token: {path / 'token'}", err=True)
        uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    report_error(run)


@app.command("token")
def token_command(state_dir: Path | None = typer.Option(None)) -> None:
    """Print the service credential for explicit client configuration."""
    report_error(lambda: typer.echo(state_token(state_path(state_dir))))


@app.command("headers")
def headers_command(state_dir: Path | None = typer.Option(None)) -> None:
    """Print JSON authentication headers for Codex's http_headers_helper."""
    report_error(lambda: typer.echo(json.dumps({
        "Authorization": f"Bearer {service_token(state_path(state_dir))}",
    })))


@app.command()
def attach(
    client: str = typer.Option(..., help="codex, claude-code, or agy."),
    native_id: str = typer.Option(..., help="Exact native thread or conversation ID."),
    worktree: Path = typer.Option(..., exists=True, file_okay=False),
    output: Path = typer.Option(..., help="Private session credential file; existing files are reused."),
    project: list[str] = typer.Option(..., help="Repeat for each project segment, such as faberline then aw."),
    session_name: str = typer.Option(..., "--session-name", "--label", help="Describe what this session does."),
    scope_description: str = typer.Option(..., help="What this session is responsible for."),
    scope_path: list[str] = typer.Option([], help="Repeat for each repository-relative path or glob."),
    url: str = typer.Option(DEFAULT_URL),
    state_dir: Path | None = typer.Option(None),
) -> None:
    """Check and register a scoped native session, or reconnect its private identity."""
    async def run():
        from aw.mcp.store import git_identity

        path = output.expanduser().absolute()
        if not path.parent.is_dir():
            raise ValueError("session output parent must already exist")
        async with connect(url, service_token(state_path(state_dir))) as remote:
            inputs = {"project": project, "session_name": session_name,
                      "scope": {"description": scope_description, "paths": scope_path}, "worktree": str(worktree)}
            if path.exists() or path.is_symlink():
                saved = credentials(path)
                if (saved["client"], saved["native_id"], saved["worktree"]) != (
                    client, native_id, git_identity(str(worktree))["worktree"],
                ):
                    raise ValueError("session file belongs to a different native session or worktree")
                checked = await call(remote, "preflight", {**inputs, **bound_arguments(saved)})
                desired = checked["proposed"]
                if not saved.get("ready") or any(saved.get(field) != desired[field] for field in desired):
                    updated = await call(remote, "update_session", {
                        **bound_arguments(saved), "preflight_id": checked["preflight_id"],
                    })
                    saved = {**updated, "session_token": saved["session_token"]}
                    private_replace(path, json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
                else:
                    await call(remote, "heartbeat", bound_arguments(saved))
            else:
                checked = await call(remote, "preflight", inputs)
                saved = await call(remote, "register_session", {
                    "preflight_id": checked["preflight_id"], "client": client, "native_id": native_id,
                })
                private_write(path, json.dumps(saved, ensure_ascii=False, indent=2) + "\n")
            typer.echo(f"session_id: {saved['id']}\nsession_file: {path}")
    report_error(lambda: asyncio.run(run()))


@app.command()
def proxy(
    session_file: Path | None = typer.Option(None, exists=True, dir_okay=False),
    claude_channel: bool = typer.Option(False, help="Emit Claude channel notifications over stdio."),
    url: str = typer.Option(DEFAULT_URL),
    state_dir: Path | None = typer.Option(None),
) -> None:
    """Run a thin stdio proxy; the shared HTTP service must already run."""
    def run():
        from aw.mcp.proxy import run_proxy

        asyncio.run(run_proxy(url, service_token(state_path(state_dir)),
                              credentials(session_file) if session_file is not None else None, claude_channel,
                              state_path(state_dir), session_file))
    report_error(run)


@app.command()
def install(
    client: str = typer.Option(..., help="codex, claude-code, agy, or all."),
    scope: str = typer.Option("user", help="user shares across repos; project uses one worktree."),
    worktree: Path | None = typer.Option(None, exists=True, file_okay=False),
    config_file: Path | None = typer.Option(None, help="Override one client's config path."),
    replace: bool = typer.Option(False, help="Replace an existing aw entry; keep other settings."),
    dry_run: bool = typer.Option(False, help="Show only the proposed aw entry; write no files."),
    url: str = typer.Option(DEFAULT_URL),
    state_dir: Path | None = typer.Option(None),
) -> None:
    """Install aw in each client's native config. Back up existing files before writing."""
    def run():
        from aw.mcp.install import apply, clients, config_path, entry, prepare

        targets = clients(client)
        if config_file is not None and len(targets) != 1:
            raise ValueError("--config-file requires one client")
        root = worktree or (Path.cwd() if scope == "project" else None)
        # Validate every target before writing any client configuration.
        plans = [prepare(target, config_path(target, scope, root, config_file),
                         entry(target, url, state_path(state_dir)), replace) for target in targets]
        for plan in plans:
            if dry_run:
                typer.echo(f"{plan.client}: {'would update' if plan.changed else 'unchanged'} {plan.path}")
                typer.echo(json.dumps({"aw": plan.entry}, ensure_ascii=False, indent=2))
                continue
            backup = apply(plan)
            typer.echo(f"{plan.client}: {'installed' if plan.changed else 'unchanged'} {plan.path}")
            if backup is not None:
                typer.echo(f"backup: {backup}")
        if not dry_run:
            typer.echo("Reload MCP in each native client. Run aw mcp doctor to check configuration and service.")
    report_error(run)


@app.command()
def install_rules(
    client: str = typer.Option("all", help="codex, claude-code, agy, or all."),
    dry_run: bool = typer.Option(False, help="Show the AW rule and target paths; write nothing."),
) -> None:
    """Add global AW guidance to native user instructions, with exact private backups."""
    def run():
        from aw.mcp.install import apply, clients
        from aw.mcp.rules import GUIDANCE, current_rule_paths, prepare_rules

        paths = current_rule_paths()
        plans = [prepare_rules(target, paths[target]) for target in clients(client)]
        for plan in plans:
            if dry_run:
                typer.echo(f"{plan.client}: {'would update' if plan.changed else 'unchanged'} {plan.path}")
                continue
            backup = apply(plan)
            typer.echo(f"{plan.client}: {'installed' if plan.changed else 'unchanged'} {plan.path}")
            if backup is not None:
                typer.echo(f"backup: {backup}")
        if dry_run:
            typer.echo(GUIDANCE)
        else:
            typer.echo("Start a new native session to load the rules. Run aw mcp doctor --rules to check files.")
    report_error(run)


@app.command()
def doctor(
    client: str = typer.Option("all", help="codex, claude-code, agy, or all."),
    scope: str = typer.Option("user"),
    worktree: Path | None = typer.Option(None, exists=True, file_okay=False),
    config_file: Path | None = typer.Option(None),
    url: str | None = typer.Option(None, help="Default: service URL from the installed entry."),
    state_dir: Path | None = typer.Option(None),
    rules: bool = typer.Option(False, help="Also check global AW session instructions."),
) -> None:
    """Check config entries and authenticated service health without starting an agent."""
    def run():
        import httpx2
        from aw.mcp.client import base_url
        from aw.mcp.install import clients, config_path, connection, inspect

        targets = clients(client)
        if config_file is not None and len(targets) != 1:
            raise ValueError("--config-file requires one client")
        root = worktree or (Path.cwd() if scope == "project" else None)
        ready = True
        connections = set()
        for target in targets:
            path = config_path(target, scope, root, config_file)
            try:
                status = inspect(target, path)
                if status == "configured":
                    connections.add(connection(target, path))
            except (OSError, ValueError):
                status = "unreadable or invalid config"
            typer.echo(f"{target}: {status} ({path})")
            ready = ready and status == "configured"
        if rules:
            from aw.mcp.rules import current_rule_paths, inspect_rules

            paths = current_rule_paths()
            for target in targets:
                try:
                    status = inspect_rules(target, paths[target])
                except (OSError, ValueError):
                    status = "unreadable or conflicting rules"
                typer.echo(f"{target} rules: {status} ({paths[target]})")
                ready = ready and status == "configured"
        if len(connections) > 1:
            typer.echo("configuration: selected clients point to different AW services")
            ready = False
        configured_url, configured_state = next(iter(connections), (DEFAULT_URL, state_path(None)))
        try:
            response = httpx2.get(base_url(url or configured_url) + "/health", timeout=3, headers={
                "Authorization": f"Bearer {service_token(state_path(state_dir) if state_dir else configured_state)}",
            })
            response.raise_for_status()
            healthy = response.json().get("name") == "aw"
        except (OSError, ValueError, httpx2.HTTPError):
            healthy = False
        typer.echo(f"service: {'available' if healthy else 'unavailable'}")
        typer.echo("Configured does not prove that a native client loaded the tools or received a notification.")
        return ready and healthy

    if not report_error(run):
        raise typer.Exit(1)


@app.command()
def receive(
    session_file: Path = typer.Option(..., exists=True, dir_okay=False),
    codex_socket: Path | None = typer.Option(None, help="Socket of the existing target Codex App Server."),
    codex_binary: str = typer.Option("codex"),
    agentapi_binary: str = typer.Option("agentapi", help="AGY's official desktop sidecar executable."),
    url: str = typer.Option(DEFAULT_URL),
    state_dir: Path | None = typer.Option(None),
) -> None:
    """Push inbox events into an existing Codex thread or AGY desktop conversation."""
    async def run():
        from aw.mcp.receivers import AgyReceiver, CodexReceiver, receive_forever

        saved, token = credentials(session_file), service_token(state_path(state_dir))
        async with connect(url, token) as remote:
            current = await call(remote, "heartbeat", bound_arguments(saved))
            saved = {**current, "session_token": saved["session_token"]}
        if not saved["native_id"]:
            raise ValueError("active delivery requires the exact native thread or conversation ID")
        if saved["client"] == "codex":
            if codex_socket is None:
                raise ValueError("--codex-socket is required; use the target App Server's socket")
            async with CodexReceiver(saved, codex_socket, codex_binary) as receiver:
                await receive_forever(url, token, saved, receiver)
        elif saved["client"] == "agy":
            await receive_forever(url, token, saved, AgyReceiver(saved, agentapi_binary))
        else:
            raise ValueError("Claude Code uses aw mcp proxy --claude-channel")
    report_error(lambda: asyncio.run(run()))
