# Remove `close_outdated` Action

**Goal:** Remove `close_outdated` from the suggested action list everywhere — LLM prompts, validation, templates, seed data, and existing database records. Migrate existing `close_outdated` evaluations to `close_stale`.

**Architecture:** Six sequential tasks: database migration first, then backend (validation, prompts), frontend (template), test data, and verification. The migration is first because application code changes depend on it.

---

## Task 0: Database migration — migrate `close_outdated` → `close_stale`

**Files:**
- Create: `alembic/versions/<hash>_migrate_close_outdated_to_stale.py`

### Step 1: Create the Alembic migration file

```python
"""migrate_close_outdated_to_stale

Revision ID: <hash>
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "<hash>"
down_revision: str | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Migrate close_outdated suggested_action to close_stale."""
    from alembic import op

    op.execute(
        "UPDATE llm_evaluations "
        "SET suggested_action = 'close_stale' "
        "WHERE suggested_action = 'close_outdated'"
    )


def downgrade() -> None:
    """Revert: close_stale back to close_outdated (lossy)."""
    from alembic import op

    # Only revert rows that were originally close_outdated.
    # Since we can't distinguish them post-migration,
    # downgrade is marked lossy and does nothing safe.
    pass
```

### Step 2: Validate

Verify the file exists with correct revision chain (`down_revision = "0a1b2c3d4e5f"`).

### Step 3: Commit

```bash
git add alembic/versions/<hash>_migrate_close_outdated_to_stale.py
git commit -m "db: migrate close_outdated suggested_action to close_stale"
```

---

## Task 1: Remove `close_outdated` from validation

**Files:**
- Modify: `scripts/llm/validation.py`

### Step 1: Remove from ALLOWED_ACTIONS

In `scripts/llm/validation.py`, remove `"close_outdated"` from the `ALLOWED_ACTIONS` frozenset (line 14):

```python
# Original (lines 10-19):
ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close_stale",
        "close_not_a_bug",
        "close_outdated",   # DELETE
        "needs_triage",
        "needs_review",
        "keep_open",
    }
)

# After:
ALLOWED_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "close_stale",
        "close_not_a_bug",
        "needs_triage",
        "needs_review",
        "keep_open",
    }
)
```

### Step 2: Commit

```bash
git add scripts/llm/validation.py
git commit -m "refactor: remove close_outdated from validation ALLOWED_ACTIONS"
```

---

## Task 2: Remove `close_outdated` from LLM prompts

**Files:**
- Modify: `craft_dashboard/llm/prompts.py`

### Step 1: Remove from _EVALUATION_SYSTEM

In `craft_dashboard/llm/prompts.py`, remove `close_outdated` from the `suggested_action` enum in `_EVALUATION_SYSTEM` (line 34-35):

```python
# Original (lines 34-35):
  "suggested_action": "<one of: close_stale, close_not_a_bug, \
close_outdated, needs_triage, needs_review, keep_open>",

# After:
  "suggested_action": "<one of: close_stale, close_not_a_bug, \
needs_triage, needs_review, keep_open>",
```

### Step 2: Commit

```bash
git add craft_dashboard/llm/prompts.py
git commit -m "refactor: remove close_outdated from LLM evaluation prompt"
```

---

## Task 3: Remove `close_outdated` from template action filter

**Files:**
- Modify: `craft_dashboard/templates/issues/list.html`

### Step 1: Remove from action filter dropdown

In `craft_dashboard/templates/issues/list.html`, remove `"close_outdated"` from the action filter list (line 124):

```html
<!-- Original (line 124): -->
{% for act in ["close_stale", "close_duplicate", "close_not_a_bug", "close_outdated", "needs_triage", "needs_review", "keep_open"] %}

<!-- After: -->
{% for act in ["close_stale", "close_duplicate", "close_not_a_bug", "needs_triage", "needs_review", "keep_open"] %}
```

### Step 2: Commit

```bash
git add craft_dashboard/templates/issues/list.html
git commit -m "refactor: remove close_outdated from action filter dropdown"
```

---

## Task 4: Remove `close_outdated` from e2e seed data

**Files:**
- Modify: `tests/end_to_end/seed_data.py`

### Step 1: Remove from _ACTIONS and _ACTION_REASONS

In `tests/end_to_end/seed_data.py`, remove `"close_outdated"` from `_ACTIONS` (line 259) and its corresponding reason from `_ACTION_REASONS` (line 270):

```python
# Original _ACTIONS (lines 253-261):
_ACTIONS = [
    "close_stale",
    "needs_triage",
    "keep_open",
    "needs_review",
    "close_duplicate",
    "close_outdated",       # DELETE
    "close_not_a_bug",
]

# After:
_ACTIONS = [
    "close_stale",
    "needs_triage",
    "keep_open",
    "needs_review",
    "close_duplicate",
    "close_not_a_bug",
]

# Original _ACTION_REASONS (lines 263-272):
_ACTION_REASONS = [
    "No activity for over 6 months, likely abandoned.",
    "New issue needs initial assessment by a maintainer.",
    "Active discussion and recent commits, keep monitoring.",
    "PR has approvals but CI is failing, needs author attention.",
    "Very similar to issue #42, likely a duplicate report.",
    "PR has merge conflicts that need resolving.",       # DELETE (was for close_outdated)
    "References an API that was removed in v3.0.",
    "This is expected behavior, not a bug.",
]

# After:
_ACTION_REASONS = [
    "No activity for over 6 months, likely abandoned.",
    "New issue needs initial assessment by a maintainer.",
    "Active discussion and recent commits, keep monitoring.",
    "PR has approvals but CI is failing, needs author attention.",
    "Very similar to issue #42, likely a duplicate report.",
    "References an API that was removed in v3.0.",
    "This is expected behavior, not a bug.",
]
```

### Step 2: Commit

```bash
git add tests/end_to_end/seed_data.py
git commit -m "test: remove close_outdated from e2e seed data"
```

---

## Task 5: Update e2e tests referencing close_outdated

**Files:**
- Check: `tests/end_to_end/test_triage.py` (any remaining references)

### Step 1: Verify no remaining references

Run:
```bash
grep -rn "close_outdated" tests/end_to_end/
```

Expected: No matches. If any exist, remove them.

### Step 2: Commit

```bash
git commit -am "test: update e2e tests for close_outdated removal"
```

---

## Task 6: Verification

### Step 1: Run unit tests

```bash
cd ~/dev/craft/craft-dashboard
python -m pytest tests/unit/ -v -x
```

Expected: All pass.

### Step 2: Run e2e tests

```bash
cd ~/dev/craft/craft-dashboard
CRAFT_DASHBOARD_E2E=1 uv run pytest tests/end_to_end/ -v --timeout=300
```

Expected: All pass (rebuilds Docker stack with new seed data).

### Step 3: Verify no remaining references

```bash
grep -rn "close_outdated" craft_dashboard/ scripts/ tests/ plans/ --include="*.py" --include="*.html" --include="*.js"
```

Expected: Only matches in `plans/` (historical docs). No matches in application code or tests.

### Step 4: Final commit

```bash
git commit -am "verify: close_outdated removal"
```

---

## Breaking changes

- `close_outdated` is no longer a valid suggested action
- Existing database records with `suggested_action = 'close_outdated'` are migrated to `close_stale`
- LLM evaluations will no longer produce `close_outdated` actions
- Exported issue JSONs no longer contain `close_outdated` as a suggested action
- Action filter dropdown no longer includes `close_outdated`
