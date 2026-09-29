# services/

Business logic that spans repositories: dashboard metrics, admin operations,
stats aggregation, evaluation queue state.

## Contract

- May import: `repositories`, `models`, `config`, `settings`.
- Must not import: `routes`. Shared HTTP infrastructure that a service needs
  lives in `craft_dashboard/rate_limit.py` or here, never in a route module.
  Enforced by import-linter.

## Invariants

- Services own the transaction boundary; repositories never commit.
- Date-window logic reads "now" from a single place so tests are not
  timing-dependent.
- Operational defaults belong in `settings.py`, not redefined per service. The
  UI, the worker, the CLI, and cron must agree on what "stale" means.

## Tests

```bash
uv run pytest tests/unit/services -q
```
