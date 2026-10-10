"""Native delivery: Codex App Server and the Antigravity desktop sidecar."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path

from aw import __version__
from aw.mcp.client import bound_arguments, call, connect, events, peer_text, receipt

RETRY_SECONDS = (3, 300)  # first delay and cap for a failed native delivery


class CodexReceiver:
    """Connect to an existing App Server; never start or replace an agent."""

    def __init__(self, session: dict, socket_path: Path, executable: str = "codex"):
        self.session, self.socket_path, self.executable = session, socket_path, executable
        self.process = None
        self.reader = None
        self.stderr_reader = None
        self.pending = {}
        self.counter = 0

    async def __aenter__(self):
        info = self.socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("Codex socket must be an owned Unix socket")
        self.process = await asyncio.create_subprocess_exec(
            self.executable, "app-server", "proxy", "--sock", str(self.socket_path),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        self.reader = asyncio.create_task(self._read())
        self.stderr_reader = asyncio.create_task(self._discard_stderr())
        try:
            await self.rpc("initialize", {
                "clientInfo": {"name": "aw", "version": __version__},
                "capabilities": {"experimentalApi": True},
            })
            await self._write({"method": "initialized", "params": {}})
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def _write(self, data: dict):
        self.process.stdin.write((json.dumps(data) + "\n").encode())
        await self.process.stdin.drain()

    async def _discard_stderr(self):
        while await self.process.stderr.read(4096):
            pass

    async def _read(self):
        error = ValueError("Codex App Server connection closed")
        try:
            while line := await self.process.stdout.readline():
                data = json.loads(line)
                if "method" in data:
                    # Approval requests belong to the native UI. Never approve them here.
                    if "id" in data:
                        print("AW: Codex requires action in its native client.", file=sys.stderr)
                    continue
                future = self.pending.get(data.get("id"))
                if future is not None and not future.done():
                    if "error" in data:
                        future.set_exception(ValueError(f"Codex RPC rejected: {data['error'].get('message', 'error')}"))
                    else:
                        future.set_result(data["result"])
        except (ValueError, OSError) as caught:
            error = caught
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)

    async def rpc(self, method: str, params: dict):
        if self.reader is not None and self.reader.done():
            raise ValueError("Codex App Server connection closed")
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=15)
        except (OSError, TimeoutError):
            await self.__aexit__(None, None, None)
            raise
        finally:
            self.pending.pop(request_id, None)

    async def send(self, message: dict) -> str:
        if self.reader is None or self.reader.done() or self.process.returncode is not None:
            await self.__aexit__(None, None, None)
            await self.__aenter__()
        native_id = self.session["native_id"]
        cursor, found, seen = None, False, set()
        while True:
            loaded = await self.rpc("thread/loaded/list", {"cursor": cursor, "limit": 100})
            if native_id in loaded.get("data", []):
                found = True
                break
            cursor = loaded.get("nextCursor")
            if cursor is None:
                break
            if cursor in seen or len(seen) >= 100:
                raise ValueError("Codex loaded-thread pagination did not finish")
            seen.add(cursor)
        if not found:
            raise ValueError("target Codex thread is not loaded; open it in its native client")
        result = await self.rpc("thread/read", {"threadId": native_id, "includeTurns": False})
        thread = result["thread"]
        if Path(thread["cwd"]).resolve() != Path(self.session["worktree"]).resolve():
            raise ValueError("Codex thread cwd does not match the registered worktree")
        active = None
        if thread.get("status", {}).get("type") == "active":
            recent = await self.rpc("thread/turns/list", {
                "threadId": native_id, "limit": 1, "itemsView": "notLoaded", "sortDirection": "desc",
            })
            active = next((turn for turn in recent["data"] if turn["status"] == "inProgress"), None)
        params = {"threadId": native_id, "input": [{"type": "text", "text": peer_text(message)}]}
        if active is not None:
            params["expectedTurnId"] = active["id"]
            accepted = await self.rpc("turn/steer", params)
            return f"accepted by turn/steer: {accepted['turnId']}"
        accepted = await self.rpc("turn/start", params)
        return f"accepted by turn/start: {accepted['turn']['id']}"

    async def __aexit__(self, *args):
        for task in (self.reader, self.stderr_reader):
            if task is not None:
                task.cancel()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self.reader, self.stderr_reader):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)


class AgyReceiver:
    """Run only the official sidecar's existing-conversation send command."""

    def __init__(self, session: dict, executable: str = "agentapi"):
        self.session, self.executable = session, executable

    async def send(self, message: dict) -> str:
        process = await asyncio.create_subprocess_exec(
            self.executable, "send-message", self.session["native_id"], peer_text(message),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode != 0:
            raise ValueError(f"AGY agentapi refused delivery (exit {process.returncode})")
        return "agentapi send-message returned success; agent acknowledgment pending"


async def receive_forever(url: str, token: str, session: dict, receiver) -> None:
    """Replay unacknowledged mail. A failed message waits with backoff and never blocks later mail."""
    loop = asyncio.get_running_loop()
    retries = {}  # message ID -> (failed attempts, loop time of the next attempt)
    while True:
        try:
            opened, seen = loop.time(), set()
            stream = events(url, token, session)
            try:
                while True:
                    # Reopen the replaying stream when the next failed message is due.
                    due = min((at for _, at in retries.values() if at > opened), default=None)
                    try:
                        async with asyncio.timeout_at(due) as deadline:
                            message = await anext(stream)
                    except TimeoutError:
                        if deadline.expired():
                            break
                        raise
                    seen.add(message["id"])
                    attempts, at = retries.get(message["id"], (0, 0))
                    if message["delivery"] == "forwarded" or at > loop.time():
                        continue
                    async with connect(url, token) as remote:
                        current = await call(remote, "heartbeat", bound_arguments(session))
                    try:
                        if any(current[field] != session[field] for field in ("client", "native_id", "worktree")):
                            raise ValueError("receiver binding changed; restart it with the updated session file")
                        detail = await receiver.send(message)
                    except Exception as error:
                        first, cap = RETRY_SECONDS
                        retries[message["id"]] = (attempts + 1, loop.time() + min(first * 2 ** attempts, cap))
                        await receipt(url, token, session, message, "failed", str(error)[:1024])
                        continue
                    retries.pop(message["id"], None)
                    await receipt(url, token, session, message, "forwarded", detail)
            finally:
                await stream.aclose()
            # The replay holds all pending mail; forget backoff for mail that left the inbox.
            retries = {key: value for key, value in retries.items() if key in seen or value[1] > opened}
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"AW receiver will retry: {type(error).__name__}", file=sys.stderr)
            await asyncio.sleep(3)
