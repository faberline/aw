# Source preflight diagnostic (review candidate)

`source_preflight.py` is a diagnostic library, not a secret uploader. There is
no subprocess, network client, destination or writer. An explicit fixed-source
entry point is implemented for review, but has not been run against real input.
The current validation uses synthetic temporary files only. No real source has
been tested; these results do not identify the cause of a previous real-source
failure or establish access to another workspace.

Run the isolated tests from this worktree:

```sh
python3 -m unittest discover -s scripts/security/tests -v
git diff --check
```

The bounded caller `authorized_source_status()` gets the effective account's
home from `pwd.getpwuid(os.geteuid()).pw_dir`, ignoring environment `HOME`.
Starting at `/`, it opens each component using anchored directory descriptors
with `O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC`. Components must belong to root or the
effective user, without group/world write (root-owned sticky temporary ancestors
are permitted). The final home must belong to the effective user. It calls
`diagnose_at(home_fd, '.env')` exactly once and closes directory descriptors.
Any failed open/stat stops; no alternate path or source retry is attempted.

The entry point accepts exactly `--diagnose-authorized-source`, no source path
or destination arguments. It prints one fixed code; exit 0 is reserved for
`source_preflight_ok_no_write`, and all other results exit 2. Plain execution
or extra arguments prints `diagnostic_operation_required` and exits 2 without
reading a source. Do not invoke the real-source operation before independent
review passes. Its synthetic tests replace the account home with a temporary
private fixture; they never open the actual account's `.env`.

The lower-level library takes a directory descriptor and a single basename.
It anchors the file read to that descriptor, refuses symlinks, nonregular files,
foreign owners, multiple hardlinks, any group/world mode permissions and files
larger than 64 KiB. It compares inode, mode, owner, link count, size and change
timestamps before opening, after opening and after bounded reading, including
the anchored directory entry. This detects observed changes; it is not an
atomic filesystem snapshot. Mode checks do not audit platform ACLs.

The parser accepts one exact `CLAUDE_CODE_OAUTH_TOKEN=` assignment, optionally
prefixed by `export` plus horizontal space/tab whitespace, with a nonempty ASCII literal token alphabet
`A-Z a-z 0-9 . _ ~ + / -`. Matching single or double quotes are accepted without
expansion. Inline comments, interpolation, escapes, BOM, CR/CRLF, invalid UTF-8,
NUL and ambiguous target assignments refuse. Unrelated keys are not evaluated.
This intentionally supports a limited literal format, not general dotenv or
shell grammar. It neither validates token authenticity nor returns token bytes.

Results are fixed codes, never exception strings, paths, values or line numbers:

| Code family | Meaning |
| --- | --- |
| `source_<stage>_access_denied` | OS reported EACCES at that stage |
| `source_<stage>_operation_denied` | OS reported EPERM at that stage |
| `source_<stage>_not_found` | OS reported ENOENT at that stage |
| `source_<stage>_io_unknown` | Other OS failure; cause remains unknown |
| `source_*_refused`, `source_not_regular` | Strict source safety check failed |
| `source_changed`, `source_too_large` | Observed movement or size bound failed |
| `parse_*` | Literal format/encoding/key check failed |
| `source_internal_unknown` | Unexpected internal error; no success inference |
| `source_preflight_ok_no_write` | Read and literal checks passed; nothing written |

Source stages are `parent`, `stat`, `open`, `fstat`, `read` and `restat`; home
stages are `lookup`, `open` and `stat`. The complete
literal codes are defined in the module. Each denied/unknown read stops the
attempt; no retries, permission changes, alternate tool or fallback path exist.
Cleanup closes the opened descriptor. Credential bytes exist transiently in
Python memory and cannot be reliably zeroized; this diagnostic makes no memory
zeroization claim. It must not be run with tracing or a debugger on real input.

Independent review must examine the fixed-code boundary, all read/open paths,
fixed-source caller, strict parser and fixtures before real-source execution.
Real-source diagnosis and any later opaque upload remain separate operations.
No token transfer or Actions secret update has occurred in this candidate.
