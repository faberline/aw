"""Review candidate: explicit fixed-source diagnostic, no network or writer.

Callers must supply an already-authorized directory descriptor and basename.
This module is currently exercised only against synthetic temporary files.
It deliberately returns no credential bytes and cannot perform a transfer.
"""
import errno
import os
import pwd
import re
import stat
import sys

KEY = "CLAUDE_CODE_OAUTH_TOKEN"
MAX_BYTES = 65536


def error_code(stage, error):
    """Map OS failures without formatting exception text, filenames or values."""
    reason = {
        errno.EACCES: "access_denied",
        errno.EPERM: "operation_denied",
        errno.ENOENT: "not_found",
        errno.ELOOP: "symlink_refused",
        errno.ENOTDIR: "not_directory",
    }.get(error.errno, "io_unknown")
    return stage + "_" + reason


def parse_status(raw):
    """Strict literal assignment subset; never source, eval or expand anything."""
    if len(raw) > MAX_BYTES:
        return "parse_too_large"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "parse_encoding"
    if "\x00" in text or "\r" in text or text.startswith("\ufeff"):
        return "parse_format"
    candidates = []
    malformed = False
    for line in text.split("\n"):
        line = line.strip(" \t")
        if not line or line.startswith("#"):
            continue
        exported = re.match(r"export[ \t]+", line)
        if exported:
            line = line[exported.end():]
        # Catch malformed assignments to the target, while ignoring other keys.
        if re.match(re.escape(KEY) + r"(?:$|[^A-Za-z0-9_])", line):
            if not line.startswith(KEY + "="):
                malformed = True
            else:
                candidates.append(line[len(KEY) + 1:])
    if len(candidates) > 1:
        return "parse_duplicate"
    if malformed:
        return "parse_assignment"
    if not candidates:
        return "parse_missing"
    value = candidates[0]
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    if not value:
        return "parse_empty"
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]+", value, flags=re.ASCII):
        return "parse_nonliteral"
    return "source_preflight_ok_no_write"


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def diagnose_at(directory_fd, basename):
    """Read once through an anchored no-follow descriptor; return fixed code.

    Parent resolution/authorization belongs to the future reviewed caller.
    Denials and unknown failures stop this attempt, without retry or fallback.
    """
    if not isinstance(basename, str) or basename in ("", ".", "..") or "/" in basename or "\x00" in basename:
        return "source_name_refused"
    stage = "source_parent"
    descriptor = None
    try:
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            return "source_parent_not_directory"
        stage = "source_stat"
        before = os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            return "source_not_regular"
        if before.st_uid != os.geteuid():
            return "source_owner_refused"
        if before.st_mode & 0o077 or before.st_nlink != 1:
            return "source_permissions_refused"
        if before.st_size > MAX_BYTES:
            return "source_too_large"
        stage = "source_open"
        descriptor = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        stage = "source_fstat"
        if _identity(os.fstat(descriptor)) != _identity(before):
            return "source_changed"
        stage = "source_read"
        chunks = []
        count = 0
        while count <= MAX_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_BYTES + 1 - count))
            if not chunk:
                break
            chunks.append(chunk)
            count += len(chunk)
        if count > MAX_BYTES:
            return "source_too_large"
        stage = "source_restat"
        if (_identity(os.fstat(descriptor)) != _identity(before)
                or _identity(os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)) != _identity(before)):
            return "source_changed"
        return parse_status(b"".join(chunks))
    except OSError as error:
        return error_code(stage, error)
    except Exception:
        return "source_internal_unknown"
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def authorized_source_status():
    """One fixed source: account database home/.env, never HOME or CLI paths.

    Walk from / using no-follow directory descriptors. No real-source caller
    has been executed during development; tests patch the account home to a
    synthetic private temporary directory.
    """
    descriptors = []
    stage = "home_lookup"
    try:
        home = pwd.getpwuid(os.geteuid()).pw_dir
        if (not isinstance(home, str) or not home.startswith("/") or home == "/"
                or any(part in ("", ".", "..") for part in home[1:].split("/"))):
            return "home_path_refused"
        stage = "home_open"
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        descriptors.append(descriptor)
        for part in home[1:].split("/"):
            descriptor = os.open(part, flags, dir_fd=descriptor)
            descriptors.append(descriptor)
            stage = "home_stat"
            info = os.fstat(descriptor)
            # Root-owned sticky temporary ancestors are safe to traverse using
            # anchored descriptors; the actual home must belong to this user.
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                    or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
                return "home_permissions_refused"
            stage = "home_open"
        if os.fstat(descriptor).st_uid != os.geteuid():
            return "home_owner_refused"
        return diagnose_at(descriptor, ".env")
    except OSError as error:
        return error_code(stage, error)
    except Exception:
        return "home_internal_unknown"
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def main(argv=None):
    # Explicit operation required. Plain execution is an error, never a pass.
    if (sys.argv[1:] if argv is None else argv) != ["--diagnose-authorized-source"]:
        print("diagnostic_operation_required")
        return 2
    code = authorized_source_status()
    print(code)
    return 0 if code == "source_preflight_ok_no_write" else 2


if __name__ == "__main__":
    sys.exit(main())
