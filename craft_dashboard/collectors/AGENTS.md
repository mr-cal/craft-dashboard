# collectors/

Fetches issues, pull requests, releases, and forum activity from GitHub,
Launchpad, and Discourse into the database. Runs from cron, never from a
request.

## Contract

- Public API: `collect_*` coroutines called by `scripts/collect_data.py`.
- May import: `models`, `repositories`, `http_retry`, `config`, `settings`.
- Must not import: `routes`, `services`, `templates`. Enforced by import-linter.

## Invariants

- **A partial run must not advance a watermark.** Watermarks live in
  `collection_watermarks` and are written only on full success, with an
  overlap window subtracted on read. Deriving a watermark from already-stored
  rows loses data silently and permanently.
- **Paginate every connection.** A GraphQL `first: N` without `pageInfo` and a
  cursor silently truncates. Repos exceed 100 tags.
- **Record activity only on real change.** Compare the content hash before
  writing an `IssueActivity` row, or an over-fetch becomes a fake activity
  spike.
- **Never mark work permanently done on a heuristic.** Forum categories are
  persisted as `done`; requiring a streak of confirmations and revalidating
  periodically is what makes a wrong guess recoverable.
- All outbound HTTP retry behaviour comes from `craft_dashboard.http_retry`.

## Tests

```bash
uv run pytest tests/unit/collectors -q
```
