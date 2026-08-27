# Debug evals

This directory holds hand-curated, small-scale LLM bake-off runs used to debug
*why* a specific model reaches the scores it does, as opposed to the larger
random-sample bake-off runs used to compare models against each other. Unlike
those larger runs (whose artifacts only ever lived in `/tmp` on the production
host or in ad-hoc local backups), the contents here are committed to the repo
so they can be reviewed, diffed, and iterated on over time.

Each subdirectory/run should contain:
- `sample.json` — the exact issue set evaluated (fixed, not randomly sampled).
- `report_scoring.md` — the scoring bake-off roll-up produced by
  `scripts/llm/bakeoff/scoring_pilot.py`.
- `transcripts/*.json` — one file per (model, issue) pair, containing every
  round's raw model output, **including `reasoning`** (the model's
  thinking/reasoning trace, when the provider returns one), all tool calls
  made, and the tool outputs returned.

## 2026-08-27: qwen/qwen3.8-27b on 5 hand-picked issues

**Why this run exists:** after reviewing the earlier v3/v4 random-sample
bake-off transcripts, it was noted that no model reasoning trace was visible
in the logs, which made it impossible to tell *why* a model produced a given
score. That was a real bug in `craft_dashboard/llm/client.py`:
`LLMResponse.from_api_response()` never read the `message.reasoning` field
that OpenRouter returns by default for supporting models, and the OpenRouter
request payload never explicitly opted into reasoning tokens either. Both
were fixed (see `craft_dashboard/llm/client.py` and
`scripts/llm/bakeoff/scoring_pilot.py`, commit `03cda227`) before this run,
so this is the first bake-off run with real reasoning traces attached.

**Sample** (`sample.json`): a fixed set of 5 issues chosen by the user, not a
random draw:
- `snapcraft#6381`
- `debcraft#41`
- `snapcraft (launchpad)#1861614`
- `snapcraft-rocks#111`
- `craft-parts#766`

**Model:** `qwen/qwen3.8-27b` only (the model the user selected for
production use), via OpenRouter.

**Result:** all 5 runs completed without harness errors or tool crashes
(100% completion rate, 2 rounds each, ~$0.006 average cost per issue). But
every single run's *final* score is all zeros
(`impact=0, staleness=0, complexity=0, support_request=0, confidence=0,
related_work=[]`) — despite the model doing real, on-topic tool-based
investigation in round 1 (issue lookups, related-issue search, git log
search, repo grepping).

**What the reasoning traces reveal:** in every transcript, the model's round-2
`reasoning` text describes wanting to *keep investigating* (e.g. "Let me
check the craft-parts project... Let me search for 'txz' and source type
detection", "Let me check the repository contents", "Let me check the
current state of step_handler.py") — but its round-2 `content` (the field
actually parsed as the final answer) is the placeholder all-zero JSON object,
not a real scored answer. In other words: **the model's stated intent and its
structured output are contradictory.** It behaves as though it ran out of
budget/patience to keep calling tools and emitted a JSON-shaped stub to
satisfy `response_format={"type": "json_object"}`, rather than either
continuing to investigate or committing to real scores based on what it had
already gathered.

This is a genuine model-quality finding, not a harness bug — the harness
correctly captured what the model said and did. It suggests qwen/qwen3.8-27b,
at least under the current 2-round-typical stopping pattern and prompt, is
prone to abandoning a real scoring attempt in favor of a shaped-but-empty
response. Candidates worth trying next (not yet done): forcing the model to
justify its scores in the same JSON turn it emits them, lowering
`tool_choice` friction so it doesn't feel implicitly done after one round of
tools, or explicitly disallowing all-zero score submissions unless the model
states low confidence in prose first.

**Known non-effect on this run:** a `craft_consumers` config change was also
shipped in the same commit as the reasoning fix (making `snapcraft-rocks`
pin SHAs for all `craft-applications` and `craft-libraries` when evaluated
through the real production `/api/eval/next` endpoint, since it consumes both
snapcraft and rockcraft). That change **has no effect on this bake-off run**:
`scripts/llm/bakeoff/scoring_pilot.py` builds its own local tool context from
the full `craft-projects` allowlist regardless of app/library/consumer
distinctions — that restriction is only enforced by the live `/next` HTTP
endpoint, which this script never calls.
