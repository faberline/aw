"""Thin stdio-to-HTTP MCP proxy, optionally a Claude Code channel receiver."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

from aw import __version__
from aw.mcp.client import bound_arguments, call, connect, default_state_dir, peer_text
from aw.mcp.receivers import receive_forever
from aw.mcp.server import INSTRUCTIONS
from aw.mcp.store import private_replace, private_write

BOOTSTRAP_TOOLS = {"preflight", "register_session"}
ACTOR_FIELDS = {"session_id", "session_token"}
HEARTBEAT_SECONDS = 30


class Remote:
    """Open one HTTP MCP connection per call, so a service restart never ends the proxy."""

    def __init__(self, url: str, token: str):
        self.url, self.token = url, token

    async def call_tool(self, name: str, arguments: dict):
        try:
            async with connect(self.url, self.token) as client:
                return await client.call_tool(name, arguments)
        except Exception as error:
            raise ValueError("AW service is unavailable; retry when it is running again") from error


class ChannelReceiver:
    def __init__(self):
        self.ready = asyncio.Event()
        self.session = None

    async def send(self, message: dict) -> str:
        await self.ready.wait()
        await self.session.send_notification(types.Notification[dict[str, Any], str](
            method="notifications/claude/channel",
            params={"content": peer_text(message), "meta": {
                "message_id": message["id"], "task_id": message["task_id"],
                "sender": message["sender"], "kind": message["kind"],
            }},
        ))
        return "written to Claude channel transport; agent acknowledgment pending"


async def run_proxy(url: str, token: str, session: dict | None = None, claude_channel: bool = False,
                    state_dir: Path | None = None, session_path: Path | None = None) -> None:
    if claude_channel and (session is None or session["client"] != "claude-code"):
        raise ValueError("--claude-channel requires a claude-code session file")
    async with connect(url, token) as remote:
        ready = False
        if session is not None:
            checked = await remote.call_tool("heartbeat", bound_arguments(session))
            if checked.is_error:
                detail = " ".join(getattr(item, "text", "") for item in checked.content)
                if "session needs project and scope" not in detail:
                    raise ValueError("proxy session credentials were rejected")
            else:
                session = {**checked.structured_content, "session_token": session["session_token"]}
                ready = True
        listed = await remote.list_tools()
    originals = {tool.name: tool for tool in listed.tools}
    if not BOOTSTRAP_TOOLS | {"update_session"} <= originals.keys():
        raise ValueError("AW service needs the session preflight update")
    remote = Remote(url, token)

    def visible_tools():
        tools = {}
        for name, original in originals.items():
            if not ready and name not in BOOTSTRAP_TOOLS:
                continue
            tool = original.model_copy(deep=True)
            properties = tool.input_schema.get("properties", {})
            for field in ACTOR_FIELDS:
                properties.pop(field, None)
            tool.input_schema["required"] = [
                field for field in tool.input_schema.get("required", [])
                if field not in ACTOR_FIELDS
            ]
            tools[name] = tool
        return tools

    channel = ChannelReceiver()
    issued = set()
    registration = None
    registration_arguments = None
    actor_lock = asyncio.Lock()

    def local_result(data):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
            structured_content=data,
        )

    def public_registration():
        return {key: value for key, value in registration.items() if key != "session_token"} | {
            "session_file": str(session_path),
        }

    async def list_tools(context, params):
        if claude_channel:
            channel.session = context.session
            channel.ready.set()
        return types.ListToolsResult(tools=list(visible_tools().values()))

    async def call_tool(context, params):
        nonlocal session, ready, registration, registration_arguments, session_path
        async with actor_lock:
            arguments = params.arguments or {}
            try:
                if params.name not in visible_tools() or ACTOR_FIELDS & arguments.keys():
                    raise ValueError("registration required, unknown tool or attempted session override")
                if params.name == "register_session" and ready:
                    if registration is not None and arguments == registration_arguments:
                        return local_result(public_registration())
                    raise ValueError("this proxy already has a session; use preflight then update_session")
                if params.name in {"register_session", "update_session"}:
                    if arguments.get("preflight_id") not in issued:
                        raise ValueError("run preflight in this proxy before registration or update")
                actor = bound_arguments(session) if session is not None else {}
                if params.name == "register_session":
                    if session is not None:
                        for field in ("client", "native_id"):
                            if arguments.get(field, session[field]) != session[field]:
                                raise ValueError("a bound proxy cannot change its native identity")
                        data = await call(remote, "update_session", {
                            **actor, "preflight_id": arguments["preflight_id"],
                        })
                        data["session_token"] = session["session_token"]
                        private_replace(session_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                    else:
                        data = await call(remote, params.name, arguments)
                        directory = (state_dir or default_state_dir()) / "sessions"
                        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                        info = directory.stat()
                        if directory.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
                            raise ValueError("session directory must be owned and mode 0700")
                        session_path = directory / (data["id"] + ".json")
                        private_write(session_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                    session, registration, registration_arguments = data, data, arguments.copy()
                    ready = True
                    issued.clear()
                    await context.session.send_tool_list_changed()
                    return local_result(public_registration())
                result = await remote.call_tool(params.name, {**arguments, **actor})
                if not result.is_error:
                    if params.name == "preflight":
                        issued.add(result.structured_content["preflight_id"])
                    elif params.name == "update_session":
                        session = {**result.structured_content, "session_token": session["session_token"]}
                        private_replace(session_path, json.dumps(session, ensure_ascii=False, indent=2) + "\n")
                        issued.clear()
                return types.CallToolResult(
                    content=result.content, structured_content=result.structured_content, is_error=result.is_error,
                )
            except ValueError as error:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=str(error))], is_error=True,
                )

    async def auto_heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with actor_lock:
                if ready:
                    try:
                        await remote.call_tool("heartbeat", bound_arguments(session))
                    except ValueError as error:
                        print(f"AW heartbeat failed: {error}", file=sys.stderr)

    proxy = Server(
        "aw", version=__version__, instructions=INSTRUCTIONS,
        on_list_tools=list_tools, on_call_tool=call_tool,
        get_tool_input_schema=lambda name: visible_tools().get(name).input_schema
        if name in visible_tools() else None,
    )
    options = proxy.create_initialization_options(
        notification_options=NotificationOptions(tools_changed=True),
        experimental_capabilities={"claude/channel": {}} if claude_channel else None,
    )
    async with stdio_server() as (incoming, outgoing):
        async with anyio.create_task_group() as group:
            group.start_soon(auto_heartbeat)
            if claude_channel:
                # Claude Channels require a handshake-era protocol, even when
                # the central HTTP service speaks the current stateless protocol.
                # Restrict only this bridge, without changing SDK globals.
                forwarded, reader = anyio.create_memory_object_stream(0)

                async def legacy_only():
                    async with forwarded:
                        async for message in incoming:
                            rpc = message.message
                            if isinstance(rpc, types.JSONRPCRequest) and rpc.method == "server/discover":
                                await outgoing.send(SessionMessage(types.JSONRPCError(
                                    jsonrpc="2.0", id=rpc.id,
                                    error=types.ErrorData(code=-32601, message="Claude channel bridge uses initialize"),
                                )))
                                continue
                            if isinstance(rpc, types.JSONRPCRequest) and rpc.method == "initialize":
                                rpc = rpc.model_copy(update={
                                    "params": {**rpc.params, "protocolVersion": "2025-11-25"},
                                })
                                message = SessionMessage(rpc, metadata=message.metadata)
                            await forwarded.send(message)

                group.start_soon(legacy_only)
                group.start_soon(receive_forever, url, token, session, channel)
                await proxy.run(reader, outgoing, options)
            else:
                await proxy.run(incoming, outgoing, options)
            group.cancel_scope.cancel()
