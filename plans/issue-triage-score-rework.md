# Issue & PR Triage Score Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `duplicateness` and `readiness` scores, add a `confidence` score measuring evaluation certainty, move the Action column before scores in the triage table, and clean up the database schema.

**Architecture:** Nine sequential tasks: database migration first, then backend (LLM prompts, validation, models, routes, repository, API), frontend (HTML template, JavaScript), and tests. The migration is the first step because it must run before application code changes take effect.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Jinja2, plain JavaScript, Alembic, PostgreSQL with pgvector, pytest (asyncio + httpx TestClient).

**Database considerations:**
- `llm_evaluations.scores` is a `JSONB` column — score keys are freeform JSON. No schema migration needed for the scores themselves; adding/removing keys is purely application-level.
- The duplicate detection columns (`summary_embedding`, `candidates_compared`, `duplicate_locked_until`, `duplicate_of_issue_id`) are physical columns that must be dropped via migration.
- An HNSW index on `summary_embedding` must be dropped alongside the column.
- A data migration cleans existing JSONB scores of `duplicateness` and `readiness` keys.

**Breaking changes:**
- `GET /api/eval/duplicate-work` and `POST /api/eval/duplicate-result` endpoints are removed.
- `scripts/eval_client.py --detect-duplicates` flag no longer works.
- All exported issue JSONs no longer contain `duplicateness` or `readiness` fields.
- Database migration drops duplicate detection columns and cleans JSONB scores.

---

## Task 0: Database migration — drop duplicate detection columns and clean JSONB scores

**Files:**
- Create: `alembic/versions/0a1b2c3d4e5f_remove_duplicate_detection_columns.py`

### Step 1: Create the Alembic migration file

```python
"""remove_duplicate_detection_columns and clean_scores

Revision ID: 0a1b2c3d4e5f
Revises: a1b2c3d4e5f6
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop duplicate detection columns and clean JSONB scores.

    Phase 1: Remove physical duplicate detection columns from llm_evaluations.
    Phase 2: Clean existing scores JSONB of duplicateness and readiness keys.
    """
    from alembic import op
    import sqlalchemy as sa

    # --- Phase 1: Drop columns and index ---

    # Drop the HNSW index first (columns depend on it)
    op.execute(
        "DROP INDEX IF EXISTS ix_llm_evaluations_embedding"
    )

    # Drop the duplicate detection columns (order does not matter since
    # they are independent)
    op.drop_column("llm_evaluations", "duplicate_of_issue_id")
    op.drop_column("llm_evaluations", "duplicate_locked_until")
    op.drop_column("llm_evaluations", "candidates_compared")
    op.drop_column("llm_evaluations", "summary_embedding")

    # --- Phase 2: Clean JSONB scores ---

    # Remove 'duplicateness' and 'readiness' keys from existing score objects.
    # Use - '{}' syntax to delete specific keys from JSONB.
    # The order of subtraction does not matter since the keys are independent.
    op.execute(
        "UPDATE llm_evaluations "
        "SET scores = scores - 'duplicateness' "
        "WHERE scores ? 'duplicateness'"
    )
    op.execute(
        "UPDATE llm_evaluations "
        "SET scores = scores - 'readiness' "
        "WHERE scores ? 'readiness'"
    )


```

### Step 2: Validate the migration file

Verify the file exists and follows the project's Alembic conventions:
```bash
cd ~/dev/craft/craft-dashboard
ls -la alembic/versions/0a1b2c3d4e5f_remove_duplicate_detection_columns.py
```

Expected: File exists with the correct revision chain (`down_revision = "a1b2c3d4e5f6"`).

### Step 3: Test the migration (offline mode)
```bash
cd ~/dev/craft/craft-dashboard
DATABASE_URL=postgresql://localhost/craft_dashboard_test python -m alembic -c alembic.ini upgrade 0a1b2c3d4e5f
```

Expected: Upgrade completes without errors.
```
### Step 4: Commit

```bash
git add alembic/versions/0a1b2c3d4e5f_remove_duplicate_detection_columns.py
git commit -m "db: remove duplicate detection columns and clean JSONB scores"
```

---

## Task 1: Remove `duplicateness` and `readiness` from the IssueView model and repository

**Files:**
- Modify: `craft_dashboard/models/views.py:30-34`
- Modify: `craft_dashboard/repositories/issue_repository.py:21-27, 324-328`

### Step 1: Remove fields from IssueView

In `craft_dashboard/models/views.py`, delete the two lines that define `duplicateness` and `readiness` on IssueView (lines 31 and 34):

```python
# DELETE these two lines from IssueView dataclass:
#     duplicateness: float | None = None
#     readiness: float | None = None
```

The remaining fields should be:
```python
    staleness: float | None = None
    complexity: float | None = None
    support_request: float | None = None
```

### Step 2: Remove from repository score extraction

In `craft_dashboard/repositories/issue_repository.py`, remove `"duplicateness"` and `"readiness"` from `_SCORE_SORT_FIELDS` (lines 23 and 26):

```python
# Original (lines 21-27):
_SCORE_SORT_FIELDS = {
    "staleness",
    "duplicateness",   # DELETE
    "complexity",
    "support_request",
    "readiness",       # DELETE
}

# After:
_SCORE_SORT_FIELDS = {
    "staleness",
    "complexity",
    "support_request",
}
```

In the same file, remove the `duplicateness` and `readiness` keyword arguments from the `IssueView` constructor call (originally lines 325 and 328):

```python
# DELETE these two lines from IssueView(...) call:
#     duplicateness=scores.get("duplicateness"),
#     readiness=scores.get("readiness"),
```

### Step 3: Run tests to verify

Run:
```bash
cd ~/dev/craft/craft-dashboard
python -m pytest tests/unit/repositories/test_issue_repository.py -v -x
```

Expected: Some tests will fail because they still reference `duplicateness` and `readiness`. That's expected -- those tests are covered in Task 7. The core query logic should still work for the remaining fields.

### Step 4: Commit

```bash
git add craft_dashboard/models/views.py craft_dashboard/repositories/issue_repository.py
git commit -m "refactor: remove duplicateness and readiness fields from IssueView and repository"
```

---

## Task 2: Remove `duplicateness` and `readiness` from routes, sort enum, and templates

**Files:**
- Modify: `craft_dashboard/routes/issues.py:26-35, 155-162`
- Modify: `craft_dashboard/templates/issues/partials/issue_table.html:3-16`

### Step 1: Remove from ALL_SCORES and IssueSort in issues.py

In `craft_dashboard/routes/issues.py`, remove `duplicateness` and `readiness` from `ALL_SCORES` (lines 26-32):

```python
# Original:
ALL_SCORES = {
    "staleness": "Staleness",
    "duplicateness": "Duplicateness",    # DELETE
    "complexity": "Complexity",
    "support_request": "Support Request",
    "readiness": "Readiness",            # DELETE
}

# After:
ALL_SCORES = {
    "staleness": "Staleness",
    "complexity": "Complexity",
    "support_request": "Support Request",
}
```
Remove `readiness` from `INVERTED_SCORES` (line 34):

```python
# Original:
INVERTED_SCORES: frozenset[str] = frozenset({"readiness"})

# After:
INVERTED_SCORES: frozenset[str] = frozenset()
```

Also update `DEFAULT_SCORES` (line 35):

```python
# Original:
DEFAULT_SCORES = "staleness,readiness"

# After:
DEFAULT_SCORES = "staleness"
```

Remove `duplicateness = "duplicateness"` from `IssueSort` enum (line 159):

```python
class IssueSort(StrEnum):
    """Valid sort fields for the issue list."""

    staleness = "staleness"
    duplicateness = "duplicateness"   # DELETE
    complexity = "complexity"
    support_request = "support_request"
    readiness = "readiness"           # DELETE
    age = "age"
    updated = "updated"
    title = "title"
    author = "author"
    number = "number"
```

### Step 2: Update template score_tooltips and score_labels

In `craft_dashboard/templates/issues/partials/issue_table.html`, remove `duplicateness` and `readiness` from both dicts (lines 3-16):

```html
{% set score_tooltips = {
  "staleness": "How stale/inactive this issue is (0=very active, 100=completely dead)",
  "complexity": "How complex this issue is to resolve (0=trivial, 100=extremely complex)",
  "support_request": "How likely this is a support request rather than a bug or feature (0=not support, 100=clearly support)",
} %}
{% set score_labels = {
  "staleness": "Staleness",
  "complexity": "Complexity",
  "support_request": "Support Req",
} %}
```
### Step 2.5: Update pagination fallback in issue_table.html

Line 114 has a pagination URL with a fallback scores value of `"staleness,readiness"`. Change to `"staleness"`:

```html
<!-- Line 114, change: -->
{% set base_url = "/issues/table?...&scores=" ~ (filter_scores or "staleness,readiness") ~ "...%}
<!-- To: -->
{% set base_url = "/issues/table?...&scores=" ~ (filter_scores or "staleness") ~ "...%}
```

### Step 3: Run tests

Run:
```bash
python -m pytest tests/unit/routes/test_issues.py -v -x
```

Expected: The test `test_issues_page_includes_combined_column_visibility_picker` will fail because it asserts `duplicateness` and `readiness` appear in the HTML. Tests are covered in Task 7.

### Step 4: Commit

```bash
git add craft_dashboard/routes/issues.py craft_dashboard/templates/issues/partials/issue_table.html
git commit -m "refactor: remove duplicateness and readiness from routes, enum, and templates"
### Step 4.5: Update other templates with hardcoded readiness references

`issue_table.html` was handled in Step 2 above, but two other templates also reference readiness that need fixing:

**`detail.html:47,89`** — `score_inverted = score_key in ("readiness",)` appears twice in the scores rendering loop. Remove it entirely (no scores need inversion after readiness is gone, or replace with `score_inverted = score_key in ()`):

```html
<!-- Line 47, change: -->
{% set score_inverted = score_key in ("readiness",) %}
<!-- To: -->
{% set score_inverted = False %}

<!-- Line 89, same change in the evaluation history loop -->
```

**`list.html:159,175`** — The default filter scores are hardcoded as `'staleness,readiness'` in two places. Change to `'staleness'`:

```html
<!-- Line 159, change: -->
{% if key in (filter_scores or "staleness,readiness").split(",") %}checked{% endif %}
<!-- To: -->
{% if key in (filter_scores or "staleness").split(",") %}checked{% endif %}

<!-- Line 175, change: -->
value="{{ filter_scores if filter_scores is not none else 'staleness,readiness' }}"
<!-- To: -->
value="{{ filter_scores if filter_scores is not none else 'staleness' }}"
```

**Files:** `craft_dashboard/templates/issues/detail.html`, `craft_dashboard/templates/issues/list.html`
```

---

## Task 3: Remove duplicateness from the eval API and eval client

**Files:**
- Modify: `craft_dashboard/routes/eval_api.py:69-77, 384-443, 495-540`
- Modify: `scripts/eval_client.py:658-734`

### Step 1: Remove DuplicateResultSubmission model

In `craft_dashboard/routes/eval_api.py`, delete the `DuplicateResultSubmission` class (lines 69-77):

```python
# DELETE this entire class:
class DuplicateResultSubmission(BaseModel):
    """Request body for submitting phase-2 duplicate detection results."""

    evaluation_id: int
    duplicateness: float = Field(ge=0.0, le=100.0)
    candidates_compared: int = Field(ge=0)
    duplicate_of_issue_id: int | None = None
    updated_summary: str | None = None
    updated_embedding: list[float] | None = None
```

### Step 2: Remove duplicate_work endpoint

In `craft_dashboard/routes/eval_api.py`, delete the `duplicate_work` function (lines 384-443).

### Step 3: Remove submit_duplicate_result endpoint

In `craft_dashboard/routes/eval_api.py`, delete the `submit_duplicate_result` function (lines 495-540).

### Step 4: Clean up eval_client.py

In `scripts/eval_client.py`, remove the entire `if getattr(args, "detect_duplicates", False):` branch (lines 658-734) which includes:
- The duplicate detector initialization
- The `check_duplicates` call
- The `duplicate_result` dict construction
- The `submit_duplicate_result` POST call

The `--detect-duplicates` CLI flag should also be removed from the argument parser.

### Step 5: Run tests

Run:
```bash
python -m pytest tests/integration/test_eval_api.py -v -x
```

Expected: Tests in `TestDuplicateResultIntegration` and `TestDuplicateWork` will fail because those endpoints no longer exist.

### Step 6: Commit

```bash
git add craft_dashboard/routes/eval_api.py scripts/eval_client.py
git commit -m "feat: remove duplicate detection API endpoints and eval_client --detect-duplicates flag"
```

---

## Task 3.5: Update LLM prompts and validation to remove readiness, add confidence

**Files:**
- Modify: `scripts/llm/validation.py:20-23`
- Modify: `craft_dashboard/llm/prompts.py:83-100`

### Step 1: Update validation required score keys

In `scripts/llm/validation.py`, change `_REQUIRED_SCORE_KEYS` (lines 20-23):

```python
# Original:
_REQUIRED_SCORE_KEYS: Final[dict[str, frozenset[str]]] = {
    "issue": frozenset({"staleness", "complexity", "support_request", "readiness"}),
    "pull_request": frozenset({"staleness", "complexity", "readiness"}),
}

# After:
_REQUIRED_SCORE_KEYS: Final[dict[str, frozenset[str]]] = {
    "issue": frozenset({"staleness", "complexity", "support_request", "confidence"}),
    "pull_request": frozenset({"staleness", "complexity", "confidence"}),
}
```

### Step 2: Update issue extra scores in prompts

In `craft_dashboard/llm/prompts.py`, replace `_ISSUE_EXTRA_SCORES` (lines 83-91):

```python
# Original:
_ISSUE_EXTRA_SCORES = """
For issues, also include:
- "support_request": <0-100, how likely this is a support/help request rather than a bug or feature>
- "readiness": <0-100, how ready is this issue to be worked on. Consider: \
does it have a clear description of the problem or feature request? Are there \
steps to reproduce (for bugs)? Is there enough context and information for a \
developer to start working on it without needing to ask many clarifying \
questions? An issue with no description or vague requirements is not ready.>
"""

# After:
_ISSUE_EXTRA_SCORES = """
For issues, also include:
- "support_request": <0-100, how likely this is a support/help request rather than a bug or feature>
- "confidence": <0-100, how confident the LLM is in its suggested action. High confidence means \
the issue is clearly one of the allowed actions based on the evidence. Low confidence means \
the issue is ambiguous, mixed signals, or would benefit from human review before deciding.>
"""
```

### Step 3: Update PR extra scores in prompts

In `craft_dashboard/llm/prompts.py`, replace `_PR_EXTRA_SCORES` (lines 93-100):

```python
# Original:
_PR_EXTRA_SCORES = """
For pull requests, also include:
- "readiness": <0-100, how ready is this PR for review and merge. Consider: \
does it have a clear description? Are CI checks passing? Are there unresolved \
or unanswered review comments? Is the diff in reviewable shape (not WIP, not \
too large without explanation)? A PR with failing CI, unresolved comments, or \
no description is not ready.>
"""

# After:
_PR_EXTRA_SCORES = """
For pull requests, also include:
- "confidence": <0-100, how confident the LLM is in its suggested action. High confidence means \
the PR is clearly ready for review or ready to merge based on CI status and review state. \
Low confidence means mixed signals -- e.g. passing CI but no reviewer assigned, or \
review approved but CI is still running.>
"""
```

### Step 4: Run validation tests

Run:
```bash
cd ~/dev/craft/craft-dashboard
python -c "
from scripts.llm.validation import validate_evaluation_result, _REQUIRED_SCORE_KEYS
print('Required keys:', _REQUIRED_SCORE_KEYS)

# Test issue with new scores
result = {
    'summary': 'This is a test summary that meets minimum length requirements for validation purposes.',
    'scores': {'staleness': 50, 'complexity': 30, 'support_request': 20, 'confidence': 75},
    'suggested_action': 'needs_review',
    'suggested_action_reason': 'Has labels and maintainer comment.',
}
validate_evaluation_result(result, issue_type='issue')
print('Issue validation: PASS')

# Test PR with new scores
result = {
    'summary': 'This is a test PR summary that meets minimum length requirements for validation purposes.',
    'scores': {'staleness': 10, 'complexity': 40, 'confidence': 85},
    'suggested_action': 'needs_review',
    'suggested_action_reason': 'CI passing, ready for maintainer review.',
}
validate_evaluation_result(result, issue_type='pull_request')
print('PR validation: PASS')

# Test readiness still fails
result_bad = {
    'summary': 'This is a test summary that meets minimum length requirements for validation purposes.',
    'scores': {'staleness': 50, 'complexity': 30, 'support_request': 20, 'readiness': 80},
    'suggested_action': 'needs_review',
    'suggested_action_reason': 'Test.',
}
try:
    validate_evaluation_result(result_bad, issue_type='issue')
    print('readiness-only validation: FAIL (should have rejected)')
except Exception as e:
    print(f'readiness-only validation: PASS (rejected: {e})')
"
```

Expected output:
```
Required keys: {'issue': frozenset({'staleness', 'complexity', 'support_request', 'confidence'}), 'pull_request': frozenset({'staleness', 'complexity', 'confidence'})}
Issue validation: PASS
PR validation: PASS
readiness-only validation: PASS (rejected: scores missing required keys: confidence)
```
### Step 4.5: Update test_prompts.py

In `tests/unit/llm/test_prompts.py`, the `test_pr_specific_scores` test asserts `"readiness" in system_msg` (line 181). Since the prompt now uses `confidence` instead of `readiness`, update the assertion:

```python
# Line 181, change:
assert "readiness" in system_msg.lower()
# To:
assert "confidence" in system_msg.lower()
```

The `test_issue_specific_scores` test (line 198) asserts `"support_request"` which is still present, so it does not need updating.

### Step 5: Commit

```bash
git add scripts/llm/validation.py craft_dashboard/llm/prompts.py
git commit -m "feat: replace readiness with confidence in LLM prompts and validation"
```

---

## Task 4: Add `confidence` score to model, routes, and templates

**Files:**
- Modify: `craft_dashboard/models/views.py` (after `support_request` line)
- Modify: `craft_dashboard/repositories/issue_repository.py` (IssueView constructor)
- Modify: `craft_dashboard/routes/issues.py` (ALL_SCORES, IssueSort)
- Modify: `craft_dashboard/templates/issues/partials/issue_table.html` (score_tooltips, score_labels)
- Modify: `craft_dashboard/static/js/issue-columns.js` (scoreColumns)

### Step 1: Add confidence field to IssueView

In `craft_dashboard/models/views.py`, add after the `support_request` line:

```python
    support_request: float | None = None
    confidence: float | None = None
```

### Step 2: Add confidence to repository

In `craft_dashboard/repositories/issue_repository.py`, add to `_SCORE_SORT_FIELDS`:

```python
_SCORE_SORT_FIELDS = {
    "staleness",
    "complexity",
    "support_request",
    "confidence",
}
```

Add to the `IssueView` constructor call:

```python
                    support_request=scores.get("support_request"),
                    confidence=scores.get("confidence"),
```

### Step 3: Add confidence to ALL_SCORES and IssueSort

In `craft_dashboard/routes/issues.py`:

```python
ALL_SCORES = {
    "staleness": "Staleness",
    "complexity": "Complexity",
    "support_request": "Support Request",
    "confidence": "Confidence",
}
```

In `IssueSort`:

```python
    support_request = "support_request"
    confidence = "confidence"
    age = "age"
```

### Step 4: Add confidence to template tooltips and labels

In `craft_dashboard/templates/issues/partials/issue_table.html`, add to both dicts:

```html
{% set score_tooltips = {
  "staleness": "How stale/inactive this issue is (0=very active, 100=completely dead)",
  "complexity": "How complex this issue is to resolve (0=trivial, 100=extremely complex)",
  "support_request": "How likely this is a support request rather than a bug or feature (0=not support, 100=clearly support)",
  "confidence": "How confident the evaluation is in its suggested action (0=uncertain, 100=highly certain)",
} %}
{% set score_labels = {
  "staleness": "Staleness",
  "complexity": "Complexity",
  "support_request": "Support Req",
  "confidence": "Confidence",
} %}
```

### Step 5: Add confidence to JavaScript column list

In `craft_dashboard/static/js/issue-columns.js`, add `"confidence"` to `scoreColumns` and update `defaultColumns` filter to only show `staleness` by default (confidence is NOT in defaults):

```javascript
const scoreColumns = [
  "staleness",
  "complexity",
  "support_request",
  "confidence",
];
const defaultColumns = [
  "issue",
  "title",
  "author",
  "age",
  ...scoreColumns.filter((column) => ["staleness"].includes(column)),
  "action",
  "summary",
];
```

### Step 6: Run tests

Run:
```bash
python -m pytest tests/unit/repositories/test_issue_repository.py tests/unit/routes/test_issues.py -v -x
```

Expected: Tests should pass -- new field is optional (None by default), so existing test data without confidence still works.

### Step 7: Commit

```bash
git add craft_dashboard/models/views.py craft_dashboard/repositories/issue_repository.py craft_dashboard/routes/issues.py craft_dashboard/templates/issues/partials/issue_table.html craft_dashboard/static/js/issue-columns.js
git commit -m "feat: add confidence score to model, routes, templates, and JS columns"
```

---

## Task 5: Reorder the table -- Action before scores

**Files:**
- Modify: `craft_dashboard/templates/issues/partials/issue_table.html`

### Step 1: Reorder colgroup

In the `<colgroup>`, move the `action` col before the scores loop:

```html
<!-- Original -->
<colgroup>
  <col class="col-issue" data-col="issue">
  <col class="col-title" data-col="title">
  <col class="col-author" data-col="author">
  <col class="col-age" data-col="age">
  {% for score_key in active_scores %}<col class="col-score" data-col="{{ score_key }}">{% endfor %}
  <col class="col-action" data-col="action">
  <col class="col-summary" data-col="summary">
</colgroup>

<!-- After -->
<colgroup>
  <col class="col-issue" data-col="issue">
  <col class="col-title" data-col="title">
  <col class="col-author" data-col="author">
  <col class="col-age" data-col="age">
  <col class="col-action" data-col="action">
  {% for score_key in active_scores %}<col class="col-score" data-col="{{ score_key }}">{% endfor %}
  <col class="col-summary" data-col="summary">
</colgroup>
```

### Step 2: Reorder header rows

Move the `<th data-col="action">Action</th>` before the scores loop in `<thead>`:

```html
<!-- Original -->
{{ sort_header("Age", "age", column_name="age") }}
{% for score_key in active_scores %}
{{ sort_header(score_labels.get(score_key, score_key), score_key, score_tooltips.get(score_key, ""), column_name=score_key) }}
{% endfor %}
<th data-col="action">Action</th>
<th data-col="summary">Summary</th>

<!-- After -->
{{ sort_header("Age", "age", column_name="age") }}
<th data-col="action">Action</th>
{% for score_key in active_scores %}
{{ sort_header(score_labels.get(score_key, score_key), score_key, score_tooltips.get(score_key, ""), column_name=score_key) }}
{% endfor %}
<th data-col="summary">Summary</th>
```

### Step 3: Reorder body rows

Move the action `<td>` before the scores loop in `<tbody>`. The action cell content stays identical -- only its position changes:

```html
<!-- Original order in tbody: age cell -> scores loop -> action cell -> summary cell -->
<!-- After order: age cell -> action cell -> scores loop -> summary cell -->
```

The action cell HTML stays the same:
```html
<td data-col="action">
  {% if issue.state in ("closed", "merged") %}
  <span style="color:var(--app-text-muted);">-</span>
  {% elif issue.suggested_action %}
  <span class="action-badge" aria-label="Suggested action: {{ issue.suggested_action|replace('_', ' ') }}">{{ issue.suggested_action|replace("_", " ") }}</span>
  {% endif %}
</td>
```

### Step 4: Verify the page renders

Start the app and visit `/issues`:
```bash
cd ~/dev/craft/craft-dashboard
uv run python -m craft_dashboard.app
# Then open http://localhost:8000/issues in a browser
```

Verify: Action column appears before Staleness/Complexity/Support Req/Confidence columns.

### Step 5: Commit

```bash
git add craft_dashboard/templates/issues/partials/issue_table.html
git commit -m "refactor: move Action column before scores in triage table"
```

---

## Task 6: Add confidence score to test seed data

**Files:**
- Modify: `tests/unit/repositories/test_issue_repository.py`

### Step 1: Add confidence to test evaluations

In `tests/unit/repositories/test_issue_repository.py`, in the `_seed_issues_with_scores` function, add `"confidence"` to every `scores` dict in the `make_evaluation` calls. For example, the first evaluation (issue_stale, line 265) originally has:

```python
scores={
    "staleness": 0.95,
    "duplicateness": 0.1,
    "complexity": 0.3,
    "support_request": 0.2,
    "readiness": 0.2,
},
```

Replace with:

```python
scores={
    "staleness": 0.95,
    "complexity": 0.3,
    "support_request": 0.2,
    "confidence": 70.0,
},
```

Do the same for the other two evaluations (issue_ready and issue_complex), removing `duplicateness` and `readiness` keys and adding `confidence`.

### Step 2: Update test assertions

In `test_query_returns_all_score_fields`, update the assertions:

```python
# DELETE these:
assert "duplicateness" in scored_issue
assert "readiness" in scored_issue
assert scored_issue.duplicateness == 0.1
assert scored_issue.readiness == 0.2

# ADD these:
assert "confidence" in scored_issue
assert scored_issue.confidence == 70.0
```

In `test_query_handles_missing_scores`, update:

```python
# DELETE:
assert unscored_issue.duplicateness is None
assert unscored_issue.readiness is None

# ADD:
assert unscored_issue.confidence is None
```

### Step 3: Delete duplicateness sort test

Delete the entire `test_sort_by_duplicateness_score` method (lines 983-991).

### Step 4: Run tests

Run:
```bash
python -m pytest tests/unit/repositories/test_issue_repository.py::TestQueryIssuesLLMScores -v
```

Expected: All LLM score tests pass.

### Step 5: Commit

```bash
git add tests/unit/repositories/test_issue_repository.py
git commit -m "test: update seed data to use confidence instead of readiness/duplicateness"
```

---

## Task 7: Update remaining tests for removed and added scores

**Files:**
 - Modify: `tests/end_to_end/test_triage.py`
 - Modify: `tests/end_to_end/seed_data.py`
 - Modify: `tests/integration/test_eval_api.py`
 - Modify: `tests/unit/routes/test_issues.py`
 - Modify: `tests/unit/routes/test_issues_export.py`

### Step 1: Update test_issues.py

In `test_issues_page_includes_combined_column_visibility_picker`, remove assertions for duplicateness and readiness:

```python
# DELETE:
assert 'value="duplicateness"' in response.text
assert 'value="readiness"' in response.text
```
### Step 1.5: Update test_triage.py (end-to-end)

In `tests/end_to_end/test_triage.py`, update `test_triage_default_score_columns` (lines 161-184). The Puppeteer script asserts `has_readiness` which will break after readiness is removed. Replace the test to check for staleness and confidence instead:

```python
def test_triage_default_score_columns(self, seeded_url: str) -> None:
    """The triage page should show default score columns: Staleness (confidence is hidden by default)."""
    script = make_script("""\
await page.goto(`${BASE}/issues`, {waitUntil: 'networkidle0', timeout: 30000});
await new Promise(r => setTimeout(r, 2000));

const headers = await page.evaluate(() => {
  const ths = document.querySelectorAll('table thead th');
  return Array.from(ths).map(th => th.textContent.trim());
});

console.log(JSON.stringify({
  headers: headers,
  has_staleness: headers.some(h => h.toLowerCase().includes('staleness')),
  has_confidence: headers.some(h => h.toLowerCase().includes('confidence')),
}));
""")
    result = run_puppeteer(script, base_url=seeded_url, timeout=20)
    assert result["has_staleness"], (
        f"Expected 'Staleness' column, got headers: {result['headers']}"
    )
    # Confidence is NOT in default columns (it's opt-in via column picker)
    assert not result["has_confidence"], (
        f"Confidence should not be a default column, got headers: {result['headers']}"
    )
```
### Step 1.6: Update test_dashboard_api.py

In `tests/integration/test_dashboard_api.py`, the `test_issues_table_with_all_filter_params` test passes `scores: "staleness,readiness"` in its filter params (line 214). Update to `"staleness"` since readiness no longer exists:

```python
# Line 214, change:
"scores": "staleness,readiness",
# To:
"scores": "staleness",
```

### Step 2: Update test_issues_export.py

In `tests/unit/routes/test_issues_export.py`, remove `duplicateness=None` and `readiness=18.0` from the `IssueView` constructor and from the expected JSON output:

```python
# DELETE from IssueView constructor:
duplicateness=None,
readiness=18.0,

# DELETE from expected JSON response:
"duplicateness": None,
"readiness": 18.0,
```

### Step 3: Update test_eval_api.py

In `tests/integration/test_eval_api.py`:
- Delete the entire `TestDuplicateWork` class (tests for `GET /api/eval/duplicate-work`)
- Delete the entire `TestDuplicateResultIntegration` class (tests for `POST /api/eval/duplicate-result`)
- In `_seed_entities` test helper, remove `duplicateness` from scores dict
- In `test_submit_duplicate_result_stores_no_duplicate`, remove `"duplicateness": 0.0` from the request body

### Step 4: Update seed_data.py (end-to-end tests)

In `tests/end_to_end/seed_data.py`, in `_make_llm_evaluations`:
- Remove `"duplicateness": duplicateness` from the scores dict
- Remove the `duplicateness = min(100, j * 12)` variable
- Remove `"readiness"` from PR scores: `scores["readiness"] = min(100, 30 + j * 15)`
- Optionally add `"confidence"` to seed scores (e.g., `scores["confidence"] = 60 + j * 5`)

### Step 5: Run all tests

Run:
```bash
python -m pytest tests/unit/repositories/test_issue_repository.py tests/unit/routes/test_issues.py tests/unit/routes/test_issues_export.py tests/integration/test_eval_api.py tests/end_to_end/ -v --tb=short
```

Expected: All tests pass.

### Step 6: Commit

```bash
git add tests/unit/routes/test_issues.py tests/unit/routes/test_issues_export.py tests/integration/test_eval_api.py tests/end_to_end/seed_data.py
git commit -m "test: update all tests for removed duplicateness/readiness and added confidence"
```

---

## Task 8: Final verification and lint

**Files:** All modified files

### Step 1: Run the full test suite

Run:
```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass.

### Step 2: Run type checker (if configured)

Run:
```bash
python -m mypy craft_dashboard/ scripts/ --no-error-summary 2>&1 | head -40
```

Expected: No new errors from changed files.

### Step 3: Run linter

Run:
```bash
python -m ruff check craft_dashboard/routes/issues.py craft_dashboard/models/views.py craft_dashboard/repositories/issue_repository.py craft_dashboard/routes/eval_api.py scripts/eval_client.py scripts/llm/validation.py craft_dashboard/llm/prompts.py
```

Expected: No lint violations.

### Step 4: Final commit

```bash
git add -A
git commit -m "chore: lint, typecheck, and verify all changes for score rework"
```

---

## Verification checklist

After all tasks complete, verify:

1. **`/issues` page** -- Action column appears before scores. No duplicateness or readiness columns. Confidence column is hidden by default but available in column picker.
2. **`/issues/export`** -- JSON no longer contains `duplicateness` or `readiness`. Contains `confidence` (null if not set).
3. **Sort dropdown** -- No duplicateness or readiness sort options. Confidence sort works.
4. **`/api/eval/duplicate-work`** -- Returns 404 (endpoint removed).
5. **`/api/eval/duplicate-result`** -- Returns 404 (endpoint removed).
6. **`scripts/eval_client.py --help`** -- No `--detect-duplicates` flag.
7. **`scripts/llm/validation.py`** -- `readiness` is no longer a required score; `confidence` is required instead.
8. **LLM prompt** -- Evaluation prompt no longer asks for `readiness` or `duplicateness`; asks for `confidence` instead.
9. **Database** -- `alembic current` shows `0a1b2c3d4e5f`. No `summary_embedding`, `candidates_compared`, `duplicate_locked_until`, or `duplicate_of_issue_id` columns in `llm_evaluations`. Existing JSONB scores have no `duplicateness` or `readiness` keys.
10. **All tests pass** -- `pytest tests/ -v`.
