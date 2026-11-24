# scripts/collect/

Implementation modules behind `scripts/collect_data.py`. The cron job invokes
`scripts/collect_data.py` by path; that file stays the entry point and owns the
CLI and the run-level orchestration.

## Contract

- `reporting.py` — `CollectionStats`, duration formatting, and the error
  summarizer that keeps oversized GitHub payloads out of the database.
- `retry.py` — retry wrapper for transient GitHub failures, including the
  truncated-response errors PyGithub does not wrap in `GithubException`.
- `projects.py` — project upserts and `collection_watermarks` reads/writes.
- `runs.py` — `collection_runs` health records and the per-source concurrency
  guard.
- `github_pass.py` — the per-project GitHub pass: dependencies, releases, the
  open-issue phase, and the schedule-gated full phase. In the frequent open
  pass the dependencies/releases/CI phases are gated on their own watermarks
  (`github:deps`, `github:releases`, `github:ci`), each advanced only when the
  phase succeeds; full refreshes ignore the gates. Projects are collected
  concurrently (`collect_project_concurrency`, default 2) with sync
  PyGithub/GraphQL page fetches in worker threads; the first `RateLimitError`
  sets a halt flag so un-started projects skip their pass.
- `launchpad_pass.py` — the per-project Launchpad pass.

## Invariants

- A watermark is written only after the whole pass it covers has succeeded. A
  failed phase leaves the previous watermark in place so the next run refetches
  the gap.
- Each phase commits through the session it was handed. A project that fails
  midway must leave behind exactly the rows its successful phases wrote.
- Per-project failures are isolated: they are logged, recorded on the refresh
  schedule, and collected into `CollectionStats.errors` instead of aborting the
  run.

## Tests

```bash
uv run pytest tests/unit/test_collect_data.py tests/unit/scripts -q
```
