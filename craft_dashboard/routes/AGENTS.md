# routes/

FastAPI handlers, roughly one module per page. The thinnest layer: parse the
request, call a service, render a template.

## Contract

- May import: `services`, `repositories`, `models`, `rate_limit`, `templates`.
- Must not import another route module. Shared infrastructure (the rate
  limiter, worker liveness state) lives in `craft_dashboard/rate_limit.py` and
  `services/eval_activity.py`. Enforced by import-linter.

## Invariants

- **Never emit untrusted data into a `<script>` with `| safe`.** Use
  `| tojson`, which escapes `<`, `>`, `&`, and `'`. Issue titles and forum
  names come from anyone who can file an issue.
- Pass raw objects to templates and let `| tojson` serialize; do not
  `json.dumps` in the route.
- Concurrent HTMX requests to the same target need a shared `hx-sync`, or a
  slow early response overwrites a fast later one.

## Tests

```bash
uv run pytest tests/unit/routes tests/integration -q
```
