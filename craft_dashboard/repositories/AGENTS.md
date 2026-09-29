# repositories/

All SQL lives here. Nothing above this layer builds queries.

## Contract

- Public API: one `*Repository` class per aggregate, constructed with an
  `AsyncSession`.
- May import: `craft_dashboard.models`, `craft_dashboard.enums`, SQLAlchemy.
- Must not import: `routes`, `services`, `llm`, `collectors`. Enforced by the
  import-linter contracts in `pyproject.toml`.

## Invariants

- Never commit. The caller owns the transaction boundary.
- Dialect-specific SQL must branch on `self.session.bind.dialect.name`. Tests
  run on SQLite, production runs on PostgreSQL; they differ on regex, `GLOB`,
  and JSON operators.
- Every query that can return the full table needs a `LIMIT`. A user-supplied
  page size of 0 means "the server's maximum", not "unbounded".
- Exclusion and filter semantics are duplicated across several modules. Prefer
  extending a shared helper over adding a seventh copy.

## Tests

```bash
uv run pytest tests/unit/repositories -q
```
