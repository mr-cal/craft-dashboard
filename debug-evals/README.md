# Debug evals

This directory holds hand-curated, small-scale LLM bake-off runs used to debug
*why* a specific model reaches the scores it does, as opposed to the larger
random-sample bake-off runs used to compare models against each other. Unlike
those larger runs (whose artifacts only ever lived in `/tmp` on the production
host or in ad-hoc local backups), the contents here are committed to the repo
so they can be reviewed, diffed, and iterated on over time.

Each subdirectory/run should contain:
- `sample.json` — the exact issue set evaluated (fixed, not randomly sampled;
  shared across v1/v2/v3 runs below since they evaluate the same 5 issues).
- `report_scoring_vN.md` — the scoring bake-off roll-up produced by
  `scripts/llm/bakeoff/scoring_pilot.py` for run N.
- `transcripts_vN/*.json` — one file per (model, issue) pair for run N,
  containing every round's raw model output, **including `reasoning`** (the
  model's thinking/reasoning trace, when the provider returns one), all tool
  calls made, and the tool outputs returned.

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

> **CORRECTION (see the 2026-08-27 v2 entry below): the "genuine
> model-quality finding" conclusion above was wrong.** It was a real harness
> bug after all, as suspected. Kept here unedited for the record, plus the
> correction, so the debugging trail is honest about the mistake rather than
> quietly rewritten.

## 2026-08-27 (v2): root cause found — `response_format` was suppressing tool calls

**Why this run exists:** after presenting the v1 findings above, the user
pushed back: *"I think this is a harness problem... could it be that the LLM
is failing when trying to dispatch something and exits before it got a
chance to finish thinking?"* That was the right call. Two prior gaps made
this invisible: (1) `related_issues` returning `{"results": []}` for 4/5
issues looked suspicious but turned out to be legitimate — embedding
coverage is 100% for every project including the small ones, and the
`exclude_issue_id` self-match filter correctly excludes the issue's own
(highly similar) embedding, which was often the *only* real nearest
neighbor; (2) the harness never captured the API's `finish_reason` or
per-round token/reasoning-token usage, so a model that was cut off
mid-generation was indistinguishable from one that had deliberately
finished. Both gaps are now closed (`craft_dashboard/llm/client.py` +
`scripts/llm/bakeoff/scoring_pilot.py`, commit `bdc1d812`).

**Root cause, confirmed by direct reproduction:** with `finish_reason`
visible, every "all-zero final answer" transcript showed `finish_reason ==
"stop"` (not `"length"`) — ruling out simple token-budget truncation — while
the model's own `reasoning` for that same turn explicitly described wanting
to call more tools (e.g. *"Let me run a few more tools: Read filesets.py...
git_log_path... git_log_search... related_issues... Let me run these in
parallel"* for craft-parts#766). Replaying the exact same conversation state
directly against OpenRouter with and without
`response_format={"type": "json_object"}` reproduced the bug on demand:

- **With** `response_format={"type": "json_object"}` (what the harness sent
  every round): `finish_reason: stop`, `has tool_calls: False`, and a
  nonsensical JSON-shaped stub in `content`.
- **Without** it (same messages, same model, nothing else changed):
  `finish_reason: tool_calls`, `has tool_calls: True`, and the model
  continues investigating exactly as its own reasoning said it would.

So forcing JSON response mode on every round — including rounds where tools
were still being offered with `tool_choice="auto"` — was silently disabling
this model's ability to keep calling tools via OpenRouter. The production
single-shot evaluator (`craft_dashboard/llm/evaluator.py`) never hit this
because it never offers `tools` at all; the conflict is specific to this
tool-calling bake-off harness.

**Fix (commit `f16a13b5`):** stop sending `response_format` whenever `tools`
are also offered. The system prompt already instructs JSON-shaped output,
and the existing production parser (`_parse_evaluation_response`, reused via
`parse_scoring_output`) already tolerates markdown fences, `<think>` blocks,
and surrounding prose, so `response_format` wasn't needed for correct
parsing. Also added a genuine safety net for the failure mode this
investigation could have hidden: if a round returns no tool calls *and*
`finish_reason == "length"` (an actual mid-generation cutoff), the harness
now retries (up to `MAX_TRUNCATION_RETRIES = 2`) instead of silently
accepting truncated content as a real final answer.

**Re-ran the same 5-issue sample after the fix** (`report_scoring_v2.md`,
`transcripts_v2/`): the fix works exactly as hoped — every transcript now
shows the model doing sustained, genuine multi-round investigation (reading
real source files, checking git history, cross-referencing related PRs)
instead of bailing out with a placeholder. But it surfaced a **new, real
calibration problem**: all 5 runs now hit the `MAX_TOOL_ROUNDS = 6` cap (or
the 120k token ceiling) without ever producing a final answer — completion
rate dropped to 0% for a different reason than v1 (never finalizes, instead
of finalizing with garbage). Looking at the reasoning per round (e.g.
craft-parts#766), the model is doing real analysis each round but doesn't
converge — it re-derives the same code path understanding several times
without visibly building toward a stop condition.

**Not yet resolved, needs a decision:** either raise `MAX_TOOL_ROUNDS`
(cost/time will scale accordingly — this run averaged ~$0.042/issue and
~70s/issue at 6 rounds, versus ~$0.006/issue at ~2 rounds in v1), add an
explicit "you have N of M rounds left, finalize now if you have enough
evidence" nudge to the prompt as the round budget runs low, or both. Holding
off on a third re-run until this is decided together, per the user's request
to iterate on the eval process rather than have it done unilaterally.

### 2026-08-27 (v3): round-cap calibration fix

Decision from the user: raise the cap to 8, add an explicit
"round N of M (K remaining)" nudge every round so the model can pace itself
and finalize early once it has enough evidence, and force the model to
answer on the final round instead of letting it request tools it will never
get to use.

**Fix (`scripts/llm/bakeoff/scoring_pilot.py`, commit `6bc1a554`):**
- `MAX_TOOL_ROUNDS` (via `--max-rounds`) default raised from 6 to 8.
- Every round, a `user`-role message is appended stating the current round
  number, the total, and rounds remaining, and encouraging the model to
  finalize early if it already has enough evidence (`tool_choice="auto"`
  already permitted this; the nudge just makes the budget explicit instead
  of implicit).
- On the final round, `tool_choice` is forced to `"none"` (no further tool
  calls will be dispatched after this round regardless of what the model
  asks for) and the nudge instead instructs the model that this is the
  final round and it must return its scores now, based on the evidence
  already gathered.

Added regression tests: `test_final_round_forces_tool_choice_none_and_finalizes`,
`test_non_final_rounds_get_a_rounds_remaining_nudge` (red-green verified).

**Re-ran the same 5-issue sample a fourth time** (`report_scoring_v3.md`,
`transcripts_v3/`): **completion rate rose from 0% to 80% (4/5)**, with
real, evidence-grounded scores and `related_work` citations for
snapcraft#6381, snapcraft (launchpad)#1861614, snapcraft-rocks#111, and
craft-parts#766 — all finalizing within 5-7 rounds (below the 8-round cap),
confirming the early-finalize nudge is working, not just the raised cap.
The one remaining failure, debcraft#41, did *not* hit the round cap either —
it hit the separate `token ceiling exceeded (120000)` guard at round 5 after
several `grep_repo`/`read_file` calls returned unusually large tool output
for that repo. This is a distinct, narrower problem (per-issue token budget
sizing for verbose tool output) from the round-convergence issue this
session set out to fix, and is a reasonable next thing to look at, but is
not blocking review of the 4 real, trustworthy results now available.

Mean cost/time per issue: ~$0.045, ~117s — similar to the v2 (broken
convergence) run's ~$0.042/~70s, but now actually producing final answers
instead of silently failing every time.


