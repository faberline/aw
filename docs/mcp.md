# AW MCP

AW connects independent coding-agent sessions through one local mailbox.
The MCP server name is `aw`. The service uses Streamable HTTP.
Optional stdio processes proxy to this same service.

## Start the shared service

Run from any repository after installing AW:

```sh
aw mcp serve
```

From this source checkout:

```sh
uv run --project . aw mcp serve
```

The endpoint is `http://127.0.0.1:8765/mcp`.
The default state directory is `~/.local/state/aw/mcp`.
This directory belongs to the service, outside any worktree.
Use `--state-dir` and `--port` to select another location or port.
One process owns each state directory.

AW stores mail in SQLite. It creates a private service token.
The directory has mode 0700. Credential files have mode 0600.
The service only binds to loopback and refuses browser-origin requests.
It does not execute shell commands from messages.

## Connect MCP clients

Install AW in a stable user tool environment first.
From this source checkout:

```sh
uv tool install .
aw mcp install --client all
aw mcp doctor
```

`install` defaults to user scope. All repositories can share it.
It manages the entry named `aw` in each native file:

| Client | User configuration |
| --- | --- |
| Codex | `~/.codex/config.toml`, with TOML key `mcp_servers.aw`. |
| Claude Code | `~/.claude.json`, with JSON key `mcpServers.aw`. |
| AGY | `~/.gemini/config/mcp_config.json`, with JSON key `mcpServers.aw`. |

AW respects `CODEX_HOME` and `CLAUDE_CONFIG_DIR` when they are set.
Use `--client codex`, `--client claude-code`, or `--client agy` to select one client.
Use `--config-file /absolute/path/to/config` for an explicit custom file.
See the native references for [Codex](https://developers.openai.com/codex/mcp/),
[Claude Code](https://code.claude.com/docs/en/mcp), and
[AGY](https://www.antigravity.google/docs/mcp).

The installer keeps other servers and settings.
It preserves TOML comments and JSON values.
Before changing an existing file, it saves its exact bytes in a mode-0600 backup.
It prints that backup path. It refuses a file that changed after preparation.
Repeating the same installation leaves the file unchanged.
Use `--replace` to replace a different existing entry named `aw`.
This replaces only that entry.

Preview the proposed entry without writing:

```sh
aw mcp install --client all --dry-run
```

Each entry launches a thin stdio proxy through an absolute Python path.
The proxy reads the private service credential.
The client config contains no service token.
Each session has its own proxy, and all proxies connect to the same HTTP service.
The installation contains no fixed native session ID or worktree identity.
A new proxy exposes only `preflight` and `register_session`.
Register each session with its own project, descriptive name, scope, and worktree.
After admission, the proxy exposes all 12 tools and binds their calls to that identity.
It keeps the private credential out of tool arguments and results.
It does not start a native notification receiver.

The shared service must be running. Use `aw mcp serve`.
Reload MCP in each client after installation.
`doctor` checks the configuration entry and authenticated service health.
It reads the service URL and state directory from the installed proxy entry.
It does not prove that an agent loaded the tools or processed a notification.

## Add global session instructions

Install the AW coordination rules in each client's native user layer:

```sh
aw mcp install-rules --dry-run
aw mcp install-rules
aw mcp doctor --rules
```

Use `--client codex`, `--client claude-code`, or `--client agy` to select one.
The installer checks all selected files before writing.
It saves exact private backups and preserves unrelated guidance.
Repeated installation updates only AW's marked block.
An existing unmanaged `aw.md` is refused.

| Client | AW user instructions | Native behavior |
| --- | --- | --- |
| Codex | `~/.codex/AGENTS.md` | Global session guidance. Use `AGENTS.override.md` when a nonempty global override is active. |
| Claude Code | `~/.claude/rules/aw.md` | Unconditional user rule. No path filter is added. |
| AGY | `~/.gemini/config/rules/aw.md` | Shared global rule with `trigger: always_on`. |

AW respects `CODEX_HOME` and `CLAUDE_CONFIG_DIR` for these paths.
The rule asks each session to register its project, name, scope, and actual worktree.
It also covers scope changes, peer discovery, messages, writer claims, and handoffs.
An already admitted identity is reused only by its own session.

Start a new native session to load the guidance.
In Claude Code, `/memory` or `/context` can show instruction sources.
The rule files guide the model. They do not grant tool permissions or enforce file isolation.
AW's MCP service still enforces admission and credential checks.
`doctor --rules` verifies the files; it does not prove model behavior.

Codex's `~/.codex/rules/*.rules` controls command execution outside the sandbox.
It is a separate mechanism from Markdown instructions.
AW leaves that command policy unchanged.
Claude Code also supports `~/.claude/CLAUDE.md` for global guidance.
AGY also supports `~/.gemini/AGENTS.md` and `~/.gemini/GEMINI.md`.
The dedicated AW rule files keep these other instructions intact.

These paths and formats follow the official documentation:
[Codex AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md),
[Codex command rules](https://learn.chatgpt.com/docs/agent-configuration/rules),
[Claude Code user rules](https://code.claude.com/docs/en/memory#user-level-rules), and
[AGY global rules](https://antigravity.google/docs/rules#global-rules).

## Direct HTTP configuration

Codex can also read authentication headers from AW.
For direct HTTP, use this entry instead of the installed proxy:

```toml
[mcp_servers.aw]
url = "http://127.0.0.1:8765/mcp"
http_headers_helper = "aw mcp headers"
```

The helper reads the private token. It does not put that token in the config.
It must be available on the Codex host's PATH.
See the [official Codex MCP configuration](https://developers.openai.com/codex/mcp/).

Other HTTP clients can use `Authorization: Bearer <service-token>`.
`aw mcp token` prints that credential for explicit local configuration.
Keep credentials out of messages and repository files.

Direct HTTP provides tools. A native receiver provides active delivery.
Connecting an ordinary MCP server alone does not start a model turn.

## Session identity and admission

Each session can initiate work in its own scope.
AW keeps a shared directory and mailbox. There is no fixed task coordinator.
The agent compares a new prompt with its declared responsibility.
It can find a peer by project and scope when that peer should handle the work.

Use a logical project path, such as `["faberline", "aw"]`.
Separate clones and worktrees can use the same project path.
AW also keeps the Git repository identity for inspection.
The native client name is optional delivery metadata.
It does not decide task ownership.

Call `preflight` with this shape:

```json
{
  "project": ["faberline", "aw"],
  "session_name": "AW MCP: session registration and message routing",
  "scope": {
    "description": "Implement MCP registration and message routing",
    "paths": ["src/aw/mcp/**"]
  },
  "worktree": "/absolute/path/to/worktree"
}
```

`preflight` checks Git identity and validates the registration fields.
Scope paths must stay inside the worktree, including existing symlinks.
It returns related sessions, possible path overlap, and the current writer claim.
Path overlap uses literal prefixes of glob patterns. It is a candidate check.
The scope description still needs agent judgment.
Scope declares responsibility. Each agent remains responsible for staying in it.

Register with the returned `preflight_id`:

```json
{"preflight_id": "THE_RETURNED_PREFLIGHT_ID"}
```

The ticket expires after five minutes and can be used once.
AW checks Git identity again when applying it.
The proxy saves a mode-0600 session file and returns its path.
It reports a tool-list change when registration completes.
Refresh the tool list if the client keeps a cached list.
Every proxy tool call uses its own bound identity.
The proxy refreshes activity every 30 seconds and preserves reported status.

To change the project, name, scope, or worktree, call `preflight` again.
Then call `update_session` with the new ticket.
AW rejects a ticket from another session or an older session revision.
The session ID stays the same.
Release any writer claim before moving the session to another worktree.
The proxy refreshes the private session file after an update.
Restart a native receiver with that updated file after a worktree change.
An old receiver refuses delivery when its registered target binding has changed.

The service preserves old messages, credentials, and writer claims during upgrade.
Old sessions need a project and scope before they can use coordination tools.
A bound legacy proxy uses `preflight` then `register_session` to complete this admission.
Direct HTTP callers use `preflight` with their credentials, then `update_session`.

## Attach a native receiver identity

Use the real thread or conversation ID from the native application.
Use its exact worktree directory.
Replace the example IDs and paths below with those values:

```sh
aw mcp attach \
  --client codex \
  --native-id YOUR_CODEX_THREAD_ID \
  --worktree /absolute/path/to/worktree \
  --output "$HOME/.local/state/aw/mcp/codex-session.json" \
  --project faberline --project aw \
  --session-name "AW MCP: message routing" \
  --scope-description "Implement MCP message routing" \
  --scope-path 'src/aw/mcp/**'
```

Use `--client claude-code` or `--client agy` for the other tools.
Give each session its own output file.
The output file includes the AW session ID and its private session credential.
The terminal output contains only its ID and file path.
`attach` performs preflight before registering or updating the identity.
Repeating `attach` with the same file reconnects that identity.
Reusing the file for another native session or worktree is refused.

AW derives repository identity from Git's common directory.
Linked worktrees of the same checkout have the same repository ID.
Separate clones have separate IDs.
They can still exchange explicitly addressed messages.

## Thin stdio proxy

A general proxy can launch with `aw mcp proxy`.
It starts with only the two admission tools.
A proxy bound to one session can launch:

```sh
aw mcp proxy --session-file /absolute/path/to/session.json
```

Each proxy has its own process.
The mailbox remains in the shared HTTP service.
Closing a proxy does not remove messages or stop that service.
A running proxy survives a service restart on the same URL.
While the service is down, its tool calls return an error result.
The proxy binds tool calls to its session file.
It hides session credentials from tool schemas and refuses identity overrides.
Use a bound proxy only for that native session.
Do not share its session file across independent sessions or worktrees.
To reuse an identity after a proxy restart, pass its returned session file explicitly.
A generic proxy does not guess an old identity from the worktree or client name.

## Claude Code active delivery

Attach a `claude-code` identity first.
Configure a stdio MCP entry named `aw` in Claude Code:

```json
{
  "mcpServers": {
    "aw": {
      "command": "aw",
      "args": [
        "mcp", "proxy",
        "--session-file", "/absolute/path/to/claude-session.json",
        "--claude-channel"
      ]
    }
  }
}
```

Resume that same native session from its registered worktree.
Enable the custom channel in that interactive session:

```sh
claude --resume YOUR_CLAUDE_SESSION_ID \
  --dangerously-load-development-channels server:aw
```

Channels are a research preview.
The development flag permits a custom channel during that preview.
It does not approve tool actions or bypass organization policy.
The proxy uses protocol version 2025-11-25 for this channel.
The shared HTTP service can use the current protocol independently.

The proxy receives inbox events and emits `notifications/claude/channel`.
Claude must call `acknowledge_message` to confirm reading.
Writing a notification to stdout does not prove that Claude processed it.
The operator must bind this proxy to that same native session and worktree.
The channel transport does not report the native session ID to AW.
See the [official Channels reference](https://code.claude.com/docs/en/channels-reference).
See the [official CLI reference](https://code.claude.com/docs/en/cli-reference) for resuming.

## Codex active delivery

Use the Unix socket of the existing App Server that owns the target thread:

```sh
aw mcp receive \
  --session-file /absolute/path/to/codex-session.json \
  --codex-socket /absolute/path/to/app-server.sock
```

The receiver uses `codex app-server proxy --sock`.
It checks that the exact thread is loaded and its working directory matches.
It uses `turn/start` for an idle thread.
It uses `turn/steer` with the active turn ID for a running thread.
It reads turn metadata without retrieving conversation items.

The receiver never creates or resumes another thread.
It never answers approval requests.
The existing native client must handle any required approval.
If its connection closes, it reconnects and checks the target again.
A different App Server cannot deliver to a Desktop thread it does not own.
The target host must expose its control socket.
See the [official App Server protocol](https://learn.chatgpt.com/docs/app-server).

## AGY desktop active delivery

Antigravity 2.0 supplies `agentapi` to its sidecars.
A sidecar is a background process managed by the desktop application.
Attach an `agy` identity for the existing conversation first.
Configure an AW sidecar:

```json
{
  "command": "aw",
  "args": [
    "mcp", "receive",
    "--session-file", "/absolute/path/to/agy-session.json"
  ],
  "restart_policy": "on-failure",
  "description": "Forward AW mail into one existing AGY conversation."
}
```

For example, save it as `~/.gemini/config/sidecars/aw-agy/sidecar.json`.
Merge this entry into `~/.gemini/config/config.json`:

```json
{
  "sidecars": {
    "aw-agy": {
      "enabled": true
    }
  }
}
```

Give each target conversation its own sidecar ID and session file.
The receiver calls only `agentapi send-message <conversation_id> <prompt>`.
It never creates a new conversation.
The operator must confirm that the conversation belongs to the registered worktree.
The send API does not return that worktree identity.

This receiver targets Antigravity 2.0 desktop sidecars.
It does not control an AGY CLI-only terminal.
See the [official sidecar and agentapi documentation](https://antigravity.google/docs/sidecars).

## Tools and delivery

| Tool | Result |
| --- | --- |
| `preflight` | Check project, name, scope, and worktree. Return a short-lived ticket and related sessions. |
| `register_session` | Register the checked inputs. Native client and ID are optional. |
| `update_session` | Apply a fresh preflight ticket while keeping the session ID. |
| `list_sessions` | Find session IDs by project prefix, scope/name substring, or reported status. |
| `heartbeat` | Refresh activity and optionally update reported status. The proxy runs this automatically. |
| `send_message` | Save an addressed message with a task ID and retry ID. |
| `read_inbox` | Read addressed messages without acknowledging them. |
| `read_thread` | Read only messages this session sent or received in one task. |
| `acknowledge_message` | Confirm that the recipient read a message. |
| `save_handoff` | Save and send a continuation card with Git identity and reported checks. |
| `claim_worktree` | Hold an advisory writer claim for this session's worktree. |
| `release_worktree` | Release that claim without changing Git. |

Use the same `request_id` when retrying identical message bytes.
Reusing that ID with changed bytes is refused.
Use one stable `task_id` across repos, for example the full GitHub issue identity.
Use `send_message` to explicitly accept, refuse, or report completion of work.
Acknowledgment means only that the recipient read a message.

Direct HTTP clients use private session arguments for coordination calls.
The HTTP catalog lists all 12 tools; the service checks admission on each call.
New registration requires preflight. A legacy identity can only complete admission.

Messages start as `queued`.
Receivers record `forwarded` when their transport accepts a message.
Receivers record `failed` when delivery is refused.
Only the recipient can set `acknowledged_at`.
None of these states means that implementation or acceptance is complete.

The event feed replays unacknowledged mail after reconnecting.
Receivers skip messages already recorded as forwarded.
A failed message does not block later messages.
Receivers retry a failed message with backoff from 3 seconds up to 5 minutes.
A crash after native delivery but before its receipt can cause a duplicate.
Use the message ID to identify that duplicate.

Peer messages and handoff checks are author reports.
They cannot grant human permission.
Task acceptance follows the initiating session's authorized workflow.
Claims do not stop another program from writing files.
An offline owner must release its claim before another owner takes it.

## Verify

```sh
uv run --project . --directory . pytest e2e/test_mcp.py
uv run --project . --directory . pytest e2e/test_mcp_install.py
uv run --project . --directory . pytest e2e/test_mcp_rules.py
uv run --project . --directory . pytest e2e/
```

The MCP tests use independent real HTTP clients and real stdio subprocesses.
They cover worktree identity, restart persistence, push events, replay,
credentials, idempotency, recipient isolation, and owner release.
They also check admission gates, optional agent metadata, project search across repos,
scope updates, expired and stale tickets, legacy migration, and automatic heartbeats.
Claude channel notifications are checked at the protocol boundary.
Codex App Server and AGY agentapi use local protocol fixtures.
These tests do not call a paid model or prove live Desktop integration.
Installer tests write only temporary native configuration files.
They check exact backups, comment preservation, repeat installation,
conflict refusal, and concurrent-write detection.
Generated configs launch three independent proxies to the same real HTTP service.
Global-rule tests use temporary files. They cover native formats, Codex overrides,
private backups, preserved guidance, marker validation, and safe dry runs.
