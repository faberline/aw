"""Real HTTP/stdio coordination and native delivery, with no paid model calls."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx2
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from aw.mcp.client import bound_arguments, call, connect, events
from aw.mcp.receivers import AgyReceiver, CodexReceiver, receive_forever
from aw.mcp.store import Mailbox, private_read, private_write, state_token

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def running_hub(state: Path):
    token = state_token(state)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "aw.main", "mcp", "serve", "--state-dir", str(state), "--port", str(port)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        with httpx2.Client(timeout=0.3) as http:
            for _ in range(150):
                if process.poll() is not None:
                    raise AssertionError(process.communicate()[1].decode())
                try:
                    if http.get(base + "/health", headers={"Authorization": f"Bearer {token}"}).status_code == 200:
                        break
                except httpx2.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("AW HTTP service did not start")
        yield SimpleNamespace(url=base + "/mcp", base=base, token=token, state=state)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.communicate()


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    with running_hub(tmp_path_factory.mktemp("aw-mail") / "state") as value:
        yield value


@pytest.fixture
def repos(tmp_path):
    paths = [tmp_path / "repo-a", tmp_path / "repo-b"]
    for path in paths:
        subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
        subprocess.run([
            "git", "-C", str(path), "-c", "user.name=AW tests", "-c", "user.email=aw@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "commit", "-q", "--allow-empty", "-m", "fixture",
        ], check=True)
    linked = tmp_path / "linked-a"
    subprocess.run(["git", "-C", str(paths[0]), "worktree", "add", "-q", "-b", "linked", str(linked)], check=True)
    return *paths, linked


async def register(remote, client: str, path: Path):
    checked = await call(remote, "preflight", {
        "project": ["tests", "aw"], "session_name": client + " fixture " + str(uuid4()),
        "scope": {"description": "MCP transport tests", "paths": ["src/**"]}, "worktree": str(path),
    })
    return await call(remote, "register_session", {
        "client": client, "native_id": str(uuid4()), "preflight_id": checked["preflight_id"],
    })


def test_cli_attachment_reuses_one_private_identity(hub, repos, tmp_path):
    path = tmp_path / "session.json"
    command = [
        sys.executable, "-m", "aw.main", "mcp", "attach", "--client", "codex",
        "--native-id", "cli-" + str(uuid4()), "--worktree", str(repos[0]),
        "--output", str(path), "--url", hub.url,
        "--project", "tests", "--project", "aw", "--session-name", "CLI attachment test",
        "--scope-description", "MCP attachment", "--scope-path", "src/**",
    ]
    environment = {**os.environ, "AW_MCP_TOKEN": hub.token}
    first = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    saved = json.loads(private_read(path))
    assert path.stat().st_mode & 0o777 == 0o600
    assert saved["session_token"] not in first.stdout + first.stderr
    second = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(private_read(path)) == saved
    bad = [*command]
    bad[bad.index("--native-id") + 1] = "another-thread"
    refusal = subprocess.run(bad, cwd=ROOT, env=environment, capture_output=True, text=True)
    assert refusal.returncode == 2
    assert "different native session" in refusal.stderr
    headers = subprocess.run(
        [sys.executable, "-m", "aw.main", "mcp", "headers"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
    )
    assert headers.returncode == 0
    assert json.loads(headers.stdout) == {"Authorization": f"Bearer {hub.token}"}


def test_state_directory_has_one_live_service_owner(hub):
    process = subprocess.run(
        [sys.executable, "-m", "aw.main", "mcp", "serve", "--state-dir", str(hub.state), "--port", "8766"],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert process.returncode != 0
    assert "another AW server owns this state directory" in process.stderr


def test_project_directory_and_optional_agent_identity(tmp_path, repos):
    box = Mailbox(tmp_path / "scopes")
    def scoped(path, name, project, paths):
        checked = box.preflight(project, name, {"description": name, "paths": paths}, str(path))
        return box.register(checked["preflight_id"])
    a = scoped(repos[0], "AW MCP implementation", ["faberline", "aw"], ["src/aw/mcp/**"])
    b = scoped(repos[1], "AW MCP verification", ["faberline", "aw"], ["e2e/**"])
    c = scoped(repos[2], "Core delivery", ["faberline", "core"], ["src/**"])
    same_worktree = scoped(repos[0], "AW documentation", ["faberline", "aw"], ["docs/**"])
    assert a["id"] != same_worktree["id"]
    assert a["client"] == a["native_id"] == ""
    assert a["project"] == b["project"] and a["repo_id"] != b["repo_id"]
    assert a["repo_id"] == c["repo_id"]
    found = box.sessions(a["id"], a["session_token"], project=["faberline", "aw"], scope_query="verification")
    assert [row["id"] for row in found] == [b["id"]]
    assert len(box.sessions(a["id"], a["session_token"], project=["faberline"])) == 4
    assert box.sessions(a["id"], a["session_token"], session_name="Core")[0]["id"] == c["id"]
    checked = box.preflight(["faberline", "aw"], "MCP retry handling", {
        "description": "Retry handling", "paths": ["src/aw/mcp/store.py"],
    }, str(repos[1]))
    overlaps = {row["session_id"]: row["reasons"] for row in checked["possible_overlaps"]}
    assert overlaps[a["id"]] == ["paths_may_overlap"]
    assert overlaps[b["id"]] == ["shared_worktree"]
    assert c["id"] not in {row["id"] for row in checked["related_sessions"]}
    assert all("session_token" not in row and "token_hash" not in row for row in checked["related_sessions"])
    assert box.heartbeat(a["id"], a["session_token"], "busy")["status"] == "busy"
    assert box.heartbeat(a["id"], a["session_token"])["status"] == "busy"


def test_preflight_rejects_invalid_scope_and_changed_git(tmp_path, repos):
    box = Mailbox(tmp_path / "validation")
    inputs = {"project": ["faberline", "aw"], "session_name": "AW MCP implementation",
              "scope": {"description": "MCP implementation", "paths": ["src/**"]}, "worktree": str(repos[0])}
    for changes in ({"project": []}, {"session_name": " "}, {"scope": {"description": ""}},
                    {"scope": {"description": "escape", "paths": ["../outside"]}},
                    {"scope": {"description": "escape", "paths": ["/tmp"]}}):
        with pytest.raises(ValueError):
            box.preflight(**(inputs | changes))
    (repos[0] / "external").symlink_to(repos[1], target_is_directory=True)
    with pytest.raises(ValueError, match="symlink outside"):
        box.preflight(**(inputs | {"scope": {"description": "escape", "paths": ["external/**"]}}))
    with pytest.raises(ValueError, match="missing or expired"):
        box.register("no-preflight")
    expired = box.preflight(**inputs)
    with box.connect() as db:
        db.execute("UPDATE preflights SET expires_at='2000-01-01' WHERE id=?", (expired["preflight_id"],))
    with pytest.raises(ValueError, match="expired"):
        box.register(expired["preflight_id"])
    checked = box.preflight(**inputs)
    subprocess.run([
        "git", "-C", str(repos[0]), "-c", "user.name=AW tests", "-c", "user.email=aw@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
        "commit", "-q", "--allow-empty", "-m", "change after preflight",
    ], check=True)
    with pytest.raises(ValueError, match="Git identity changed"):
        box.register(checked["preflight_id"])


def test_scope_update_keeps_identity_and_rejects_other_or_stale_tickets(tmp_path, repos):
    box = Mailbox(tmp_path / "updates")
    inputs = {"project": ["faberline", "aw"], "session_name": "AW MCP implementation",
              "scope": {"description": "Implement MCP", "paths": ["src/**"]}, "worktree": str(repos[0])}
    a = box.register(box.preflight(**inputs)["preflight_id"])
    b = box.register(box.preflight(**(inputs | {"session_name": "AW MCP verification"}))["preflight_id"])
    checked = box.preflight(**(inputs | {"session_name": "AW MCP retry handling",
                                        "scope": {"description": "Handle retries", "paths": ["src/aw/mcp/**"]}}),
                            session_id=a["id"], token=a["session_token"])
    stale = box.preflight(**inputs, session_id=a["id"], token=a["session_token"])
    with pytest.raises(ValueError, match="different session"):
        box.update(b["id"], b["session_token"], checked["preflight_id"])
    updated = box.update(a["id"], a["session_token"], checked["preflight_id"])
    assert updated["id"] == a["id"] and updated["revision"] == a["revision"] + 1
    assert updated["scope"]["description"] == "Handle retries"
    with pytest.raises(ValueError, match="session changed"):
        box.update(a["id"], a["session_token"], stale["preflight_id"])
    with pytest.raises(ValueError, match="missing or expired"):
        box.update(a["id"], a["session_token"], checked["preflight_id"])
    box.claim(a["id"], a["session_token"])
    move = box.preflight(**(inputs | {"worktree": str(repos[1])}), session_id=a["id"], token=a["session_token"])
    with pytest.raises(ValueError, match="release the current"):
        box.update(a["id"], a["session_token"], move["preflight_id"])
    assert box.session(a["id"], a["session_token"])["worktree"] == str(repos[0])
    box.claim(a["id"], a["session_token"], release=True)
    assert box.update(a["id"], a["session_token"], move["preflight_id"])["worktree"] == str(repos[1])


def test_legacy_upgrade_preserves_mail_credentials_and_claims(tmp_path, repos):
    state = tmp_path / "legacy"
    box = Mailbox(state)
    def scoped(name):
        checked = box.preflight(["faberline", "aw"], name, {"description": "Legacy migration"}, str(repos[0]))
        return box.register(checked["preflight_id"], "codex", name)
    a, b = scoped("legacy-a"), scoped("legacy-b")
    message = box.send(a["id"], a["session_token"], b["id"], "migration", "Preserve this message", "legacy-1")
    box.claim(a["id"], a["session_token"])
    # Recreate the deployed v1 table, including its original uniqueness rule.
    with box.connect() as db:
        db.executescript("""
            PRAGMA foreign_keys=OFF;
            BEGIN IMMEDIATE;
            CREATE TABLE old_sessions (
                id TEXT PRIMARY KEY, client TEXT NOT NULL, native_id TEXT NOT NULL,
                label TEXT NOT NULL, repo_id TEXT NOT NULL, worktree TEXT NOT NULL,
                branch TEXT NOT NULL, head TEXT NOT NULL, token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL, last_seen TEXT NOT NULL, status TEXT NOT NULL,
                UNIQUE(client,native_id,worktree)
            );
            INSERT INTO old_sessions SELECT id,client,native_id,session_name,repo_id,worktree,
                branch,head,token_hash,created_at,last_seen,status FROM sessions;
            DROP TABLE preflights;
            DROP TABLE sessions;
            ALTER TABLE old_sessions RENAME TO sessions;
            COMMIT;
        """)
    migrated = Mailbox(state)
    for session in (a, b):
        assert migrated.session(session["id"], session["session_token"])["ready"] is False
        with pytest.raises(ValueError, match="needs project and scope"):
            migrated.inbox(session["id"], session["session_token"])
        checked = migrated.preflight(["faberline", "aw"], session["session_name"],
                                     {"description": "Migrated session"}, str(repos[0]),
                                     session["id"], session["session_token"])
        migrated.update(session["id"], session["session_token"], checked["preflight_id"])
    assert migrated.inbox(b["id"], b["session_token"])[0]["id"] == message["id"]
    with pytest.raises(ValueError, match="owner must release"):
        migrated.claim(b["id"], b["session_token"])
    assert migrated.claim(a["id"], a["session_token"])["owner"] == a["id"]


def test_http_clients_share_mail_and_enforce_recipient_identity(hub, repos):
    async def scenario():
        async with connect(hub.url, hub.token) as a, connect(hub.url, hub.token) as b:
            sender = await register(a, "codex", repos[0])
            recipient = await register(b, "agy", repos[1])
            stranger = await register(b, "claude-code", repos[2])
            assert sender["repo_id"] == stranger["repo_id"] != recipient["repo_id"]
            arguments = {**bound_arguments(sender), "recipient": recipient["id"],
                         "task_id": "repo-a#42", "body": "The contract is ready.", "request_id": "message-1"}
            message = await call(a, "send_message", arguments)
            assert (await call(a, "send_message", arguments))["id"] == message["id"]
            with pytest.raises(ValueError, match="different message bytes"):
                await call(a, "send_message", {**arguments, "body": "changed"})
            assert (await call(b, "read_inbox", bound_arguments(recipient)))[0]["id"] == message["id"]
            assert await call(b, "read_inbox", bound_arguments(stranger)) == []
            assert await call(b, "read_thread", {**bound_arguments(stranger), "task_id": "repo-a#42"}) == []
            with pytest.raises(ValueError, match="invalid session credentials"):
                await call(a, "read_inbox", {**bound_arguments(recipient), "session_token": sender["session_token"]})
            with pytest.raises(ValueError, match="does not belong"):
                await call(b, "acknowledge_message", {**bound_arguments(stranger), "message_id": message["id"]})
            acknowledged = await call(b, "acknowledge_message", {
                **bound_arguments(recipient), "message_id": message["id"],
            })
            assert acknowledged["acknowledged_at"] is not None
            assert await call(b, "read_inbox", bound_arguments(recipient)) == []
            directory = await call(a, "list_sessions", bound_arguments(sender))
            assert all("session_token" not in row and "token_hash" not in row for row in directory)
    asyncio.run(scenario())


def test_event_stream_pushes_without_an_inbox_tool_call_and_replays(hub, repos):
    async def scenario():
        async with connect(hub.url, hub.token) as remote:
            sender, target = await register(remote, "codex", repos[0]), await register(remote, "agy", repos[1])
            stream = events(hub.url, hub.token, target)
            waiting = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.1)
            first = await call(remote, "send_message", {
                **bound_arguments(sender), "recipient": target["id"],
                "task_id": "cross-repo", "body": "push", "request_id": "push-1",
            })
            assert (await asyncio.wait_for(waiting, 4))["id"] == first["id"]
            await stream.aclose()
            replay = events(hub.url, hub.token, target)
            assert (await asyncio.wait_for(anext(replay), 4))["id"] == first["id"]
            await replay.aclose()
    asyncio.run(scenario())


def test_state_survives_a_service_restart_and_handoff_keeps_git_identity(tmp_path, repos):
    state = tmp_path / "persistent"
    with running_hub(state) as first:
        async def write():
            async with connect(first.url, first.token) as remote:
                sender = await register(remote, "codex", repos[0])
                target = await register(remote, "claude-code", repos[2])
                card = await call(remote, "save_handoff", {
                    **bound_arguments(sender), "recipient": target["id"], "task_id": "handoff-42",
                    "request_id": "handoff-1", "objective": "Finish the change",
                    "summary": "Quota ended", "next_step": "Run the focused gate",
                    "allowed_paths": ["src/a.py"], "changed_files": ["src/a.py"],
                    "untracked_files": ["notes.txt"], "checks": [{"command": "pytest", "result": "not run"}],
                })
                return sender, target, card
        sender, target, card = asyncio.run(write())
    with running_hub(state) as second:
        async def read():
            async with connect(second.url, second.token) as remote:
                saved = (await call(remote, "read_inbox", bound_arguments(target)))[0]
                assert saved["id"] == card["id"]
                assert saved["payload"]["git"]["head"] == sender["head"]
                assert saved["payload"]["untracked_files"] == ["notes.txt"]
                assert saved["payload"]["verified"] is False
                assert saved["payload"]["grants_permission"] is False
        asyncio.run(read())


def test_worktree_claim_requires_its_owner_to_release(tmp_path, repos):
    box = Mailbox(tmp_path / "claims")
    def scoped(client, name):
        checked = box.preflight(["tests", "aw"], name, {"description": "Writer fixture"}, str(repos[0]))
        return box.register(checked["preflight_id"], client, name)
    a, b = scoped("codex", "a"), scoped("agy", "b")
    box.claim(a["id"], a["session_token"])
    box.heartbeat(a["id"], a["session_token"], "offline")
    with pytest.raises(ValueError, match="owner must release"):
        box.claim(b["id"], b["session_token"])
    with pytest.raises(ValueError, match="owner must release"):
        box.claim(b["id"], b["session_token"], release=True)
    box.claim(a["id"], a["session_token"], release=True)
    assert box.claim(b["id"], b["session_token"])["owner"] == b["id"]


def test_http_rejects_missing_service_auth_and_browser_origins(hub):
    assert httpx2.get(hub.base + "/health").status_code == 401
    assert httpx2.get(hub.base + "/health", headers={b"authorization": b"Bearer \xff"}).status_code == 401
    assert httpx2.get(hub.base + "/health", headers={
        "Authorization": f"Bearer {hub.token}", "Origin": "https://unrelated.invalid",
    }).status_code == 403


@pytest.mark.parametrize("payload", [[], {"session_id": [], "delivery": "forwarded"}, {"detail": 1}])
def test_http_rejects_malformed_receipts_without_a_server_error(hub, payload):
    response = httpx2.post(hub.base + "/receipts/nonexistent", headers={
        "Authorization": f"Bearer {hub.token}",
    }, json=payload)
    assert response.status_code == 400


def test_http_bounds_delivery_receipt_size(hub):
    response = httpx2.post(hub.base + "/receipts/nonexistent", headers={
        "Authorization": f"Bearer {hub.token}",
    }, content=b"x" * 8193)
    assert response.status_code == 413


def test_private_credentials_refuse_public_files_and_symlinks(tmp_path):
    path = tmp_path / "credential"
    path.write_text("test-only")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="mode-0600"):
        private_read(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        private_read(link)


def test_stdio_proxy_binds_the_actor_and_uses_shared_http_state(hub, repos, tmp_path):
    async def scenario():
        async with connect(hub.url, hub.token) as remote:
            actor = await register(remote, "codex", repos[0])
            target = await register(remote, "agy", repos[1])
            path = tmp_path / "actor.json"
            private_write(path, json.dumps(actor))
            params = StdioServerParameters(command=sys.executable, args=[
                "-m", "aw.main", "mcp", "proxy", "--session-file", str(path), "--url", hub.url,
            ], env={**os.environ, "AW_MCP_TOKEN": hub.token}, cwd=str(ROOT))
            async with Client(params) as proxy:
                tools = (await proxy.list_tools()).tools
                assert len(tools) == 12
                assert all("session_token" not in tool.input_schema["properties"] for tool in tools)
                message = await call(proxy, "send_message", {
                    "recipient": target["id"], "task_id": "proxy", "body": "hello", "request_id": "proxy-1",
                })
                assert message["sender"] == actor["id"]
                rejected = await proxy.call_tool("read_inbox", {"session_id": target["id"]})
                assert rejected.is_error
                assert (await call(remote, "read_inbox", bound_arguments(target)))[0]["id"] == message["id"]
    asyncio.run(scenario())


def test_unbound_proxy_gates_registration_updates_scope_and_resumes(hub, repos):
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "aw.main", "mcp", "proxy", "--url", hub.url, "--state-dir", str(hub.state),
        ], cwd=str(repos[0]))
        inputs = {"project": ["faberline", "aw"], "session_name": "AW MCP registration",
                  "scope": {"description": "Implement session admission", "paths": ["src/aw/mcp/**"]},
                  "worktree": str(repos[0])}
        async with Client(params) as proxy:
            assert {tool.name for tool in (await proxy.list_tools()).tools} == {"preflight", "register_session"}
            refused = await proxy.call_tool("read_inbox", {})
            assert refused.is_error
            refused = await proxy.call_tool("register_session", {"preflight_id": "unchecked"})
            assert refused.is_error
            checked = await call(proxy, "preflight", inputs)
            arguments = {"preflight_id": checked["preflight_id"]}
            registration = await proxy.call_tool("register_session", arguments)
            assert not registration.is_error
            public = registration.structured_content
            saved = json.loads(private_read(Path(public["session_file"])))
            assert saved["client"] == saved["native_id"] == ""
            assert saved["session_token"] not in json.dumps(public) + " ".join(item.text for item in registration.content)
            assert await call(proxy, "register_session", arguments) == public
            tools = (await proxy.list_tools()).tools
            assert len(tools) == 12
            assert all("session_token" not in tool.input_schema["properties"] for tool in tools)
            refused = await proxy.call_tool("preflight", {**inputs, "session_id": "another-session"})
            assert refused.is_error
            renamed = inputs | {"session_name": "AW MCP scope verification",
                                "scope": {"description": "Verify scoped sessions", "paths": ["e2e/**"]}}
            checked = await call(proxy, "preflight", renamed)
            updated = await call(proxy, "update_session", {"preflight_id": checked["preflight_id"]})
            assert updated["id"] == public["id"]
            assert updated["scope"]["paths"] == ["e2e/**"]
            cached = json.loads(private_read(Path(public["session_file"])))
            assert cached["scope"] == updated["scope"]
        resumed_params = StdioServerParameters(command=sys.executable, args=[
            "-m", "aw.main", "mcp", "proxy", "--url", hub.url, "--state-dir", str(hub.state),
            "--session-file", public["session_file"],
        ], cwd=str(repos[0]))
        async with Client(resumed_params) as resumed:
            assert len((await resumed.list_tools()).tools) == 12
            peers = await call(resumed, "list_sessions", {"project": ["faberline", "aw"],
                                                       "session_name": "AW MCP scope verification"})
            assert [row["id"] for row in peers] == [public["id"]]
            assert await call(resumed, "read_inbox", {}) == []
    asyncio.run(scenario())


def test_proxy_heartbeat_preserves_reported_busy_status(hub, repos, tmp_path):
    async def scenario():
        async with connect(hub.url, hub.token) as remote:
            actor = await register(remote, "codex", repos[0])
            path = tmp_path / "heartbeat.json"
            private_write(path, json.dumps(actor))
            # Run the production proxy with a shorter timer, without adding a
            # test-only CLI option or calling a native model.
            script = """import asyncio, os, sys
from pathlib import Path
from aw.mcp.client import credentials
import aw.mcp.proxy as proxy
proxy.HEARTBEAT_SECONDS = 0.05
asyncio.run(proxy.run_proxy(sys.argv[1], os.environ['AW_MCP_TOKEN'],
    credentials(Path(sys.argv[2])), state_dir=Path(sys.argv[3]), session_path=Path(sys.argv[2])))
"""
            params = StdioServerParameters(command=sys.executable, args=[
                "-c", script, hub.url, str(path), str(hub.state),
            ], env={**os.environ, "AW_MCP_TOKEN": hub.token}, cwd=str(ROOT))
            async with Client(params) as proxy:
                busy = await call(proxy, "heartbeat", {"status": "busy"})
                box = Mailbox(hub.state)
                for _ in range(50):
                    current = box.session(actor["id"], actor["session_token"])
                    if current["last_seen"] > busy["last_seen"]:
                        break
                    await asyncio.sleep(0.05)
                assert current["last_seen"] > busy["last_seen"]
                assert current["status"] == "busy"
    asyncio.run(scenario())


def test_native_receiver_refuses_a_changed_worktree_binding(hub, repos):
    async def scenario():
        async with connect(hub.url, hub.token) as remote:
            sender = await register(remote, "codex", repos[2])
            target = await register(remote, "agy", repos[0])
            checked = await call(remote, "preflight", {
                **bound_arguments(target), "project": ["tests", "aw"], "session_name": "Moved receiver",
                "scope": {"description": "Moved to another worktree"}, "worktree": str(repos[1]),
            })
            await call(remote, "update_session", {**bound_arguments(target), "preflight_id": checked["preflight_id"]})
            message = await call(remote, "send_message", {
                **bound_arguments(sender), "recipient": target["id"], "task_id": "moved-receiver",
                "body": "Check the new target before forwarding", "request_id": "moved-1",
            })
            class Receiver:
                called = False
                async def send(self, message):
                    self.called = True
                    return "must not be forwarded"
            receiver = Receiver()
            receiving = asyncio.create_task(receive_forever(hub.url, hub.token, target, receiver))
            try:
                for _ in range(80):
                    row = (await call(remote, "read_inbox", bound_arguments(target)))[0]
                    if row["delivery"] == "failed":
                        break
                    await asyncio.sleep(0.05)
                assert row["id"] == message["id"] and row["delivery"] == "failed"
                assert "receiver binding changed" in row["delivery_detail"]
                assert receiver.called is False
            finally:
                receiving.cancel()
                await asyncio.gather(receiving, return_exceptions=True)
    asyncio.run(scenario())


def test_generated_client_configs_launch_independent_proxies_to_one_mailbox(hub, repos):
    configured = subprocess.run([
        sys.executable, "-m", "aw.main", "mcp", "install", "--client", "all",
        "--scope", "project", "--worktree", str(repos[0]), "--state-dir", str(hub.state), "--url", hub.url,
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert configured.returncode == 0, configured.stderr
    assert hub.token not in configured.stdout + configured.stderr
    definitions = [
        tomllib.loads((repos[0] / ".codex/config.toml").read_text())["mcp_servers"]["aw"],
        json.loads((repos[0] / ".mcp.json").read_text())["mcpServers"]["aw"],
        json.loads((repos[0] / ".agents/mcp_config.json").read_text())["mcpServers"]["aw"],
    ]
    async def scenario():
        params = [StdioServerParameters(command=definition["command"], args=definition["args"],
                                       cwd=str(repos[1])) for definition in definitions]
        async with Client(params[0]) as codex, Client(params[1]) as claude, Client(params[2]) as agy:
            assert {tool.name for tool in (await codex.list_tools()).tools} == {"preflight", "register_session"}
            source = await register(codex, "codex", repos[0])
            target = await register(agy, "agy", repos[1])
            third = await register(claude, "claude-code", repos[2])
            message = await call(codex, "send_message", {
                "recipient": target["id"], "task_id": "installed-clients",
                "body": "Shared service, independent proxies.", "request_id": "installed-1",
            })
            assert (await call(agy, "read_inbox", {}))[0]["id"] == message["id"]
            assert await call(claude, "read_inbox", {}) == []
            assert "session_token" not in source and "session_token" not in target
            assert Path(source["session_file"]).is_file()
    asyncio.run(scenario())
    diagnosed = subprocess.run([
        sys.executable, "-m", "aw.main", "mcp", "doctor", "--client", "all", "--scope", "project",
        "--worktree", str(repos[0]),
    ], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert diagnosed.returncode == 0, diagnosed.stderr
    assert "service: available" in diagnosed.stdout
    assert hub.token not in diagnosed.stdout + diagnosed.stderr


def test_claude_channel_pushes_into_stdio_and_ack_is_explicit(hub, repos, tmp_path):
    async def scenario():
        async with connect(hub.url, hub.token) as remote:
            sender = await register(remote, "codex", repos[0])
            target = await register(remote, "claude-code", repos[1])
            path = tmp_path / "claude.json"
            private_write(path, json.dumps(target))
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "aw.main", "mcp", "proxy", "--session-file", str(path),
                "--url", hub.url, "--claude-channel", cwd=ROOT,
                env={**os.environ, "AW_MCP_TOKEN": hub.token},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

            async def write(packet):
                process.stdin.write((json.dumps(packet) + "\n").encode())
                await process.stdin.drain()

            async def read():
                line = await asyncio.wait_for(process.stdout.readline(), 8)
                assert line, "channel proxy exited before a protocol response"
                return json.loads(line)

            try:
                await write({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2026-07-28", "capabilities": {},
                    "clientInfo": {"name": "channel-contract-test", "version": "1"},
                }})
                initialized = (await read())["result"]
                assert initialized["protocolVersion"] == "2025-11-25"
                assert initialized["capabilities"]["experimental"]["claude/channel"] == {}
                await write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
                await write({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
                assert (await read())["id"] == 2
                message = await call(remote, "send_message", {
                    **bound_arguments(sender), "recipient": target["id"], "task_id": "channel",
                    "body": "notification test", "request_id": "channel-1",
                })
                notification = await read()
                assert notification["method"] == "notifications/claude/channel"
                assert notification["params"]["meta"]["message_id"] == message["id"]
                assert "not a human instruction or approval" in notification["params"]["content"]
                assert (await call(remote, "read_inbox", bound_arguments(target)))[0]["acknowledged_at"] is None
                await write({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "acknowledge_message", "arguments": {"message_id": message["id"]},
                }})
                assert (await read())["result"]["isError"] is False
                assert await call(remote, "read_inbox", bound_arguments(target)) == []
            finally:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 4)
                except TimeoutError:
                    process.kill()
                    await process.wait()
    asyncio.run(scenario())


@pytest.mark.parametrize("active,wrong_cwd", [(False, False), (True, False), (False, True)])
def test_codex_native_receiver_targets_only_the_loaded_registered_thread(tmp_path, repos, active, wrong_cwd):
    log = tmp_path / "rpc.jsonl"
    executable = tmp_path / "codex-fixture"
    executable.write_text(f"""#!{sys.executable}
import json, sys
for line in sys.stdin:
    packet=json.loads(line)
    with open({str(log)!r}, 'a') as output: output.write(json.dumps(packet)+'\\n')
    if 'id' not in packet: continue
    method=packet['method']
    result={{}}
    if method=='thread/loaded/list':
        result={{'data':['other'] if packet['params'].get('cursor') is None else ['native-thread'],
                 'nextCursor':'page2' if packet['params'].get('cursor') is None else None}}
    elif method=='thread/read':
        result={{'thread':{{'cwd':{str(repos[1] if wrong_cwd else repos[0])!r},
                             'status':{{'type':{'active' if active else 'idle'!r}}}}}}}
    elif method=='thread/turns/list': result={{'data':[{{'id':'active-turn','status':'inProgress'}}]}}
    elif method=='turn/start': result={{'turn':{{'id':'new-turn'}}}}
    elif method=='turn/steer': result={{'turnId':'active-turn'}}
    print(json.dumps({{'id':packet['id'],'result':result}}), flush=True)
""")
    executable.chmod(0o700)
    async def scenario(socket_path):
        saved = {"native_id": "native-thread", "worktree": str(repos[0])}
        message = {"id": "mail-1", "task_id": "native", "sender": "peer", "body": "hello",
                   "kind": "message", "payload": {}}
        async with CodexReceiver(saved, socket_path, str(executable)) as receiver:
            if wrong_cwd:
                with pytest.raises(ValueError, match="cwd does not match"):
                    await receiver.send(message)
            else:
                accepted = await receiver.send(message)
                assert ("turn/steer" if active else "turn/start") in accepted
                # A native connection can close while the mailbox receiver stays alive.
                receiver.process.kill()
                await receiver.process.wait()
                await receiver.reader
                accepted = await receiver.send({**message, "id": "mail-after-reconnect"})
                assert ("turn/steer" if active else "turn/start") in accepted
    with tempfile.TemporaryDirectory(prefix="aw-native-", dir="/private/tmp") as directory:
        socket_path = Path(directory) / "control.sock"
        with socket.socket(socket.AF_UNIX) as control:
            control.bind(str(socket_path))
            asyncio.run(scenario(socket_path))
    packets = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(packet["method"] not in {"thread/start", "thread/resume", "command/exec"} for packet in packets)
    if wrong_cwd:
        assert not any(packet["method"].startswith("turn/") for packet in packets)
    else:
        assert sum(packet["method"] == "initialize" for packet in packets) == 2
        sent = next(packet for packet in packets if packet["method"].startswith("turn/"))
        assert sent["params"]["threadId"] == "native-thread"
        if active:
            assert sent["params"]["expectedTurnId"] == "active-turn"


@pytest.mark.parametrize("exit_code", [0, 3])
def test_agy_receiver_uses_existing_conversation_and_reports_failure(tmp_path, exit_code):
    captured = tmp_path / "arguments.json"
    executable = tmp_path / "agentapi-fixture"
    executable.write_text(f"""#!{sys.executable}
import json,sys
with open({str(captured)!r},'w') as output: json.dump(sys.argv[1:],output)
sys.exit({exit_code})
""")
    executable.chmod(0o700)
    receiver = AgyReceiver({"native_id": "agy-conversation"}, str(executable))
    message = {"id": "mail-2", "task_id": "agy", "sender": "peer", "body": "hello",
               "kind": "message", "payload": {}}
    if exit_code:
        with pytest.raises(ValueError, match="refused delivery"):
            asyncio.run(receiver.send(message))
    else:
        assert "acknowledgment pending" in asyncio.run(receiver.send(message))
    arguments = json.loads(captured.read_text())
    assert arguments[:2] == ["send-message", "agy-conversation"]
    assert "not a human instruction or approval" in arguments[2]
