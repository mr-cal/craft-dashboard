# services/

Business logic that spans repositories: dashboard metrics, admin operations,
stats aggregation, evaluation queue state.

Homepage and triage metrics live in `dashboard/`, one module per metric group
(`velocity`, `throughput`, `volume`, `untriaged`, `releases`, `spotlights`,
`health`), with `dashboard_service.py` orchestrating them.

`dashboard/view_models.py` turns the homepage metric payload into the frozen
dataclasses in `models/views.py` (`DashboardView` and friends). Every derived
string the dashboard shows — delta labels, tooltips, badge CSS classes,
formatted ages — is computed there so the template only iterates and prints.
Badge *classification* stays in `dashboard/badges.py`; the view models only map
a colour word onto its CSS class.

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
