"""Sandboxed, read-only operations against bare git mirrors.

Every function here takes a resolved mirror path (never a raw string from
the model — see craft_dashboard.git_mirrors.paths.mirror_path_for) plus
already-validated arguments, and shells out to git via subprocess with an
explicit argument list. No function here ever uses shell=True, ever invokes
`git checkout`, and every mirror is treated as read-only: `git log`, `git
show`, `git grep`, `git ls-tree`, and `git blame` are the only subcommands
used.

Concurrency: a module-level semaphore bounds how many git subprocesses can
run at once, independent of eval concurrency — git is CPU/RAM-bound and
wants 1-2 concurrent processes on a single-vCPU VPS, while eval concurrency
(network-bound HTTP calls to the LLM backend) wants 3+. Call
set_git_concurrency() once at process startup to size this for the current
environment (default 2).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import selectors
import subprocess
import time
from typing import IO

from craft_dashboard.git_mirrors.exceptions import InvalidPathError, InvalidRefError

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_GREP_PATTERN_LENGTH = 500
#: Length cap applied to every free-text search argument (grep patterns,
#: `git log --grep=` queries, and `git log -S` pickaxe strings) so an
#: over-long value can neither bloat the argv nor drive pathological regex
#: cost. Design "Sandboxing": "patterns are length-capped."
_MAX_QUERY_LENGTH = 500
_GIT_TIMEOUT_SECONDS = 30
#: Hard byte ceiling on any single tool's captured output. Enforced by
#: streaming stdout incrementally (see _exec_git_capped) so at most this many
#: bytes are ever held in memory, and the git process is terminated once the
#: ceiling is reached. This is the memory-safety net the design requires on
#: every tool — a broad `git grep` across ~150MB of source can otherwise emit
#: tens of MB, and buffering all of it (capture_output=True) is the
#: runaway-RSS failure mode on the 1-vCPU / ~307MB VPS.
_MAX_OUTPUT_BYTES = 200_000
#: Hard byte ceiling on captured stderr. Beyond this, stderr is still drained
#: to avoid pipe back-pressure, but additional bytes are discarded.
_MAX_STDERR_BYTES = 20_000
#: Read chunk size while streaming git stdout under the byte ceiling.
_READ_CHUNK_BYTES = 65_536

#: Git hardening flags applied to EVERY invocation, in addition to the
#: read-only nature of the operations themselves:
#:  - protocol.ext.allow=never — no ext:: transport, ever.
#:  - safe.bareRepository=all  — the mirrors ARE bare repos; without this a
#:    host/CI that sets safe.bareRepository=explicit (a hardening default in
#:    some distros/containers) refuses every `git -C <bare>` command.
#:  - safe.directory=*         — the mirror is written by the craft-dashboard
#:    container (:rw) and read by the llm-evaluate container (:ro) under a
#:    DIFFERENT uid, which trips git's "detected dubious ownership" check and
#:    aborts the read. The wildcard disables that check; it is safe here
#:    because the only directories ever passed are allowlist-resolved mirror
#:    paths, never attacker-controlled strings.
_GIT_HARDENING = (
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "safe.bareRepository=all",
    "-c",
    "safe.directory=*",
)


class _PipeState:
    """State for one subprocess pipe being drained."""

    def __init__(
        self,
        *,
        stream: IO[bytes],
        buffer: bytearray,
        limit: int,
        terminate_on_cap: bool,
    ) -> None:
        self.stream = stream
        self.buffer = buffer
        self.limit = limit
        self.terminate_on_cap = terminate_on_cap


def _git_argv(mirror: pathlib.Path, *args: str) -> list[str]:
    """Build a hardened `git -C <mirror> ...` argument list."""
    return ["git", "-C", str(mirror), *_GIT_HARDENING, *args]


_git_semaphore = asyncio.Semaphore(2)


def set_git_concurrency(limit: int) -> None:
    """Resize the module-level git concurrency semaphore.

    Call once at process startup (e.g. from the eval worker's runtime setup)
    to match the current environment — 1-2 on the VPS's single vCPU, higher
    on a dev machine with more cores.
    """
    global _git_semaphore  # noqa: PLW0603
    _git_semaphore = asyncio.Semaphore(limit)


def validate_ref(ref: str) -> None:
    """Raise InvalidRefError unless ref is a 40-character hex SHA.

    Branch names, tags, and short SHAs are all rejected — every git
    operation here must be pinned to an exact, previously-resolved commit
    so recorded evidence is reproducible (see design doc section 3, SHA
    pinning).

    PHASE-4 SEAM (do not lose this): this enforces the 40-hex *format*
    only. The design's "Sandboxing" section requires a ref be "a 40-hex SHA
    drawn from **the pinned set**" — i.e. one of the specific HEAD SHAs that
    `/api/eval/next` supplied for that project. That pinned-set membership
    check does not exist yet because the pinned set does not exist until
    Phase 4. When Phase 4 lands SHA pinning it MUST extend this: the reader
    functions currently take a bare `ref: str` with no `allowed_refs`
    parameter, so Phase 4 has to change these signatures (add e.g.
    `allowed_refs: frozenset[str] | None`) and reject any 40-hex ref not in
    the pinned set. Format-only validation lets a caller reach any commit
    that exists in the mirror, which is acceptable while nothing wires the
    reader to the model, but is not the final security posture.
    """
    if not _SHA_RE.match(ref):
        raise InvalidRefError(f"ref must be a 40-character hex SHA, got: {ref!r}")


def validate_path(path: str) -> None:
    """Raise InvalidPathError for an absolute path or one containing '..'.

    Checked as both a literal path-string prefix/component test (fast,
    catches the common cases) — path traversal here would otherwise let a
    crafted path reach files outside the repo tree via `git show <ref>:<path>`
    (which normalizes '..' components at the git-object-tree level, not the
    filesystem level, but is still rejected defensively since it has no
    legitimate use in this sandbox).
    """
    if path.startswith("/"):
        raise InvalidPathError(f"path must be relative, got: {path!r}")
    if ".." in pathlib.PurePosixPath(path).parts:
        raise InvalidPathError(f"path must not contain '..', got: {path!r}")


def _exec_git_capped(
    argv: list[str], *, allowed_exit_codes: tuple[int, ...] = (0,)
) -> tuple[int, bytes]:
    """Run *argv* to completion with capped stdout/stderr and a hard timeout.

    Streams stdout and stderr concurrently rather than using
    capture_output=True / communicate(), both of which buffer git's ENTIRE
    output in memory before any truncation can be applied. That buffering is
    the runaway-RSS failure mode on the 1-vCPU / ~307MB VPS: a broad `git
    grep` can emit tens of MB. Here stdout is capped at _MAX_OUTPUT_BYTES and
    stderr at _MAX_STDERR_BYTES, while both pipes are continuously drained so
    neither can back-pressure the child into a deadlock. If stdout reaches its
    ceiling the git process is terminated so it stops producing more.

    Runs blocking, so callers wrap it in asyncio.to_thread under the git
    semaphore. Raises subprocess.CalledProcessError if git exits with a code
    not in allowed_exit_codes (unless output was truncated, in which case the
    early SIGTERM makes the exit code meaningless and truncated output is
    returned). Raises subprocess.TimeoutExpired if git exceeds the deadline.
    """
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = bytearray()
    stderr = bytearray()
    truncated = False
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS

    def raise_timeout() -> None:
        raise subprocess.TimeoutExpired(
            argv, _GIT_TIMEOUT_SECONDS, output=bytes(out), stderr=bytes(stderr)
        )

    try:
        if proc.stdout is None or proc.stderr is None:  # pragma: no cover
            raise RuntimeError("git subprocess pipes were not created")
        os.set_blocking(proc.stdout.fileno(), False)
        os.set_blocking(proc.stderr.fileno(), False)
        selector.register(
            proc.stdout,
            selectors.EVENT_READ,
            _PipeState(
                stream=proc.stdout,
                buffer=out,
                limit=_MAX_OUTPUT_BYTES,
                terminate_on_cap=True,
            ),
        )
        selector.register(
            proc.stderr,
            selectors.EVENT_READ,
            _PipeState(
                stream=proc.stderr,
                buffer=stderr,
                limit=_MAX_STDERR_BYTES,
                terminate_on_cap=False,
            ),
        )

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise_timeout()
            ready = selector.select(remaining)
            if not ready:
                raise_timeout()
            for key, _events in ready:
                state: _PipeState = key.data
                chunk = os.read(state.stream.fileno(), _READ_CHUNK_BYTES)
                if not chunk:
                    selector.unregister(state.stream)
                    state.stream.close()
                    continue
                if len(state.buffer) < state.limit:
                    state.buffer += chunk[: state.limit - len(state.buffer)]
                if (
                    state.terminate_on_cap
                    and len(out) >= _MAX_OUTPUT_BYTES
                    and not truncated
                ):
                    truncated = True
                    proc.terminate()

        remaining = deadline - time.monotonic()
        try:
            proc.wait(timeout=max(remaining, 0))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        selector.close()
        if proc.stdout is not None and not proc.stdout.closed:
            proc.stdout.close()
        if proc.stderr is not None and not proc.stderr.closed:
            proc.stderr.close()
        if proc.poll() is None:  # ensure no orphan on any error path
            proc.kill()
            proc.wait()
    if not truncated and proc.returncode not in allowed_exit_codes:
        raise subprocess.CalledProcessError(
            proc.returncode, argv, output=bytes(out), stderr=bytes(stderr)
        )
    return proc.returncode, bytes(out)


async def _run_git(mirror: pathlib.Path, *args: str) -> str:
    """Run a git subcommand against *mirror* and return capped stdout as text.

    Bare-mirror-hardened via _git_argv (protocol.ext.allow=never,
    safe.bareRepository=all, safe.directory=*). `--no-pager` and any explicit
    `--` separator are added by each caller as appropriate; this helper owns
    the semaphore, the streaming byte ceiling, and the timeout.
    """
    async with _git_semaphore:
        _returncode, stdout = await asyncio.to_thread(
            _exec_git_capped, _git_argv(mirror, *args)
        )
    return stdout.decode("utf-8", errors="replace")


async def head_sha(mirror: pathlib.Path) -> str:
    """Return the full 40-char commit SHA that HEAD resolves to in *mirror*.

    Public wrapper over `git rev-parse HEAD`, for callers outside this module
    (e.g. Phase 4's `/api/eval/next` SHA-pinning) that need a repo's current
    HEAD without reaching into the private `_run_git` helper.
    """
    return (await _run_git(mirror, "rev-parse", "--verify", "HEAD")).strip()


async def has_commit(mirror: pathlib.Path, sha: str) -> bool:
    """Return whether *mirror* already has *sha* as a reachable commit object.

    Uses `git cat-file -e <sha>^{commit}` (existence check, no output) so
    Phase 4's worker preflight can tell "I already hold this pinned SHA"
    from "I need to fetch first" without a throwaway git-log/show call. A
    malformed (non-40-hex) sha is rejected up front via validate_ref rather
    than shelled out to git, consistent with every other public entry point
    in this module.
    """
    validate_ref(sha)
    try:
        await _run_git(mirror, "cat-file", "-e", f"{sha}^{{commit}}")
    except subprocess.CalledProcessError:
        return False
    return True


async def read_file(
    mirror: pathlib.Path,
    *,
    path: str,
    ref: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Return the content of *path* at *ref* via `git show <ref>:<path>`.

    ``start_line``/``end_line`` (both 1-indexed and inclusive) let a caller
    read a slice of a large file instead of paying for its entire content
    every time -- the model doesn't need a whole multi-thousand-line file
    just to inspect one function. Omit both to get the full file (unchanged
    behavior). The full blob is still fetched from git (git has no partial
    read for a blob), but only the requested lines are returned, so token
    cost scales with the slice, not the file.
    """
    validate_ref(ref)
    validate_path(path)
    content = await _run_git(mirror, "--no-pager", "show", f"{ref}:{path}", "--")
    if start_line is None and end_line is None:
        return content
    lines = content.splitlines()
    total = len(lines)
    start = max(start_line or 1, 1)
    end = min(end_line if end_line is not None else total, total)
    if start > total or start > end:
        return f"(no lines in range {start}-{end}; file has {total} lines)"
    selected = lines[start - 1 : end]
    header = f"(showing lines {start}-{end} of {total})"
    return f"{header}\n" + "\n".join(selected)


async def grep_repo(mirror: pathlib.Path, *, pattern: str, ref: str) -> list[str]:
    """Return matching lines for *pattern* at *ref* via `git grep`.

    Uses `-e <pattern>` (not a bare positional pattern) so a pattern
    beginning with '-' can never be parsed as a git-grep flag. Returns []
    (not an error) when there are no matches, since `git grep` exits 1 in
    that case. Output is streamed under the same _MAX_OUTPUT_BYTES ceiling as
    every other tool (see _exec_git_capped) — critically, grep is the tool
    most able to emit huge output, so it must not bypass the ceiling.
    """
    pattern = pattern[:_MAX_GREP_PATTERN_LENGTH]
    validate_ref(ref)
    argv = _git_argv(
        mirror,
        "--no-pager",
        "grep",
        "--threads=1",
        "-n",
        "-e",
        pattern,
        ref,
        "--",
    )
    async with _git_semaphore:
        # exit code 1 == "no matches", which is not an error.
        _returncode, stdout = await asyncio.to_thread(
            _exec_git_capped, argv, allowed_exit_codes=(0, 1)
        )
    output = stdout.decode("utf-8", errors="replace")
    return [line for line in output.splitlines() if line]


#: Bucket key for files that live at the repository root (no directory).
_ROOT_LAYOUT_KEY = "./"


def _summarize_layout(paths: list[str]) -> dict[str, int]:
    """Collapse a flat file list to a depth-2 {directory: file_count} map.

    The bucket for a file is its *containing directory* (its parent),
    capped at two path segments — never the first two segments of the file
    path itself. This is the fix for the earlier bug where a depth-1 file
    such as "src/parts.py" was mis-bucketed under "src/parts.py/" (treating
    the filename as a directory) instead of under "src/".

    - "README.md"                       -> "./"                (root file)
    - "src/parts.py"                    -> "src/"              (parent, depth 1)
    - "craft_parts/executor/step.py"    -> "craft_parts/executor/"
    - "a/b/c/d.py"                      -> "a/b/"              (parent capped at 2)

    Top-level files are bucketed under the explicit root key "./" rather
    than silently dropped, so the counts sum to the total file count.
    """
    counts: dict[str, int] = {}
    for path in paths:
        if not path:
            continue
        parent_parts = path.split("/")[:-1]  # drop the filename
        key = (
            _ROOT_LAYOUT_KEY if not parent_parts else "/".join(parent_parts[:2]) + "/"
        )  # cap the directory at depth 2
        counts[key] = counts.get(key, 0) + 1
    return counts


async def repo_layout(mirror: pathlib.Path, *, ref: str) -> dict[str, int]:
    """Return a depth-2 {directory: file_count} summary at *ref*.

    Backed by `git ls-tree -r --name-only <ref>`, collapsed by
    _summarize_layout() so a file counts against its containing directory
    capped at two path segments (e.g.
    "craft_parts/executor/step_handler.py" counts toward
    "craft_parts/executor/", and "src/parts.py" toward "src/"). Root-level
    files count under "./".

    PHASE-6 SEAM: this returns a structured ``dict[str, int]``, which is the
    right shape for caching and tests. The design's round-1 baseline (§1)
    needs a *rendered text block* like ``craft_parts/executor/  18 files``.
    That formatter is intentionally NOT built here — it is presentation for
    the prompt layer and belongs to Phase 6, which will render this dict.
    Phase 2 stops at the structured data plus its cache (Task 7).
    """
    validate_ref(ref)
    output = await _run_git(mirror, "--no-pager", "ls-tree", "-r", "--name-only", ref)
    return _summarize_layout([line for line in output.splitlines() if line])


async def log_search(mirror: pathlib.Path, *, query: str, ref: str) -> list[str]:
    """Return commit summaries matching *query* via `git log --grep`.

    Searches commit messages only (not diff content — see log_pickaxe for
    that). Returns one-line "<short-sha> <subject>" entries, newest first,
    capped implicitly by _MAX_OUTPUT_BYTES. The query is length-capped
    (design "Sandboxing": patterns are length-capped) and passed as a single
    `--grep=<query>` argument, so it can never be parsed as a separate flag.
    """
    validate_ref(ref)
    query = query[:_MAX_QUERY_LENGTH]
    output = await _run_git(
        mirror,
        "--no-pager",
        "log",
        f"--grep={query}",
        "--format=%h %s",
        ref,
    )
    return [line for line in output.splitlines() if line]


async def log_pickaxe(mirror: pathlib.Path, *, query: str, ref: str) -> list[str]:
    """Return commits whose diff introduced/removed *query* (`git log -S`).

    The query is length-capped (design "Sandboxing": patterns are
    length-capped) and glued into the single `-S<query>` argument, so it is
    never a separate token that could be read as a flag.
    """
    validate_ref(ref)
    query = query[:_MAX_QUERY_LENGTH]
    output = await _run_git(
        mirror,
        "--no-pager",
        "log",
        f"-S{query}",
        "--format=%h %s",
        ref,
    )
    return [line for line in output.splitlines() if line]


async def log_path(mirror: pathlib.Path, *, path: str, ref: str) -> list[str]:
    """Return the commit history touching *path*, via `git log -- <path>`."""
    validate_ref(ref)
    validate_path(path)
    output = await _run_git(
        mirror,
        "--no-pager",
        "log",
        "--format=%h %s",
        ref,
        "--",
        path,
    )
    return [line for line in output.splitlines() if line]
