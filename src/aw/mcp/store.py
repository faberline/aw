"""SQLite mailbox. Peer reports are evidence to inspect, never approval."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

CLIENTS = {"codex", "claude-code", "agy"}
STATUSES = {"idle", "busy", "blocked", "offline"}
PUBLIC_SESSION = (
    "id,client,native_id,session_name,project,scope,repo_id,worktree,branch,head,"
    "created_at,last_seen,status,revision,ready"
)
PREFLIGHT_SECONDS = 300

SESSION_SCHEMA = """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, client TEXT NOT NULL, native_id TEXT NOT NULL,
        session_name TEXT NOT NULL, project TEXT NOT NULL, scope TEXT NOT NULL,
        repo_id TEXT NOT NULL, worktree TEXT NOT NULL, branch TEXT NOT NULL,
        head TEXT NOT NULL, token_hash TEXT NOT NULL, created_at TEXT NOT NULL,
        last_seen TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
        ready INTEGER NOT NULL
    )
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_value(value: str, name: str, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} must be nonempty and at most {maximum} UTF-8 bytes")
    return value


def project_value(project: list[str]) -> list[str]:
    if not isinstance(project, list) or not 1 <= len(project) <= 8:
        raise ValueError("project must contain 1 to 8 name segments")
    result = [text_value(part, "project segment", 128).strip() for part in project]
    if any(part in {".", ".."} or any(char in part for char in "/\\>") for part in result):
        raise ValueError("use separate project segments, for example ['faberline', 'aw']")
    return result


def scope_value(scope: dict, worktree: str) -> dict:
    if not isinstance(scope, dict) or set(scope) - {"description", "paths"}:
        raise ValueError("scope must contain description and optional paths")
    description = text_value(scope.get("description"), "scope description").strip()
    paths = scope.get("paths", [])
    if not isinstance(paths, list) or len(paths) > 100:
        raise ValueError("scope paths must be a list of at most 100 repository-relative paths or globs")
    root = Path(worktree)
    checked = []
    for item in paths:
        text_value(item, "scope path", 512)
        if item != item.strip() or item.startswith(("/", "~")) or "\\" in item or ".." in item.split("/"):
            raise ValueError("scope paths must stay inside the worktree")
        parts = []
        for part in item.split("/"):
            if any(char in part for char in "*?["):
                break
            parts.append(part)
        if not (root / "/".join(parts)).resolve().is_relative_to(root):
            raise ValueError("scope path follows a symlink outside the worktree")
        checked.append(item)
    return {"description": description, "paths": sorted(set(checked))}


def related_projects(a: list[str], b: list[str]) -> bool:
    return a[:len(b)] == b or b[:len(a)] == a


def possible_path_overlap(a: list[str], b: list[str]) -> bool:
    """Compare literal prefixes. This is a candidate check, not glob intersection proof."""
    def prefix(pattern):
        parts = []
        for part in pattern.split("/"):
            if any(char in part for char in "*?["):
                break
            if part and part != ".":
                parts.append(part)
        return parts
    return any(related_projects(prefix(left), prefix(right)) for left in a for right in b)


def private_read(path: Path) -> str:
    """Read an owned, private regular file without following a final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError(f"expected an owned mode-0600 file: {path}")
        with os.fdopen(fd, encoding="utf-8") as source:
            fd = -1
            return source.read()
    finally:
        if fd != -1:
            os.close(fd)


def private_write(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        target.write(content)


def private_replace(path: Path, content: str) -> None:
    """Refresh an existing private session file without following a symlink."""
    original = private_read(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        if private_read(path) != original:
            raise ValueError("session file changed during update")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def state_token(state_dir: Path) -> str:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = state_dir.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"state directory must be owned and mode 0700: {state_dir}")
    path = state_dir / "token"
    try:
        private_write(path, secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    return text_value(private_read(path).strip(), "service token", 256)


def git_identity(worktree: str) -> dict:
    try:
        path = Path(worktree).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("worktree must be an existing directory") from error
    if not path.is_dir():
        raise ValueError("worktree must be a directory")

    def git(*args: str, optional: bool = False) -> str:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode and not optional:
            raise ValueError("worktree must belong to a Git repository")
        return result.stdout.strip() if not result.returncode else ""

    root = git("rev-parse", "--show-toplevel")
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    return {
        "repo_id": hashlib.sha256(str(Path(common).resolve()).encode()).hexdigest()[:24],
        "worktree": str(Path(root).resolve()),
        "branch": git("symbolic-ref", "--quiet", "--short", "HEAD", optional=True),
        "head": git("rev-parse", "--verify", "HEAD", optional=True),
    }


class Mailbox:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.token = state_token(state_dir)
        self.path = state_dir / "mail.sqlite3"
        if self.path.is_symlink():
            raise ValueError("mail database must not be a symlink")
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            # Keep existing messages, credentials and writer claims. Legacy
            # sessions must provide their project and scope before admission.
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if not columns:
                db.execute(SESSION_SCHEMA)
            elif "project" not in columns:
                db.execute(SESSION_SCHEMA.replace("CREATE TABLE sessions", "CREATE TABLE sessions_new"))
                db.execute(
                    "INSERT INTO sessions_new SELECT id,client,native_id,label,'[]',"
                    "'{\"description\":\"\",\"paths\":[]}',repo_id,worktree,branch,head,token_hash,"
                    "created_at,last_seen,status,0,0 FROM sessions"
                )
                db.execute("DROP TABLE sessions")
                db.execute("ALTER TABLE sessions_new RENAME TO sessions")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS native_session_index ON sessions(client,native_id,worktree) "
                "WHERE client<>'' AND native_id<>''"
            )
            db.commit()
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    sender TEXT NOT NULL REFERENCES sessions(id),
                    recipient TEXT NOT NULL REFERENCES sessions(id),
                    task_id TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
                    payload TEXT NOT NULL, request_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    delivery TEXT NOT NULL DEFAULT 'queued', delivery_detail TEXT NOT NULL DEFAULT '',
                    acknowledged_at TEXT, UNIQUE(sender,request_id)
                );
                CREATE INDEX IF NOT EXISTS inbox_index ON messages(recipient,sequence);
                CREATE INDEX IF NOT EXISTS thread_index ON messages(task_id,sequence);
                CREATE TABLE IF NOT EXISTS claims (
                    worktree TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    claimed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS preflights (
                    id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
                    revision INTEGER NOT NULL, proposed TEXT NOT NULL, expires_at TEXT NOT NULL
                );
            """)
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("mail database has broken session references")
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _public(row: sqlite3.Row) -> dict:
        data = {field: row[field] for field in PUBLIC_SESSION.split(",")}
        for field in ("project", "scope"):
            data[field] = json.loads(data[field])
        data["ready"] = bool(data["ready"])
        return data

    def preflight(self, project: list[str], session_name: str, scope: dict, worktree: str,
                  session_id: str = "", token: str = "") -> dict:
        identity = git_identity(worktree)
        proposed = {
            **identity, "project": project_value(project),
            "session_name": text_value(session_name, "session_name", 256).strip(),
            "scope": scope_value(scope, identity["worktree"]),
        }
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if bool(session_id) != bool(token):
                raise ValueError("both session credentials are required for an update preflight")
            actor = self._auth(db, session_id, token, require_ready=False) if session_id else None
            related, overlaps = [], []
            for row in db.execute("SELECT * FROM sessions WHERE id<>?", (session_id,)):
                other = self._public(row)
                same_worktree = other["worktree"] == identity["worktree"]
                same_project = bool(other["project"]) and related_projects(proposed["project"], other["project"])
                if same_worktree or same_project:
                    related.append(other)
                    reasons = []
                    if same_worktree:
                        reasons.append("shared_worktree")
                    if same_project and possible_path_overlap(proposed["scope"]["paths"], other["scope"]["paths"]):
                        reasons.append("paths_may_overlap")
                    if reasons:
                        overlaps.append({"session_id": other["id"], "reasons": reasons})
            owner = db.execute("SELECT * FROM claims WHERE worktree=?", (identity["worktree"],)).fetchone()
            ticket = str(uuid4())
            expires = (datetime.now(timezone.utc) + timedelta(seconds=PREFLIGHT_SECONDS)).isoformat()
            db.execute("DELETE FROM preflights WHERE expires_at<?", (now(),))
            db.execute("INSERT INTO preflights VALUES (?,?,?,?,?)", (
                ticket, session_id or None, actor["revision"] if actor is not None else 0,
                json.dumps(proposed, ensure_ascii=False, sort_keys=True), expires,
            ))
        return {
            "preflight_id": ticket, "expires_at": expires, "proposed": proposed,
            "related_sessions": related, "possible_overlaps": overlaps,
            "writer": dict(owner) if owner is not None else None,
            "scope_match": "path prefixes only; description needs agent judgment",
        }

    def _checked(self, db: sqlite3.Connection, preflight_id: str, session_id: str = "") -> dict:
        row = db.execute("SELECT * FROM preflights WHERE id=?", (preflight_id,)).fetchone()
        if row is None or row["expires_at"] < now():
            raise ValueError("preflight is missing or expired; run preflight again")
        if (row["session_id"] or "") != session_id:
            raise ValueError("preflight belongs to a different session or operation")
        if session_id:
            revision = db.execute("SELECT revision FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
            if row["revision"] != revision:
                raise ValueError("session changed after preflight; run preflight again")
        proposed = json.loads(row["proposed"])
        identity = git_identity(proposed["worktree"])
        if any(identity[field] != proposed[field] for field in identity):
            raise ValueError("Git identity changed after preflight; run preflight again")
        scope_value(proposed["scope"], proposed["worktree"])
        db.execute("DELETE FROM preflights WHERE id=?", (preflight_id,))
        return proposed

    def register(self, preflight_id: str, client: str = "", native_id: str = "") -> dict:
        if client and client not in CLIENTS:
            raise ValueError("client must be codex, claude-code, or agy when supplied")
        if native_id:
            text_value(native_id, "native_id", 256)
        session_id, token, timestamp = str(uuid4()), secrets.token_urlsafe(32), now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            proposed = self._checked(db, preflight_id)
            try:
                db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, client, native_id, proposed["session_name"], json.dumps(proposed["project"]),
                     json.dumps(proposed["scope"]), proposed["repo_id"], proposed["worktree"],
                     proposed["branch"], proposed["head"], hashlib.sha256(token.encode()).hexdigest(),
                     timestamp, timestamp, "idle", 1, 1),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("session already registered; reuse its private session file") from error
        return {**self.session(session_id, token), "session_token": token}

    def update(self, session_id: str, token: str, preflight_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            actor = self._auth(db, session_id, token, require_ready=False)
            proposed = self._checked(db, preflight_id, session_id)
            if actor["worktree"] != proposed["worktree"] and db.execute(
                "SELECT 1 FROM claims WHERE session_id=?", (session_id,),
            ).fetchone() is not None:
                raise ValueError("release the current worktree claim before changing worktree")
            try:
                db.execute(
                    "UPDATE sessions SET session_name=?,project=?,scope=?,repo_id=?,worktree=?,branch=?,head=?,"
                    "last_seen=?,revision=revision+1,ready=1 WHERE id=?",
                    (proposed["session_name"], json.dumps(proposed["project"]), json.dumps(proposed["scope"]),
                     proposed["repo_id"], proposed["worktree"], proposed["branch"], proposed["head"], now(), session_id),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("native session is already registered in that worktree") from error
        return self.session(session_id, token)

    def _auth(self, db: sqlite3.Connection, session_id: str, token: str,
              *, require_ready: bool = True) -> sqlite3.Row:
        row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if row is None or not hmac.compare_digest(row["token_hash"], digest):
            raise ValueError("invalid session credentials")
        if require_ready and not row["ready"]:
            raise ValueError("session needs project and scope; run preflight then update_session")
        return row

    def session(self, session_id: str, token: str) -> dict:
        with self.connect() as db:
            return self._public(self._auth(db, session_id, token, require_ready=False))

    def sessions(self, session_id: str, token: str, repo_id: str = "", project: list[str] | None = None,
                 scope_query: str = "", session_name: str = "", status: str = "") -> list[dict]:
        prefix = project_value(project) if project is not None else None
        if status and status not in STATUSES:
            raise ValueError("unknown session status")
        with self.connect() as db:
            self._auth(db, session_id, token)
            rows = [self._public(row) for row in db.execute(
                f"SELECT {PUBLIC_SESSION} FROM sessions WHERE (?='' OR repo_id=?) ORDER BY created_at",
                (repo_id, repo_id),
            )]
        return [row for row in rows if
                (prefix is None or row["project"][:len(prefix)] == prefix) and
                (not status or row["status"] == status) and
                session_name.casefold() in row["session_name"].casefold() and
                scope_query.casefold() in (row["scope"]["description"] + " " + " ".join(row["scope"]["paths"])).casefold()]

    def heartbeat(self, session_id: str, token: str, status: str | None = None) -> dict:
        if status is not None and status not in STATUSES:
            raise ValueError("status must be idle, busy, blocked, or offline")
        with self.connect() as db:
            self._auth(db, session_id, token)
            db.execute("UPDATE sessions SET last_seen=?,status=COALESCE(?,status) WHERE id=?", (now(), status, session_id))
        return self.session(session_id, token)

    @staticmethod
    def _message(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def send(self, session_id: str, token: str, recipient: str, task_id: str,
             body: str, request_id: str, *, kind: str = "message", payload: dict | None = None) -> dict:
        text_value(task_id, "task_id", 256)
        text_value(body, "body", 32768)
        text_value(request_id, "request_id", 256)
        encoded = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        if len(encoded.encode()) > 65536:
            raise ValueError("handoff must be at most 65536 UTF-8 bytes")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._auth(db, session_id, token)
            if not db.execute("SELECT id FROM sessions WHERE id=?", (recipient,)).fetchone():
                raise ValueError("recipient session does not exist")
            previous = db.execute(
                "SELECT * FROM messages WHERE sender=? AND request_id=?", (session_id, request_id),
            ).fetchone()
            if previous is not None:
                expected = (recipient, task_id, kind, body, encoded)
                if tuple(previous[key] for key in ("recipient", "task_id", "kind", "body", "payload")) != expected:
                    raise ValueError("request_id was already used for different message bytes")
                return self._message(previous)
            message_id = str(uuid4())
            db.execute(
                "INSERT INTO messages(id,sender,recipient,task_id,kind,body,payload,request_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (message_id, session_id, recipient, task_id, kind, body, encoded, request_id, now()),
            )
            return self._message(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def inbox(self, session_id: str, token: str, after: int = 0, limit: int = 50,
              pending_only: bool = False) -> list[dict]:
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("after must be >=0 and limit must be between 1 and 200")
        with self.connect() as db:
            self._auth(db, session_id, token)
            return [self._message(row) for row in db.execute(
                "SELECT * FROM messages WHERE recipient=? AND sequence>? "
                "AND (?=0 OR acknowledged_at IS NULL) ORDER BY sequence LIMIT ?",
                (session_id, after, int(pending_only), limit),
            )]

    def thread(self, session_id: str, token: str, task_id: str, after: int = 0, limit: int = 50) -> list[dict]:
        if after < 0 or not 1 <= limit <= 200:
            raise ValueError("after must be >=0 and limit must be between 1 and 200")
        with self.connect() as db:
            self._auth(db, session_id, token)
            return [self._message(row) for row in db.execute(
                "SELECT * FROM messages WHERE task_id=? AND sequence>? "
                "AND (sender=? OR recipient=?) ORDER BY sequence LIMIT ?",
                (task_id, after, session_id, session_id, limit),
            )]

    def receipt(self, session_id: str, token: str, message_id: str, delivery: str,
                detail: str = "") -> dict:
        if delivery not in {"forwarded", "failed", "acknowledged"}:
            raise ValueError("unknown delivery state")
        if len(detail.encode()) > 2048:
            raise ValueError("delivery detail is too long")
        with self.connect() as db:
            self._auth(db, session_id, token)
            row = db.execute("SELECT * FROM messages WHERE id=? AND recipient=?", (message_id, session_id)).fetchone()
            if row is None:
                raise ValueError("message does not belong to this recipient")
            if delivery == "acknowledged":
                db.execute("UPDATE messages SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                           (now(), message_id))
            elif row["acknowledged_at"] is None:
                db.execute("UPDATE messages SET delivery=?,delivery_detail=? WHERE id=?", (delivery, detail, message_id))
            return self._message(db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone())

    def claim(self, session_id: str, token: str, release: bool = False) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self._auth(db, session_id, token)
            path = session["worktree"]
            owner = db.execute("SELECT * FROM claims WHERE worktree=?", (path,)).fetchone()
            if owner is not None and owner["session_id"] != session_id:
                raise ValueError("worktree is held by another session; its owner must release it")
            if release:
                db.execute("DELETE FROM claims WHERE worktree=? AND session_id=?", (path, session_id))
                return {"worktree": path, "owner": None, "advisory": True}
            if owner is None:
                db.execute("INSERT INTO claims VALUES (?,?,?)", (path, session_id, now()))
            return {"worktree": path, "owner": session_id, "advisory": True}
